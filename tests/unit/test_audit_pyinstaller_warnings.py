"""Fail-closed tests for the PyInstaller missing-module warning audit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "audit_pyinstaller_warnings.py"
_PYINSTALLER_VERSION = "6.20.0"

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
                "schema_version": 1,
                "platform": manifest_platform or platform,
                "pyinstaller_version": manifest_version or version,
                "allowed_missing_imports": allowed or [],
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


def test_exact_reviewed_conditional_and_optional_edges_pass(tmp_path: Path) -> None:
    warnings = (
        "missing module named 'org.python' - imported by copy (optional)\n"
        "missing module named winreg - imported by "
        "importlib._bootstrap_external (conditional), "
        "platform (delayed, optional)\n"
    )
    allowed = [
        _allow(
            "org.python",
            "copy",
            ["optional"],
            category="interpreter-specific",
            evidence=(
                "https://docs.python.org/3/library/platform.html"
                "#cross-platform"
            ),
        ),
        _allow(
            "winreg",
            "importlib._bootstrap_external",
            ["conditional"],
        ),
        _allow(
            "winreg",
            "platform",
            ["delayed", "optional"],
        ),
    ]

    result = _run_audit(tmp_path, warnings, allowed=allowed)

    assert result.returncode == 0, result.stderr
    assert "PASS: audited 3 reviewed missing-import edges" in result.stdout


def test_unreviewed_missing_import_fails(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "missing module named required_package - "
        "imported by general_ludd.cli (top-level)\n",
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
        "deferred module named six.moves - "
        "imported by dateutil.rrule (top-level)\n",
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
    warning = (
        "missing module named required_package - "
        f"imported by general_ludd.cli ({rendered_flags})\n"
    )
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


def test_allowlist_requires_category_and_evidence(tmp_path: Path) -> None:
    allowed = [
        {
            "module": "org.python",
            "importer": "copy",
            "flags": ["optional"],
            "category": "",
            "evidence": "",
        }
    ]
    result = _run_audit(
        tmp_path,
        "missing module named 'org.python' - imported by copy (optional)\n",
        allowed=allowed,
    )

    assert result.returncode == 1
    assert "non-empty category and evidence" in result.stderr


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
        "missing module named winreg - imported by platform (optional)\n",
        allowed=[
            _allow(
                "winreg",
                "importlib._bootstrap_external",
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
        "excluded module named unreviewed - imported by app (conditional)\n",
    )

    assert result.returncode == 1
    assert "module is not in active Analysis.excludes" in result.stderr


def test_inactive_platform_exclude_is_not_accepted(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        "excluded module named fcntl - imported by app (conditional)\n",
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
