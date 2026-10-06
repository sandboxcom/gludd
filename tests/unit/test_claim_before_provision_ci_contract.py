"""Pin the credential-free claim-before-provision CI acceptance job."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/build.yml"
JOB_NAME = "claim-before-provision-acceptance"


def _workflow() -> dict[str, Any]:
    """Load the build workflow as a mapping."""
    parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _job_commands(job: dict[str, Any]) -> str:
    """Join every run block in a workflow job."""
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return "\n".join(
        str(step.get("run", "")) for step in steps if isinstance(step, dict)
    )


def test_claim_before_provision_job_is_bounded_hermetic_and_warning_strict() -> None:
    """CI exercises local/fake-Azure ownership without credentials or spend."""
    workflow = _workflow()
    job = workflow["jobs"][JOB_NAME]

    assert job["needs"] == "version"
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 15
    assert job["permissions"] == {"contents": "read"}

    commands = _job_commands(job)
    for test_file in (
        "tests/unit/test_claim_before_provision_acceptance.py",
        "tests/unit/test_db_session_dispatch.py",
        "tests/unit/test_tick_session.py",
        "tests/unit/test_e10_db_session_dispatch.py",
        "tests/unit/test_todo_compute_demand_lifecycle.py",
        "tests/e2e/test_self_improve_private_policy_e2e.py",
    ):
        assert test_file in commands
    assert "make sync" in commands
    assert "make test-files" in commands
    assert "PYTEST_ARGS='-W error'" in commands

    serialized = yaml.safe_dump(job)
    for forbidden in (
        "secrets.",
        "az login",
        "terraform apply",
        "tofu apply",
        "LIVE=1",
        "continue-on-error: true",
    ):
        assert forbidden not in serialized

    acceptance_step = next(
        step
        for step in job["steps"]
        if isinstance(step, dict) and "make test-files" in str(step.get("run", ""))
    )
    env = acceptance_step["env"]
    assert env["AZURE_CLIENT_ID"] == ""
    assert env["AZURE_TENANT_ID"] == ""
    assert env["AZURE_CLIENT_SECRET"] == ""
    assert env["AZURE_SUBSCRIPTION_ID"] == ""
    assert env["AZURE_CONTAINERAPP_LIVE_PROOF_LIVE"] == "0"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:9"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:9"
    assert env["ALL_PROXY"] == "http://127.0.0.1:9"
    assert env["NO_PROXY"] == ""


def test_release_requires_claim_before_provision_acceptance() -> None:
    """A release cannot bypass the dedicated ownership acceptance job."""
    release = _workflow()["jobs"]["release"]
    needs = release["needs"]
    assert isinstance(needs, list)
    assert JOB_NAME in needs
    assert (
        "needs['claim-before-provision-acceptance'].result == 'success'"
        in str(release["if"])
    )
