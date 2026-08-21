"""Fail-closed tests for the PyInstaller missing-module warning audit."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts import audit_pyinstaller_warnings as warning_audit

_ROOT = Path(__file__).resolve().parents[2]
_MAKEFILE = _ROOT / "Makefile"
_SCRIPT = _ROOT / "scripts" / "audit_pyinstaller_warnings.py"
_LINUX_POLICY = _ROOT / "config" / "pyinstaller-warning-allowlist-linux.json"
_CONNECTOR_REGISTRY = _ROOT / "src" / "general_ludd" / "connectors" / "registry.py"
_PRICING_SOURCES = _ROOT / "src" / "general_ludd" / "pricing_intel" / "sources.py"
_PYINSTALLER_VERSION = "6.20.0"
_EMPTY_TRANSITIVE_DIGEST = hashlib.sha256(b"").hexdigest()
_CONTROLLER_RUNTIME_EDGES = {
    ("ansible.executor", "general_ludd.ansible.core_runner", ("delayed",)),
    ("ansible.inventory", "general_ludd.ansible.core_runner", ("delayed",)),
    ("ansible.module_utils", "general_ludd.ansible.core_runner", ("delayed",)),
    ("ansible.parsing", "general_ludd.ansible.core_runner", ("optional",)),
    ("ansible.plugins", "general_ludd.ansible.core_runner", ("delayed", "optional")),
    ("ansible.template", "general_ludd.ansible.core_runner", ("optional",)),
    ("ansible.utils", "general_ludd.ansible.core_runner", ("delayed",)),
    ("ansible.utils", "general_ludd.ansible.unsafe", ("optional",)),
    ("ansible.vars", "general_ludd.ansible.core_runner", ("delayed",)),
}

_WARNING_HEADER = """\
This file lists modules PyInstaller was not able to find. This does not
necessarily mean these modules are required for running your program. Both
Python's standard library and 3rd-party Python packages often conditionally
import optional modules, some of which may be available only on certain
platforms.

Types of import:
* top-level: imported at the top-level - look at these first
* conditional: imported within an if-statement
* delayed: imported within a function
* optional: imported within a try-except-statement

IMPORTANT: Do NOT post this list to the issue-tracker. Use it as a basis for
            tracking down the missing module yourself. Thanks!
"""


def _allow(
    module: str,
    importer: str,
    flags: list[str],
    *,
    category: str = "platform-specific",
    evidence: str = "https://docs.python.org/3/library/sys.html#sys.platform",
) -> dict[str, Any]:
    return {
        "module": module,
        "importer": importer,
        "flags": flags,
        "category": category,
        "evidence": evidence,
    }


def _run_audit(
    tmp_path: Path,
    warning_body: str | None,
    *,
    allowed: list[dict[str, Any]] | None = None,
    platform: str = "linux",
    manifest_platform: str | None = None,
    version: str = _PYINSTALLER_VERSION,
    manifest_version: str | None = None,
    spec_text: str = "a = Analysis([], excludes=[])\n",
    transitive_warning_sha256: str = _EMPTY_TRANSITIVE_DIGEST,
    architecture: str = "x86_64",
    manifest_architecture_digests: dict[str, str] | None = None,
    baseline_pinned_project_modules: list[dict[str, str]] | None = None,
    manifest_alternate_digests: dict[str, list[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    warning_path = tmp_path / "warn-gludd.txt"
    if warning_body is not None:
        warning_path.write_text(
            _WARNING_HEADER + warning_body,
            encoding="utf-8",
        )

    allowlist_path = tmp_path / "pyinstaller-warning-allowlist.json"
    allowlist_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "platform": manifest_platform or platform,
                "pyinstaller_version": manifest_version or version,
                "allowed_missing_imports": allowed or [],
                "baseline_pinned_project_modules": (baseline_pinned_project_modules or []),
                "transitive_warning_sha256_by_architecture": (
                    manifest_architecture_digests or {architecture: transitive_warning_sha256}
                ),
                "reviewed_transitive_warning_sha256_alternates_by_architecture": (manifest_alternate_digests or {}),
            }
        ),
        encoding="utf-8",
    )
    spec_path = tmp_path / "gludd.spec"
    spec_path.write_text(spec_text, encoding="utf-8")

    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--warnings",
            str(warning_path),
            "--allowlist",
            str(allowlist_path),
            "--platform",
            platform,
            "--architecture",
            architecture,
            "--pyinstaller-version",
            version,
            "--spec",
            str(spec_path),
        ],
        cwd=_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_script_exists() -> None:
    assert _SCRIPT.is_file()


def test_makefile_exposes_replayable_linux_warning_audit() -> None:
    makefile = _MAKEFILE.read_text(encoding="utf-8")

    assert "PYINSTALLER_WARNING_FILE_LINUX ?= dist/linux/warn-gludd.txt" in makefile
    assert "\naudit-linux-pyinstaller-warnings:" in makefile
    assert '--warnings "$(PYINSTALLER_WARNING_FILE_LINUX)"' in makefile
    assert makefile.count('--architecture "$$architecture"') == 3


def test_linux_policy_pins_hosted_and_container_architectures() -> None:
    policy = json.loads(_LINUX_POLICY.read_text(encoding="utf-8"))

    assert policy["schema_version"] == 3
    assert policy["transitive_warning_sha256_by_architecture"] == {
        "aarch64": ("b1f5847aeb5bf530dba2b4ef58b0890b5b7a2e409b458fdfe7d0ccbd6a218e02"),
        "x86_64": ("2c13f6587ccf3c51c1f8474df595895b028796abd584aedbfdc30b907cc02e59"),
    }


def test_linux_policy_pins_exact_controller_runtime_boundary_edges() -> None:
    policy = json.loads(_LINUX_POLICY.read_text(encoding="utf-8"))

    actual = {
        (entry["module"], entry["importer"], tuple(entry["flags"]))
        for entry in policy["allowed_missing_imports"]
        if entry["category"] == "controller-runtime-boundary"
    }
    assert actual == _CONTROLLER_RUNTIME_EDGES


def test_exact_reviewed_conditional_and_optional_edges_pass(tmp_path: Path) -> None:
    warnings = (
        "missing module named 'org.python' - "
        "imported by general_ludd.compat.copy (optional)\n"
        "missing module named winreg - imported by "
        "general_ludd.compat.bootstrap (conditional), "
        "general_ludd.compat.platform (delayed, optional)\n"
    )
    allowed = [
        _allow(
            "org.python",
            "general_ludd.compat.copy",
            ["optional"],
            category="interpreter-specific",
            evidence=("https://docs.python.org/3/library/platform.html#cross-platform"),
        ),
        _allow(
            "winreg",
            "general_ludd.compat.bootstrap",
            ["conditional"],
        ),
        _allow(
            "winreg",
            "general_ludd.compat.platform",
            ["delayed", "optional"],
        ),
    ]

    result = _run_audit(tmp_path, warnings, allowed=allowed)

    assert result.returncode == 0, result.stderr
    assert "PASS: audited 3 reviewed missing-import edges" in result.stdout


def test_unreviewed_missing_import_fails(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "missing module named required_package - imported by general_ludd.cli (top-level)\n",
    )

    assert result.returncode == 1
    assert "unreviewed missing-import edge" in result.stderr


def test_hook_provided_runtime_modules_are_audited_separately(
    tmp_path: Path,
) -> None:
    result = _run_audit(
        tmp_path,
        "runtime module named six.moves - imported by "
        "dateutil.tz.tz (top-level), "
        "dateutil.tz._factories (top-level), "
        "dateutil.rrule (top-level)\n",
    )

    assert result.returncode == 0, result.stderr
    assert "0 reviewed missing-import edges" in result.stdout
    assert "3 hook-provided runtime edges" in result.stdout


def test_unknown_module_status_still_fails_closed(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "deferred module named six.moves - imported by dateutil.rrule (top-level)\n",
    )

    assert result.returncode == 1
    assert "unrecognized warning-file line" in result.stderr


@pytest.mark.parametrize(
    ("flags", "reason"),
    [
        (["top-level"], "top-level imports are actionable"),
        (
            ["top-level", "optional"],
            "top-level imports remain actionable even on a mixed edge",
        ),
        (["delayed"], "delayed-only imports are actionable"),
    ],
)
def test_actionable_edge_cannot_be_allowlisted(
    tmp_path: Path,
    flags: list[str],
    reason: str,
) -> None:
    rendered_flags = ", ".join(flags)
    warning = f"missing module named required_package - imported by general_ludd.cli ({rendered_flags})\n"
    result = _run_audit(
        tmp_path,
        warning,
        allowed=[
            _allow(
                "required_package",
                "general_ludd.cli",
                flags,
                category="optional-dependency",
            )
        ],
    )

    assert result.returncode == 1, reason
    assert "actionable import edge" in result.stderr


def test_exact_controller_runtime_boundary_edge_passes_when_root_is_excluded(
    tmp_path: Path,
) -> None:
    result = _run_audit(
        tmp_path,
        "missing module named ansible.executor - imported by "
        "general_ludd.ansible.core_runner (delayed)\n",
        allowed=[
            _allow(
                "ansible.executor",
                "general_ludd.ansible.core_runner",
                ["delayed"],
                category="controller-runtime-boundary",
                evidence="https://docs.ansible.com/projects/builder/en/stable/",
            )
        ],
        spec_text='a = Analysis([], excludes=["ansible"])\n',
    )

    assert result.returncode == 0, result.stderr
    assert "1 reviewed missing-import edges" in result.stdout


def test_controller_runtime_boundary_requires_active_spec_exclude(
    tmp_path: Path,
) -> None:
    result = _run_audit(
        tmp_path,
        "missing module named ansible.executor - imported by "
        "general_ludd.ansible.core_runner (delayed)\n",
        allowed=[
            _allow(
                "ansible.executor",
                "general_ludd.ansible.core_runner",
                ["delayed"],
                category="controller-runtime-boundary",
                evidence="https://docs.ansible.com/projects/builder/en/stable/",
            )
        ],
    )

    assert result.returncode == 1
    assert "controller runtime edge is not covered by active Analysis.excludes" in result.stderr


def test_controller_runtime_boundary_does_not_cover_unrelated_warning(
    tmp_path: Path,
) -> None:
    warnings = (
        "missing module named ansible.executor - imported by "
        "general_ludd.ansible.core_runner (delayed)\n"
        "missing module named required_package - imported by "
        "general_ludd.cli (top-level)\n"
    )
    result = _run_audit(
        tmp_path,
        warnings,
        allowed=[
            _allow(
                "ansible.executor",
                "general_ludd.ansible.core_runner",
                ["delayed"],
                category="controller-runtime-boundary",
                evidence="https://docs.ansible.com/projects/builder/en/stable/",
            )
        ],
        spec_text='a = Analysis([], excludes=["ansible"])\n',
    )

    assert result.returncode == 1
    assert "actionable import edge: missing required_package" in result.stderr
    assert "unreviewed missing-import edge: missing required_package" in result.stderr


def test_active_exclude_boundary_matches_only_complete_module_segments() -> None:
    assert warning_audit._is_covered_by_active_exclude("ansible.executor", {"ansible"})
    assert warning_audit._is_covered_by_active_exclude("ansible", {"ansible"})
    assert not warning_audit._is_covered_by_active_exclude("ansiblex.executor", {"ansible"})


def test_missing_warning_file_fails(tmp_path: Path) -> None:
    result = _run_audit(tmp_path, None)

    assert result.returncode == 1
    assert "warning file does not exist" in result.stderr


def test_unknown_warning_syntax_fails(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "PyInstaller changed this warning format unexpectedly\n",
    )

    assert result.returncode == 1
    assert "unrecognized warning-file line" in result.stderr


@pytest.mark.parametrize(
    ("warning", "message"),
    [
        (
            "missing module named edge - imported by importer-without-flags\n",
            "unrecognized importer syntax",
        ),
        (
            "missing module named edge - imported by junk, general_ludd.cli (optional)\n",
            "unrecognized importer syntax",
        ),
        (
            "missing module named edge - imported by general_ludd.cli (optional, )\n",
            "empty importer or flag",
        ),
        (
            "missing module named edge - imported by general_ludd.cli (mystery)\n",
            "unknown PyInstaller import flags",
        ),
        (
            "missing module named edge - imported by general_ludd.cli (optional, optional)\n",
            "duplicate import flags",
        ),
        (
            "missing module named edge - imported by general_ludd.cli (optional), junk\n",
            "unrecognized importer syntax",
        ),
        (
            "missing module named edge - imported by general_ludd.cli (optional)\n"
            "missing module named edge - imported by general_ludd.cli (optional)\n",
            "duplicate missing-import edges",
        ),
    ],
)
def test_malformed_warning_edges_fail_closed(
    tmp_path: Path,
    warning: str,
    message: str,
) -> None:
    result = _run_audit(tmp_path, warning)

    assert result.returncode == 1
    assert message in result.stderr


def test_allowlist_requires_category_and_evidence(tmp_path: Path) -> None:
    allowed = [
        {
            "module": "org.python",
            "importer": "general_ludd.compat.copy",
            "flags": ["optional"],
            "category": "",
            "evidence": "",
        }
    ]
    result = _run_audit(
        tmp_path,
        "missing module named 'org.python' - imported by general_ludd.compat.copy (optional)\n",
        allowed=allowed,
    )

    assert result.returncode == 1
    assert "non-empty category and evidence" in result.stderr


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            _allow(
                "edge",
                "general_ludd.cli",
                ["optional"],
                evidence="http://example.invalid/evidence",
            ),
            "evidence must be an https URL",
        ),
        (
            _allow("edge", "general_ludd.cli", ["optional", "optional"]),
            "duplicate flags",
        ),
        (
            _allow("edge", "general_ludd.cli", ["mystery"]),
            "unknown flags",
        ),
    ],
)
def test_malformed_allowlist_edge_fails_closed(
    tmp_path: Path,
    entry: dict[str, Any],
    message: str,
) -> None:
    result = _run_audit(tmp_path, "", allowed=[entry])

    assert result.returncode == 1
    assert message in result.stderr


def test_duplicate_allowlist_edge_fails_closed(tmp_path: Path) -> None:
    edge = _allow("edge", "general_ludd.cli", ["optional"])
    result = _run_audit(tmp_path, "", allowed=[edge, edge])

    assert result.returncode == 1
    assert "duplicate allowlist edges" in result.stderr


def test_empty_runtime_architecture_fails_closed(tmp_path: Path) -> None:
    result = _run_audit(tmp_path, "", architecture="")

    assert result.returncode == 1
    assert "architecture must be non-empty" in result.stderr


def test_stale_allowlist_entry_fails(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "",
        allowed=[_allow("winreg", "platform", ["conditional"])],
    )

    assert result.returncode == 1
    assert "stale allowlist edge" in result.stderr


def test_allowlist_is_pinned_to_locked_pyinstaller_version(
    tmp_path: Path,
) -> None:
    result = _run_audit(
        tmp_path,
        "",
        manifest_version="6.19.0",
    )

    assert result.returncode == 1
    assert "PyInstaller version mismatch" in result.stderr


def test_allowlist_is_pinned_to_target_platform(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "",
        manifest_platform="darwin",
    )

    assert result.returncode == 1
    assert "platform mismatch" in result.stderr


def test_importer_and_flags_must_match_exactly(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "missing module named winreg - imported by general_ludd.compat.platform (optional)\n",
        allowed=[
            _allow(
                "winreg",
                "general_ludd.compat.bootstrap",
                ["conditional"],
            )
        ],
    )

    assert result.returncode == 1
    assert "unreviewed missing-import edge" in result.stderr
    assert "stale allowlist edge" in result.stderr


def test_exact_active_analysis_exclude_passes(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "excluded module named winreg - imported by platform (conditional)\n",
        spec_text="""\
import sys

_platform_excludes = []
if sys.platform == "win32":
    _platform_excludes = ["fcntl"]
if sys.platform != "win32":
    _platform_excludes = ["winreg"]

a = Analysis([], excludes=["pytest"] + _platform_excludes)
""",
    )

    assert result.returncode == 0, result.stderr
    assert "1 spec-excluded edge" in result.stdout


def test_excluded_warning_not_in_analysis_excludes_fails(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "excluded module named unreviewed - imported by general_ludd.app (conditional)\n",
    )

    assert result.returncode == 1
    assert "module is not in active Analysis.excludes" in result.stderr


def test_inactive_platform_exclude_is_not_accepted(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "excluded module named fcntl - imported by general_ludd.app (conditional)\n",
        spec_text="""\
import sys

_platform_excludes = []
if sys.platform == "win32":
    _platform_excludes = ["fcntl"]
if sys.platform != "win32":
    _platform_excludes = ["winreg"]

a = Analysis([], excludes=_platform_excludes)
""",
    )

    assert result.returncode == 1
    assert "module is not in active Analysis.excludes" in result.stderr


def test_missing_or_malformed_spec_fails_closed(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "",
        spec_text="this is not valid Python !!!\n",
    )

    assert result.returncode == 1
    assert "cannot parse PyInstaller spec" in result.stderr


def test_transitive_warning_graph_requires_exact_normalized_digest(
    tmp_path: Path,
) -> None:
    warning = "missing module named optional_backend - imported by dependency.compat (optional)\n"
    result = _run_audit(tmp_path, warning)

    assert result.returncode == 1
    assert "transitive warning digest mismatch" in result.stderr


def test_exact_transitive_warning_graph_digest_passes(tmp_path: Path) -> None:
    warning = "missing module named optional_backend - imported by dependency.compat (optional)\n"
    normalized = "missing optional_backend <- dependency.compat (optional)"
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    result = _run_audit(
        tmp_path,
        warning,
        transitive_warning_sha256=digest,
    )

    assert result.returncode == 0, result.stderr
    assert "1 baseline-pinned transitive edge" in result.stdout


def test_transitive_warning_digest_is_selected_by_architecture(
    tmp_path: Path,
) -> None:
    warning = "missing module named optional_backend - imported by dependency.compat (optional)\n"
    normalized = "missing optional_backend <- dependency.compat (optional)"
    x86_64_digest = hashlib.sha256(normalized.encode()).hexdigest()
    result = _run_audit(
        tmp_path,
        warning,
        architecture="x86_64",
        manifest_architecture_digests={
            "aarch64": _EMPTY_TRANSITIVE_DIGEST,
            "x86_64": x86_64_digest,
        },
    )

    assert result.returncode == 0, result.stderr
    assert "architecture=x86_64" in result.stdout


def test_missing_architecture_digest_fails_closed(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "",
        architecture="x86_64",
        manifest_architecture_digests={
            "aarch64": _EMPTY_TRANSITIVE_DIGEST,
        },
    )

    assert result.returncode == 1
    assert "no transitive warning digest for architecture 'x86_64'" in result.stderr


def test_reviewed_alternate_digest_passes(tmp_path: Path) -> None:
    """A reviewed alternate digest passes: consecutive CI builds of identical
    code can flip between two observed transitive graphs (rounds 18/19); any
    OTHER digest still fails closed."""
    warnings = "missing module named 'org.python' - imported by general_ludd.compat.copy (optional)\n"
    allowed = [
        _allow("org.python", "general_ludd.compat.copy", ["optional"]),
    ]
    result = _run_audit(
        tmp_path,
        warnings,
        allowed=allowed,
        # Primary digest does not match; the reviewed ALTERNATE does.
        transitive_warning_sha256="f" * 64,
        manifest_alternate_digests={"x86_64": ["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"]},
    )
    assert "transitive warning digest mismatch" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr


def test_unreviewed_digest_still_fails_closed(tmp_path: Path) -> None:
    """An unknown digest fails closed even when alternates exist."""
    warnings = "missing module named 'org.python' - imported by general_ludd.compat.copy (optional)\n"
    allowed = [
        _allow("org.python", "general_ludd.compat.copy", ["optional"]),
    ]
    result = _run_audit(
        tmp_path,
        warnings,
        allowed=allowed,
        transitive_warning_sha256="f" * 64,
    )
    assert "transitive warning digest mismatch" in result.stderr
    assert result.returncode == 1


def test_runtime_architecture_alias_uses_canonical_digest(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "",
        architecture="amd64",
        manifest_architecture_digests={
            "x86_64": _EMPTY_TRANSITIVE_DIGEST,
        },
    )

    assert result.returncode == 0, result.stderr
    assert "architecture=x86_64" in result.stdout


def test_manifest_architecture_alias_fails_closed(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "",
        architecture="amd64",
        manifest_architecture_digests={
            "amd64": _EMPTY_TRANSITIVE_DIGEST,
        },
    )

    assert result.returncode == 1
    assert "'amd64' should be 'x86_64'" in result.stderr


def test_project_module_attribute_graph_can_be_digest_pinned(
    tmp_path: Path,
) -> None:
    warning = (
        "missing module named pydantic.BaseModel - "
        "imported by general_ludd.schemas.job (top-level), "
        "pydantic._internal._fields (conditional)\n"
    )
    normalized = "\n".join(
        [
            ("missing pydantic.BaseModel <- general_ludd.schemas.job (top-level)"),
            ("missing pydantic.BaseModel <- pydantic._internal._fields (conditional)"),
        ]
    )
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    result = _run_audit(
        tmp_path,
        warning,
        transitive_warning_sha256=digest,
        baseline_pinned_project_modules=[
            {
                "module": "pydantic.BaseModel",
                "category": "module-attribute",
                "evidence": ("https://pyinstaller.org/en/stable/when-things-go-wrong.html#build-time-messages"),
            }
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "2 baseline-pinned transitive edges" in result.stdout


def test_transitive_warning_digest_must_be_lowercase_sha256(
    tmp_path: Path,
) -> None:
    result = _run_audit(
        tmp_path,
        "",
        transitive_warning_sha256="not-a-sha256",
    )

    assert result.returncode == 1
    assert "transitive_warning_sha256_by_architecture" in result.stderr


def test_connector_registry_avoids_pyinstaller_path_pseudo_module() -> None:
    source = _CONNECTOR_REGISTRY.read_text(encoding="utf-8")

    assert "from general_ludd.connectors import __path__" not in source
    assert "import general_ludd.connectors as _connectors_pkg" in source
    assert "pkgutil.iter_modules(_connectors_pkg.__path__)" in source


def test_optional_gcp_sdk_import_is_locally_guarded() -> None:
    source = _PRICING_SOURCES.read_text(encoding="utf-8")

    assert ("try:\n            from google.cloud import billing\n        except ImportError as exc:") in source
