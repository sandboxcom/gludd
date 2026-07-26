"""Regression tests for project-scoped resource evidence in active-work-status."""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
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


def test_concurrent_project_resource_leases_are_unique_and_bounded() -> None:
    """Concurrent project snapshots expose one owner per namespaced lease."""

    namespaces = ("project-model-alpha", "project-model-beta")
    with ThreadPoolExecutor(max_workers=len(namespaces)) as pool:
        payloads = list(pool.map(lambda name: _status(namespace=name), namespaces))

    observed_pairs: set[tuple[str, str]] = set()
    for payload in payloads:
        evidence = payload["resource_observability"]
        assert isinstance(evidence, dict)
        namespace = evidence["project_namespace"]
        root = evidence["resource_root"]
        owner = evidence["lease_owner"]
        inventory = evidence["lease_inventory"]
        worker_count = evidence["worker_count"]
        worker_limit = evidence["worker_limit"]

        assert isinstance(namespace, str)
        assert isinstance(root, str)
        assert isinstance(owner, str)
        assert isinstance(inventory, list)
        assert isinstance(worker_count, int)
        assert isinstance(worker_limit, int)
        assert worker_count <= worker_limit

        resources = []
        for record in inventory:
            assert isinstance(record, dict)
            resource = record["resource"]
            path = record["path"]
            record_owner = record["owner"]
            assert isinstance(resource, str)
            assert isinstance(path, str)
            assert isinstance(record_owner, str)
            assert path.startswith(root)
            assert record_owner == owner
            resources.append(resource)
            observed_pairs.add((namespace, resource))

        assert {"project", "model", "searx", "terraform"}.issubset(resources)
        assert len(resources) == len(set(resources))

    assert len(observed_pairs) == sum(
        len(payload["resource_observability"]["lease_inventory"])
        for payload in payloads
    )
