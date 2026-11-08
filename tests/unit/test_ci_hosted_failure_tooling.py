"""Contracts for bounded hosted-failure diagnostics and Python-version replay."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]


def _target_block(source: str, name: str) -> str:
    marker = f"{name}:"
    assert marker in source
    return source.split(marker, 1)[1].split("\n\n", 1)[0]


def test_ci_job_failure_context_is_authenticated_bounded_and_fail_closed() -> None:
    source = compose_makefile(ROOT / "Makefile")
    block = _target_block(source, "ci-job-failure-context")

    assert "gh run view -R sandboxcom/gludd" not in block
    assert "--json jobs" not in block
    assert '"repos/sandboxcom/gludd/actions/jobs/$(JOB)"' in block
    assert "select(.id == $(JOB) and .run_id == $(RUN)) | .id" in block
    assert "gh api --method GET" in block
    assert "repos/sandboxcom/gludd/actions/jobs/$(JOB)/logs" in block
    assert "X-GitHub-Api-Version: 2026-03-10" in block
    assert "--log --job=" not in block
    assert 'if [ ! -s "$$LOG" ]' in block
    assert "scripts/resource_arbiter.py root" in block
    assert 'mkdir -p "$$RESOURCE_ROOT"' in block
    assert 'mktemp "$$RESOURCE_ROOT/ci-job-$(RUN)-$(JOB).log.XXXXXX"' in block
    assert '--artifact-root "$$RESOURCE_ROOT"' in block
    assert '--artifact-file "$$(basename "$$LOG")"' in block
    assert '--log "$$LOG"' not in block
    assert ".gate-logs/ci-job-" not in block
    assert "scripts/ci_shards_log_context.py" in block
    for variable in (
        "$(RUN)",
        "$(JOB)",
        "$(PATTERN)",
        "$(BEFORE)",
        "$(AFTER)",
        "$(MAX_MATCHES)",
        "$(CI_JOB_CONTEXT_VALIDATE_ONLY)",
    ):
        assert variable in block
    assert "|| true" not in block
    assert "2>/dev/null" not in block


def test_ci_artifact_download_is_run_bound_confined_and_atomic() -> None:
    source = compose_makefile(ROOT / "Makefile")
    block = _target_block(source, "ci-artifact-download")

    assert "gh api repos/sandboxcom/gludd/actions/runs/$(RUN)/artifacts" in block
    assert 'gh run download "$(RUN)" -R sandboxcom/gludd' in block
    assert '-n "$(ARTIFACT)"' in block
    assert "CI_ARTIFACT_OUTPUT_ROOT ?= RESOURCE_ROOT" in source
    assert "scripts/resource_arbiter.py root" in block
    assert 'OUTPUT_ROOT="$$RESOURCE_ROOT/ci-artifacts"' in block
    assert "CI_ARTIFACT_HEARTBEAT_SECS ?= 10" in source
    assert "mktemp -d" in block
    assert "trap" in block
    assert "artifact-download heartbeat" in block
    assert 'kill -TERM "$$DOWNLOAD_PID"' in block
    assert 'wait "$$DOWNLOAD_PID"' in block
    assert 'mv "$$TMP" "$$DEST"' in block
    assert "Refusing unsafe ARTIFACT" in block
    assert "Refusing unsafe CI_ARTIFACT_OUTPUT_ROOT" in block
    assert ".gate-logs/ci-artifacts" not in block
    assert "|| true" not in block
    assert "ignore_errors" not in block


def test_ci_artifact_context_is_run_bound_confined_and_bounded() -> None:
    source = compose_makefile(ROOT / "Makefile")
    block = _target_block(source, "ci-artifact-context")

    assert "scripts/resource_arbiter.py root" in block
    assert 'ci-artifacts/run-$(RUN)/$(ARTIFACT)' in block
    assert "scripts/ci_shards_log_context.py" in block
    assert "--artifact-root" in block
    assert "--artifact-file" in block
    for variable in (
        "$(RUN)",
        "$(ARTIFACT)",
        "$(CI_ARTIFACT_FILE)",
        "$(PATTERN)",
        "$(BEFORE)",
        "$(AFTER)",
        "$(MAX_MATCHES)",
        "$(CI_ARTIFACT_CONTEXT_VALIDATE_ONLY)",
    ):
        assert variable in block
    assert "|| true" not in block
    assert ".gate-logs" not in block


def test_ci_artifact_download_rejects_checkout_output_even_in_validate_only() -> None:
    """Diagnostics must never share the source checkout with an active test run."""
    result = subprocess.run(
        [
            "make",
            "ci-artifact-download",
            "RUN=1",
            "ARTIFACT=diagnostics",
            "CI_ARTIFACT_OUTPUT_ROOT=.gate-logs/ci-artifacts",
            "CI_ARTIFACT_HEARTBEAT_SECS=1",
            "CI_ARTIFACT_DOWNLOAD_VALIDATE_ONLY=1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Refusing unsafe CI_ARTIFACT_OUTPUT_ROOT" in result.stdout


def test_ci_coverage_artifact_audit_reads_only_the_resource_namespace() -> None:
    """Downloaded hosted coverage must be auditable without re-entering source."""
    source = compose_makefile(ROOT / "Makefile")
    block = _target_block(source, "ci-coverage-artifact-audit")

    assert "scripts/resource_arbiter.py root" in block
    assert 'coverage.xml' in block
    assert "scripts/audit_coverage.py" in block
    assert "--xml-file=" in block
    assert "CI_COVERAGE_RUN" in block
    assert "CI_COVERAGE_ARTIFACT" in block
    assert "CI_COVERAGE_INPUT" in block
    assert "CI_COVERAGE_SOURCE" in block
    assert "CI_COVERAGE_AGGREGATE_MIN" in block
    assert "CI_COVERAGE_PER_FILE_MIN" in block
    assert "CI_COVERAGE_AUDIT_VALIDATE_ONLY" in block
    assert "CI_COVERAGE_HEARTBEAT_SECS" in block
    assert "coverage combine" in block
    assert "coverage json" in block
    assert "--rcfile=config/coverage_ci_artifacts.ini" in block
    assert "coverage-data heartbeat" in block
    for artifact in (
        "coverage-other-3.11",
        "coverage-unit-1a1-3.11",
        "coverage-unit-1a2-3.11",
        "coverage-unit-1b-3.11",
        "coverage-unit-1d-3.11",
        "coverage-unit-2-3.11",
        "coverage-unit-3a-3.11",
        "coverage-unit-3b-3.11",
    ):
        assert artifact in block
    assert ".gate-logs/ci-artifacts" not in block
    assert "|| true" not in block


def test_ci_coverage_path_map_rehomes_hosted_sources_without_omitting_them() -> None:
    """Hosted data must map onto local source rather than suppress missing files."""
    parser = ConfigParser()
    path = ROOT / "config" / "coverage_ci_artifacts.ini"
    assert parser.read(path) == [str(path)]

    source_paths = parser["paths"]["source"].splitlines()
    collection_paths = parser["paths"]["collections"].splitlines()
    assert "src/general_ludd" in source_paths
    assert "/home/runner/work/gludd/gludd/src/general_ludd" in source_paths
    assert "collections/ansible_collections" in collection_paths
    assert "/home/runner/work/gludd/gludd/collections/ansible_collections" in collection_paths
    assert "omit" not in parser["report"]


def test_ci_tooling_coverage_profile_measures_the_changed_auditor() -> None:
    """Focused coverage must measure CI tooling, not the application-only tree."""
    parser = ConfigParser()
    path = ROOT / "config" / "coverage_ci_tooling.ini"
    assert parser.read(path) == [str(path)]

    included = parser["run"]["include"].splitlines()
    assert "*/scripts/audit_coverage.py" in included
    assert "*/scripts/coverage_missing_lines.py" in included
    assert parser["run"].getboolean("branch") is True
    assert parser["report"].getboolean("show_missing") is True


def test_ci_coverage_gap_plan_is_external_bounded_and_contract_registered() -> None:
    """Coverage remediation must read exact-run evidence without workspace writes."""
    source = compose_makefile(ROOT / "Makefile")
    block = _target_block(source, "ci-coverage-gap-plan")

    assert "scripts/resource_arbiter.py root" in block
    assert "scripts/coverage_missing_lines.py" in block
    assert "coverage-data.json" in block
    assert "coverage.xml" in block
    assert "COVERAGE-GAP-INPUT" in block
    assert "CI_COVERAGE_GAP_LIMIT" in block
    assert "CI_COVERAGE_GAP_PLAN_VALIDATE_ONLY" in block
    assert ".gate-logs" not in block
    assert "|| true" not in block

    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    contracts = {item["name"]: item for item in payload["targets"]}
    assert contracts["ci-coverage-gap-plan"] == {
        "name": "ci-coverage-gap-plan",
        "make_variables": [
            "CI_COVERAGE_RUN",
            "CI_COVERAGE_ARTIFACT",
            "CI_COVERAGE_SOURCE",
            "CI_COVERAGE_PER_FILE_MIN",
            "CI_COVERAGE_GAP_LIMIT",
            "CI_COVERAGE_GAP_PLAN_VALIDATE_ONLY",
        ],
        "behavior": (
            "make ci-coverage-gap-plan CI_COVERAGE_RUN=1 "
            "CI_COVERAGE_ARTIFACT=coverage-merged "
            "CI_COVERAGE_SOURCE=src/general_ludd CI_COVERAGE_PER_FILE_MIN=75 "
            "CI_COVERAGE_GAP_LIMIT=20 CI_COVERAGE_GAP_PLAN_VALIDATE_ONLY=1"
        ),
    }


def test_python_version_replay_runs_only_the_requested_node() -> None:
    source = compose_makefile(ROOT / "Makefile")
    block = _target_block(source, "test-specific-pyver")

    assert "scripts/resource_arbiter.py root" in block
    assert 'mkdir -p "$$RESOURCE_ROOT"' in block
    assert block.index('mkdir -p "$$RESOURCE_ROOT"') < block.index("mktemp -d")
    assert "UV_PROJECT_ENVIRONMENT" in block
    assert "mktemp -d" in block
    assert 'WORK="$$(mktemp -d ' in block
    assert "trap" in block
    assert "scripts/dependency_profiles.py sync" in block
    assert '--set ci --environment "$$WORK/.venv"' in block
    assert '--python "$(PYTHON_VERSION)"' in block
    assert '$(UV) run --no-sync --python "$(PYTHON_VERSION)" python -m pytest $(TESTFILE)' in block
    assert "-W error" in block
    assert "--basetemp=" in block
    assert "tests/" not in block.replace("$(TESTFILE)", "")


def test_new_ci_targets_have_safe_behavioral_contracts() -> None:
    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    contracts = {item["name"]: item for item in payload["targets"]}

    assert contracts["ci-job-failure-context"] == {
        "name": "ci-job-failure-context",
        "make_variables": [
            "RUN",
            "JOB",
            "PATTERN",
            "BEFORE",
            "AFTER",
            "MAX_MATCHES",
            "CI_JOB_CONTEXT_VALIDATE_ONLY",
        ],
        "behavior": (
            "make ci-job-failure-context RUN=1 JOB=1 PATTERN=FAILED "
            "BEFORE=2 AFTER=4 MAX_MATCHES=5 CI_JOB_CONTEXT_VALIDATE_ONLY=1"
        ),
    }
    assert contracts["ci-artifact-download"] == {
        "name": "ci-artifact-download",
        "make_variables": [
            "RUN",
            "ARTIFACT",
            "CI_ARTIFACT_OUTPUT_ROOT",
            "CI_ARTIFACT_HEARTBEAT_SECS",
            "CI_ARTIFACT_DOWNLOAD_VALIDATE_ONLY",
        ],
        "behavior": (
            "make ci-artifact-download RUN=1 ARTIFACT=diagnostics "
            "CI_ARTIFACT_OUTPUT_ROOT=RESOURCE_ROOT "
            "CI_ARTIFACT_HEARTBEAT_SECS=1 "
            "CI_ARTIFACT_DOWNLOAD_VALIDATE_ONLY=1"
        ),
    }
    assert contracts["ci-artifact-context"] == {
        "name": "ci-artifact-context",
        "make_variables": [
            "RUN",
            "ARTIFACT",
            "CI_ARTIFACT_FILE",
            "PATTERN",
            "BEFORE",
            "AFTER",
            "MAX_MATCHES",
            "CI_ARTIFACT_CONTEXT_VALIDATE_ONLY",
        ],
        "behavior": (
            "make ci-artifact-context RUN=1 ARTIFACT=diagnostics "
            "CI_ARTIFACT_FILE=failure.log PATTERN=FAILED BEFORE=2 AFTER=4 "
            "MAX_MATCHES=1 CI_ARTIFACT_CONTEXT_VALIDATE_ONLY=1"
        ),
    }
    assert contracts["ci-coverage-artifact-audit"] == {
        "name": "ci-coverage-artifact-audit",
        "make_variables": [
            "CI_COVERAGE_RUN",
            "CI_COVERAGE_ARTIFACT",
            "CI_COVERAGE_INPUT",
            "CI_COVERAGE_SOURCE",
            "CI_COVERAGE_AGGREGATE_MIN",
            "CI_COVERAGE_PER_FILE_MIN",
            "CI_COVERAGE_AUDIT_VALIDATE_ONLY",
            "CI_COVERAGE_HEARTBEAT_SECS",
        ],
        "behavior": (
            "make ci-coverage-artifact-audit CI_COVERAGE_RUN=1 "
            "CI_COVERAGE_ARTIFACT=coverage-merged "
            "CI_COVERAGE_INPUT=xml "
            "CI_COVERAGE_SOURCE=src/general_ludd "
            "CI_COVERAGE_AGGREGATE_MIN=85 CI_COVERAGE_PER_FILE_MIN=75 "
            "CI_COVERAGE_AUDIT_VALIDATE_ONLY=1 CI_COVERAGE_HEARTBEAT_SECS=1"
        ),
    }
    assert contracts["test-specific-pyver"] == {
        "name": "test-specific-pyver",
        "make_variables": ["TESTFILE", "PYTHON_VERSION", "PYTEST_ARGS"],
        "behavior": (
            "make test-specific-pyver "
            "TESTFILE=tests/unit/test_ci_hosted_failure_tooling.py "
            "PYTHON_VERSION=3.11 PYTEST_ARGS=-q"
        ),
    }
def test_hosted_failure_diagnostics_always_materialize_one_bounded_artifact() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )
    collect = workflow.split(
        "- name: Collect failure diagnostics (shard ${{ matrix.shard }})", 1
    )[1].split("- name: Upload failure diagnostics", 1)[0]
    upload = workflow.split(
        "- name: Upload failure diagnostics (shard ${{ matrix.shard }})", 1
    )[1].split("- name: Upload coverage data", 1)[0]
    artifact = (
        "failure-diagnostics-${{ matrix.shard }}-"
        "${{ matrix.python-version }}.log"
    )

    assert artifact in collect
    assert "tee" in collect
    assert "test -s" in collect
    assert artifact in upload
    assert "if-no-files-found: error" in upload
    assert "continue-on-error" not in collect
    assert "continue-on-error" not in upload


def test_molecule_failure_artifact_retains_raw_pyinstaller_warning_graph() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )
    upload = workflow.split(
        "- name: Upload molecule logs (shard ${{ matrix.shard }}/4)", 1
    )[1].split("\n\n", 1)[0]
    verify = workflow.split(
        "- name: Verify Linux PyInstaller warning graph artifact", 1
    )[1].split("- name: Upload molecule logs", 1)[0]

    assert "if: always() && matrix.shard == 1" in verify
    assert "test -s dist/linux/warn-gludd.txt" in verify
    assert "path: |" in upload
    assert "/tmp/gludd-molecule-*.log" in upload
    assert "dist/linux/warn-gludd.txt" in upload
    assert "if-no-files-found: error" in upload


def test_ci_pyinstaller_warning_audit_replays_the_exact_downloaded_graph() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    block = _target_block(makefile, "ci-pyinstaller-warning-audit")

    assert "scripts/resource_arbiter.py root" in block
    assert 'ci-artifacts/run-$(RUN)/$(ARTIFACT)' in block
    assert "warn-gludd.txt" in block
    assert "scripts/audit_pyinstaller_warnings.py" in block
    assert "CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY" in block
    assert "PYINSTALLER_WARNING_ARCHITECTURE_LINUX" in block
    assert "PYINSTALLER_VERSION_LINUX" in block
    assert "|| true" not in block

    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    contracts = {item["name"]: item for item in payload["targets"]}
    assert contracts["ci-pyinstaller-warning-audit"] == {
        "name": "ci-pyinstaller-warning-audit",
        "make_variables": [
            "RUN",
            "ARTIFACT",
            "PYINSTALLER_WARNING_ARCHITECTURE_LINUX",
            "PYINSTALLER_VERSION_LINUX",
            "CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY",
            "CI_PYINSTALLER_WARNING_RAW_OUTPUT",
        ],
        "behavior": (
            "make ci-pyinstaller-warning-audit RUN=1 "
            "ARTIFACT=molecule-logs-shard-1 "
            "PYINSTALLER_WARNING_ARCHITECTURE_LINUX=x86_64 "
            "PYINSTALLER_VERSION_LINUX=6.22.3 "
            "CI_PYINSTALLER_WARNING_RAW_OUTPUT=artifacts/pyinstaller-warning-contract.txt "
            "CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY=1"
        ),
    }


@dataclass
class _NativeWarningAudit:
    root: Path
    resource: Path
    warning: Path
    environment: dict[str, str] = field(repr=False)

    def invoke(
        self, *arguments: str, early_environment: bool = False,
        mode: str = "", environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "make", "--no-print-directory", *(["-e"] if early_environment else []),
                *([mode] if mode else []), "ci-pyinstaller-warning-audit",
                "RUN=42391330412", "ARTIFACT=owned-warning-graph",
                "PYINSTALLER_WARNING_ARCHITECTURE_LINUX=x86_64", "PYINSTALLER_VERSION_LINUX=6.22.3",
                "PYTHON=" + sys.executable, "SYSTEM_PYTHON=" + sys.executable,
                "UV=" + shlex.join((sys.executable, str(self.root / "scripts/owned_uv.py"))),
                "GATE_CANDIDATE_SHA=" + "a" * 40, *arguments,
            ],
            cwd=self.root, env=environment or self.environment,
            capture_output=True, text=True, check=False, timeout=30,
        )


def _native_warning_audit_fixture(tmp_path: Path) -> _NativeWarningAudit:
    root = tmp_path / "owned-checkout"
    root.mkdir()
    shutil.copy(ROOT / "Makefile", root / "Makefile")
    shutil.copytree(ROOT / "make", root / "make")
    scripts = root / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/audit_pyinstaller_warnings.py", scripts / "audit_pyinstaller_warnings.py")
    resource = tmp_path / "owned-resources"
    artifact = resource / "ci-artifacts/run-42391330412/owned-warning-graph"
    artifact.mkdir(parents=True)
    warning = artifact / "warn-gludd.txt"
    warning.write_text(
        "".join(
            f"missing module named owned_optional_{index} - imported by dependency.compat (optional)\n"
            for index in range(915)
        ),
        encoding="utf-8",
    )
    (scripts / "resource_arbiter.py").write_text(
        f"print({str(resource)!r})\n", encoding="utf-8",
    )
    (scripts / "owned_uv.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:3] == ['run', 'python']\n"
        "Path('owned-audit-argv.json').write_text(json.dumps(sys.argv[3:]), encoding='utf-8')\n"
        "status = int(os.environ.get('OWNED_WARNING_NATIVE_RC', '0'))\n"
        "if status:\n    raise SystemExit(status)\n"
        "os.execve(sys.executable, [sys.executable, *sys.argv[3:]], dict(os.environ))\n",
        encoding="utf-8",
    )
    config = root / "config"
    config.mkdir()
    (config / "pyinstaller-warning-allowlist-linux.json").write_text(
        json.dumps({
            "schema_version": 3, "platform": "linux", "pyinstaller_version": "6.22.3",
            "allowed_missing_imports": [], "baseline_pinned_project_modules": [],
            "transitive_warning_sha256_by_architecture": {"x86_64": hashlib.sha256(b"").hexdigest()},
            "reviewed_transitive_warning_sha256_alternates_by_architecture": {},
        }),
        encoding="utf-8",
    )
    (root / "gludd.spec").write_text("a = Analysis([], excludes=[])\n", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {
            "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "GNUMAKEFLAGS", "PYTEST_ARGS",
            "CI_PYINSTALLER_WARNING_RAW_OUTPUT", "CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY",
            "GLUDD_CI_PYINSTALLER_WARNING_RAW_OUTPUT", "OWNED_WARNING_NATIVE_RC",
        }
    }
    environment["UV_NO_SYNC"] = "1"
    return _NativeWarningAudit(root, resource, warning, environment)


@pytest.mark.parametrize("origin", ["command-line", "environment", "environment-e"])
def test_native_warning_audit_exports_complete_raw_graph_without_accepting_it(
    tmp_path: Path, origin: str,
) -> None:
    fixture = _native_warning_audit_fixture(tmp_path)
    relative = "artifacts/pyinstaller-warning-owned-915.txt"
    environment = dict(fixture.environment)
    arguments: tuple[str, ...] = ()
    if origin == "command-line":
        arguments = ("CI_PYINSTALLER_WARNING_RAW_OUTPUT=" + relative,)
    else:
        environment["CI_PYINSTALLER_WARNING_RAW_OUTPUT"] = relative
    before = fixture.warning.read_bytes()
    assert len(before) > 32_768

    result = fixture.invoke(
        *arguments, early_environment=origin == "environment-e", environment=environment,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "transitive warning digest mismatch" in result.stderr
    assert "total=915 shown=50" in result.stderr
    assert "PASS: audited" not in result.stdout
    assert (fixture.root / relative).read_bytes() == before
    assert fixture.warning.read_bytes() == before
    assert "sha256=" + hashlib.sha256(before).hexdigest() in result.stdout
    assert not list((fixture.root / "artifacts").glob(".*.tmp"))
    argv = json.loads((fixture.root / "owned-audit-argv.json").read_text(encoding="utf-8"))
    assert argv == [
        "scripts/audit_pyinstaller_warnings.py", "--warnings", str(fixture.warning),
        "--allowlist", "config/pyinstaller-warning-allowlist-linux.json", "--platform", "linux",
        "--architecture", "x86_64", "--pyinstaller-version", "6.22.3", "--spec", "gludd.spec",
        "--raw-export", relative,
    ]
    assert environment.get("CI_PYINSTALLER_WARNING_RAW_OUTPUT") == (
        None if origin == "command-line" else relative
    )


@pytest.mark.parametrize("early_environment", [False, True])
def test_native_warning_export_uses_physical_checkout_with_foreign_curdir(
    tmp_path: Path, early_environment: bool,
) -> None:
    fixture = _native_warning_audit_fixture(tmp_path)
    foreign = tmp_path / "owned-foreign-curdir"
    foreign.mkdir()
    relative = "artifacts/pyinstaller-warning-physical-root.txt"
    result = fixture.invoke(
        "CI_PYINSTALLER_WARNING_RAW_OUTPUT=" + relative, "CURDIR=" + str(foreign),
        early_environment=early_environment,
    )
    assert result.returncode == 2
    assert "transitive warning digest mismatch" in result.stderr
    assert (fixture.root / relative).read_bytes() == fixture.warning.read_bytes()
    assert list(foreign.iterdir()) == []


def test_native_warning_audit_default_and_native_failure_status_remain(
    tmp_path: Path,
) -> None:
    fixture = _native_warning_audit_fixture(tmp_path)
    ordinary = fixture.invoke()
    assert ordinary.returncode == 2
    assert "transitive warning digest mismatch" in ordinary.stderr
    assert not (fixture.root / "artifacts").exists()
    argv = json.loads((fixture.root / "owned-audit-argv.json").read_text(encoding="utf-8"))
    assert "--raw-export" not in argv
    environment = dict(fixture.environment)
    environment["OWNED_WARNING_NATIVE_RC"] = "73"
    failed = fixture.invoke(
        "CI_PYINSTALLER_WARNING_RAW_OUTPUT=artifacts/pyinstaller-warning-native-error.txt",
        environment=environment,
    )
    assert failed.returncode == 2
    assert "Error 73" in failed.stderr
    assert not (fixture.root / "artifacts").exists()


@pytest.mark.parametrize("early_environment", [False, True])
@pytest.mark.parametrize("mode", ["", "-n", "-q", "-t"])
def test_native_warning_export_offline_and_inspection_modes_write_nothing(
    tmp_path: Path, early_environment: bool, mode: str,
) -> None:
    fixture = _native_warning_audit_fixture(tmp_path)
    before = fixture.warning.read_bytes()
    arguments = ["CI_PYINSTALLER_WARNING_RAW_OUTPUT=artifacts/pyinstaller-warning-plan.txt"]
    if mode == "":
        arguments.append("CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY=1")
    result = fixture.invoke(*arguments, early_environment=early_environment, mode=mode)
    assert result.returncode == (1 if mode == "-q" else 0), result.stdout + result.stderr
    assert not (fixture.root / "artifacts").exists()
    assert not (fixture.root / "owned-audit-argv.json").exists()
    assert not (fixture.root / "ci-pyinstaller-warning-audit").exists()
    assert fixture.warning.read_bytes() == before


@pytest.mark.parametrize("warning_count", [0, 2])
def test_native_warning_export_retains_exactly_one_artifact_file_guard(
    tmp_path: Path, warning_count: int,
) -> None:
    fixture = _native_warning_audit_fixture(tmp_path)
    if warning_count == 0:
        fixture.warning.unlink()
    else:
        extra = fixture.warning.parent / "nested"
        extra.mkdir()
        (extra / "warn-gludd.txt").write_bytes(fixture.warning.read_bytes())
    result = fixture.invoke(
        "CI_PYINSTALLER_WARNING_RAW_OUTPUT=artifacts/pyinstaller-warning-count.txt",
    )
    assert result.returncode == 2
    assert f"found {warning_count}" in result.stdout
    assert "Expected exactly one warn-gludd.txt" in result.stdout
    assert not (fixture.root / "owned-audit-argv.json").exists()
    assert not (fixture.root / "artifacts").exists()


@pytest.mark.parametrize("value", [
    "/tmp/gludd-foreign.txt", "../outside.txt", "artifacts/../outside.txt",
    "artifacts/pyinstaller-warning-has.dot.txt", "artifacts/pyinstaller-warning-.txt",
    "artifacts/pyinstaller-warning-" + "a" * 97 + ".txt",
])
def test_native_warning_export_rejects_unsafe_output_before_offline_plan(
    tmp_path: Path, value: str,
) -> None:
    fixture = _native_warning_audit_fixture(tmp_path)
    result = fixture.invoke(
        "CI_PYINSTALLER_WARNING_RAW_OUTPUT=" + value,
        "CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY=1",
    )
    assert result.returncode == 2
    assert "CI_PYINSTALLER_WARNING_RAW_OUTPUT" in result.stdout
    assert not (fixture.root / "owned-audit-argv.json").exists()
    assert not (fixture.root / "artifacts").exists()


@pytest.mark.parametrize("origin", ["command-line", "environment", "environment-e"])
def test_native_warning_export_input_is_literal_with_active_same_entrypoint_witness(
    tmp_path: Path, origin: str,
) -> None:
    fixture = _native_warning_audit_fixture(tmp_path)
    marker = tmp_path / "owned-evaluated-warning-path"
    payload = "$(shell touch " + shlex.quote(str(marker)) + ")"
    environment = dict(fixture.environment)
    arguments = ["CI_PYINSTALLER_WARNING_AUDIT_VALIDATE_ONLY=1"]
    if origin == "command-line":
        arguments.append("CI_PYINSTALLER_WARNING_RAW_OUTPUT=" + payload)
    else:
        environment["CI_PYINSTALLER_WARNING_RAW_OUTPUT"] = payload
    guarded = fixture.invoke(
        *arguments, environment=environment, early_environment=origin == "environment-e",
    )
    assert guarded.returncode == 2
    assert not marker.exists()
    foundation = fixture.root / "make/00-foundation.mk"
    source = foundation.read_text(encoding="utf-8")
    marker_line = "# Keep optional raw warning export literal before startup/export expansion.\n"
    start = source.index(marker_line)
    end = source.index("\n\n", start) + 2
    binding = source[start:end]
    literal = "$(value CI_PYINSTALLER_WARNING_RAW_OUTPUT)"
    assert binding.count(literal) == 1
    unguarded_binding = binding.replace(literal, "$(CI_PYINSTALLER_WARNING_RAW_OUTPUT)", 1)
    foundation.write_text(source.replace(binding, unguarded_binding, 1), encoding="utf-8")
    witness = fixture.invoke(
        *arguments, environment=environment, early_environment=origin == "environment-e",
    )
    assert marker.exists(), "same native input/payload/origin must execute without its literal boundary"
    marker.unlink()
    assert witness.returncode == 0, witness.stdout + witness.stderr
    assert not (fixture.root / "artifacts").exists()


def test_hosted_gate_runtime_diagnostics_retain_exact_native_evidence() -> None:
    from typing import cast

    import yaml

    workflow = cast(dict[str, object], yaml.safe_load(
        (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8"),
    ))
    jobs = cast(dict[str, object], workflow["jobs"])
    gate = cast(dict[str, object], jobs["gate"])
    steps = cast(list[dict[str, object]], gate["steps"])
    runtime = next(step for step in steps if step.get("name") == "Known runtime and handoff regression admission")
    collect = next(step for step in steps if step.get("name") == (
        "Collect failure diagnostics (gate, Python ${{ matrix.python-version }})"
    ))
    upload = next(step for step in steps if step.get("name") == (
        "Upload failure diagnostics (gate, Python ${{ matrix.python-version }})"
    ))
    run = str(runtime["run"])
    assert "RUN_WATCHED_PYTEST_TRACE=1" in shlex.split(run)
    assert "STALL_SECS=60 MAX_SECS=90" in run
    assert runtime.get("continue-on-error") is None
    assert collect["if"] == "failure()"
    assert upload["if"] == "always() && failure()"
    assert collect.get("continue-on-error") is None and upload.get("continue-on-error") is None
    assert steps.index(collect) + 1 == steps.index(upload)
    assert collect["env"] == {
        "UV_PROJECT_ENVIRONMENT": "${{ github.workspace }}/.venv",
        "VIRTUAL_ENV": "${{ github.workspace }}/.venv",
        "UV_NO_SYNC": "1", "UV_PYTHON": "${{ matrix.python-version }}",
        "UV_PYTHON_DOWNLOADS": "never",
    }
    body = str(collect["run"])
    identity = "runtime-admission-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.python-version }}"
    assert 'RUNTIME_ID="' + identity + '"' in body
    assert "make --no-print-directory observed-status OBSERVED_ROOT=.gate-logs/observed" in body
    assert "OBSERVED_LABEL=ci-runtime-regression-admission" in body
    assert body.count('RUN_ID="$RUNTIME_ID"') == 2
    assert 'test -s ".gate-logs/observed/ci-runtime-regression-admission/$RUNTIME_ID.pytest.jsonl"' in body
    assert "make --no-print-directory test-xdist-trace-summary" in body
    assert 'LOG=".gate-logs/observed/ci-runtime-regression-admission/$RUNTIME_ID.pytest.jsonl"' in body
    assert "TRACE_SUMMARY_TIMINGS=1 TRACE_SUMMARY_MEMORY=1" in body
    assert "status_rc=$?" in body and "summary_rc=$?" in body
    assert "no terminal proof" in body and "raw trace retained" in body
    assert "tee" in body and 'test -s "$DIAG_PATH"' in body
    assert upload["uses"] == "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f"
    options = cast(dict[str, object], upload["with"])
    assert options["name"] == (
        "failure-diag-gate-${{ matrix.python-version }}-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert options["include-hidden-files"] is True
    assert options["if-no-files-found"] == "error"
    assert options["retention-days"] == 3
    assert str(options["path"]).splitlines() == [
        "gate-failure-diagnostics-${{ matrix.python-version }}.log",
        ".github/workflows/build.yml",
        ".gate-logs/ci-runtime-regression-admission.log",
        ".gate-logs/ci-runtime-regression-admission-status.json",
        ".gate-logs/ci-runtime-regression-admission-trace-summary.json",
        ".gate-logs/observed/ci-runtime-regression-admission/" + identity + ".json",
        ".gate-logs/observed/ci-runtime-regression-admission/" + identity + ".pytest.jsonl",
    ]
