"""Contracts for bounded hosted-failure diagnostics and Python-version replay."""

from __future__ import annotations

import json
import subprocess
from configparser import ConfigParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _target_block(source: str, name: str) -> str:
    marker = f"{name}:"
    assert marker in source
    return source.split(marker, 1)[1].split("\n\n", 1)[0]


def test_ci_job_failure_context_is_authenticated_bounded_and_fail_closed() -> None:
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = _target_block(source, "ci-job-failure-context")

    assert "gh run view -R sandboxcom/gludd" in block
    assert "--json jobs" in block
    assert "--log --job=" in block
    assert ".gate-logs/ci-job-" in block
    assert "scripts/ci_shards_log_context.py" in block
    for variable in (
        "$(RUN)",
        "$(JOB)",
        "$(PATTERN)",
        "$(BEFORE)",
        "$(AFTER)",
        "$(CI_JOB_CONTEXT_VALIDATE_ONLY)",
    ):
        assert variable in block
    assert "|| true" not in block
    assert "2>/dev/null" not in block


def test_ci_artifact_download_is_run_bound_confined_and_atomic() -> None:
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
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
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
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
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = _target_block(source, "ci-coverage-gap-plan")

    assert "scripts/resource_arbiter.py root" in block
    assert "scripts/coverage_missing_lines.py" in block
    assert "coverage-data.json" in block
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
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = _target_block(source, "test-specific-pyver")

    assert '$(UV) sync --python "$(PYTHON_VERSION)"' in block
    assert '$(UV) run --python "$(PYTHON_VERSION)" python -m pytest $(TESTFILE)' in block
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
            "CI_JOB_CONTEXT_VALIDATE_ONLY",
        ],
        "behavior": (
            "make ci-job-failure-context RUN=1 JOB=1 PATTERN=FAILED "
            "BEFORE=2 AFTER=4 CI_JOB_CONTEXT_VALIDATE_ONLY=1"
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
