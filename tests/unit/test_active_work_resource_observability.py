"""Regression tests for project-scoped resource evidence in active-work-status."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _status(*, namespace: str) -> dict[str, object]:
    env = os.environ.copy()
    env["GLUDD_PROJECT_NAMESPACE"] = namespace
    result = subprocess.run(
        ["make", "active-work-status"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


def test_active_status_reports_project_and_lease_identity() -> None:
    payload = _status(namespace="e2e-project-alpha")
    evidence = payload["resource_observability"]

    assert isinstance(evidence, dict)
    assert evidence["project_namespace"] == "e2e-project-alpha"
    assert isinstance(evidence["resource_root"], str)
    assert evidence["resource_root"].endswith("/gludd-resources/e2e-project-alpha")
    assert isinstance(evidence["lease_owner"], str)
    assert evidence["lease_owner"].startswith("pid:")


def test_active_status_leases_are_namespaced_to_the_project() -> None:
    payload = _status(namespace="e2e-project-beta")
    evidence = payload["resource_observability"]

    assert isinstance(evidence, dict)
    leases = evidence["leases"]
    assert isinstance(leases, list)
    for lease in leases:
        assert isinstance(lease, str)
        assert lease.startswith(str(evidence["resource_root"]))


def test_active_status_worker_count_is_bounded() -> None:
    payload = _status(namespace="e2e-project-bounded")
    evidence = payload["resource_observability"]

    assert isinstance(evidence, dict)
    worker_count = evidence["worker_count"]
    worker_limit = evidence["worker_limit"]
    assert isinstance(worker_count, int)
    assert isinstance(worker_limit, int)
    assert worker_count >= 0
    assert worker_limit > 0
    assert worker_count <= worker_limit
