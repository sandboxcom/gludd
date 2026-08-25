"""Contracts for bounded hosted-failure diagnostics and Python-version replay."""

from __future__ import annotations

import json
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
