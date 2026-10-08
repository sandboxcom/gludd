"""Runtime lifecycle coverage for the sealed managed-process registry."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from general_ludd.ansible.core_runner import AnsibleResult, CoreAnsibleRunner
from general_ludd.process import registry as registry_module
from general_ludd.process.registry import ProcessRegistry, ProcessRegistryError
from general_ludd.routers import processes as process_routes


def _spawn_sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )


@pytest.mark.asyncio
async def test_sealed_runtime_registry_tracks_real_owned_child_and_reaps_after_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ProcessRegistry(max_records=4)
    registry.seal()
    app = FastAPI()
    process_routes.register(app, {})
    monkeypatch.setattr(process_routes, "default_registry", lambda: registry)
    process = _spawn_sleeper()
    lease = None

    try:
        lease = registry.lease_owned_process(
            process,
            command=["python", "-c", "sleep"],
            job_id="job-integration",
            project_id="project-integration",
            origin="integration_test",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            live = await client.get("/admin/processes")

            assert live.status_code == 200
            assert live.json()["count"] == 1
            assert live.json()["processes"][0]["pid"] == process.pid
            assert live.json()["processes"][0]["alive"] is True

            process.terminate()
            process.wait(timeout=5)

            finished = await client.get("/admin/processes")
            metrics = await client.get("/admin/processes/metrics")

        assert finished.status_code == 200
        assert finished.json() == {"processes": [], "count": 0}
        assert lease.release() is False
        assert metrics.status_code == 200
        assert metrics.json() == {
            "capacity": 4,
            "current": 0,
            "owner_leases_acquired_total": 1,
            "owner_leases_released_total": 0,
            "stale_records_pruned_total": 1,
            "capacity_rejections_total": 0,
        }
    finally:
        if lease is not None:
            lease.release()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@dataclass(frozen=True)
class _PidHandle:
    pid: int


@dataclass
class _PendingHandle:
    pid: int | None


def test_owner_lease_is_pid_reuse_safe_and_capacity_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = {101: 10.0, 202: 20.0}
    monkeypatch.setattr(
        registry_module,
        "_read_create_time",
        lambda pid: identities.get(pid),
    )
    registry = ProcessRegistry(max_records=1)
    registry.seal()

    first = registry.lease_owned_process(_PidHandle(101), command=["first"])
    with pytest.raises(ProcessRegistryError, match="capacity"):
        registry.lease_owned_process(_PidHandle(202), command=["second"])

    identities[101] = 11.0
    replacement = registry.lease_owned_process(
        _PidHandle(101),
        command=["replacement"],
    )

    assert first.release() is False
    replacement_record = registry.get(101)
    assert replacement_record is not None
    assert replacement_record.create_time == 11.0
    assert replacement.release() is True
    assert registry.metrics() == {
        "capacity": 1,
        "current": 0,
        "owner_leases_acquired_total": 2,
        "owner_leases_released_total": 1,
        "stale_records_pruned_total": 1,
        "capacity_rejections_total": 1,
    }


def test_owner_lease_fails_closed_for_invalid_or_changing_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ProcessRegistry()
    registry.seal()

    with pytest.raises(ProcessRegistryError, match="positive integer pid"):
        registry.lease_owned_process(_PendingHandle(None), command=["pending"])

    monkeypatch.setattr(registry_module, "_read_create_time", lambda _pid: None)
    with pytest.raises(ProcessRegistryError, match="cannot verify"):
        registry.lease_owned_process(_PidHandle(404), command=["unverifiable"])

    identities = iter((10.0, 11.0))
    monkeypatch.setattr(
        registry_module,
        "_read_create_time",
        lambda _pid: next(identities),
    )
    with pytest.raises(ProcessRegistryError, match="changed identity"):
        registry.lease_owned_process(
            _PidHandle(405),
            command=["identity-race"],
            pgid=405,
        )


def test_owner_lease_context_rejects_duplicate_and_releases_exact_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry_module,
        "_read_create_time",
        lambda _pid: 20.0,
    )
    registry = ProcessRegistry(max_records=2)
    registry.seal()
    lease = registry.lease_owned_process(
        _PidHandle(505),
        command="context-child",
        pgid=505,
    )

    assert lease.pid == 505
    with pytest.raises(ProcessRegistryError, match="already managed"):
        registry.lease_owned_process(
            _PidHandle(505),
            command=["duplicate"],
            pgid=505,
        )
    with lease as held:
        assert held is lease

    assert registry.is_managed(505) is False
    assert lease.release() is False
    assert registry.metrics()["owner_leases_released_total"] == 1


def test_registry_capacity_configuration_and_unrestricted_path_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match="integer"):
        ProcessRegistry(max_records=True)
    with pytest.raises(ValueError, match="at least 1"):
        ProcessRegistry(max_records=0)

    identities = {601: 60.0, 602: 61.0}
    monkeypatch.setattr(
        registry_module,
        "_read_create_time",
        lambda pid: identities.get(pid),
    )
    registry = ProcessRegistry(max_records=1)
    registry.register(601, ["first"], pgid=601)

    with pytest.raises(ProcessRegistryError, match="capacity"):
        registry.register(602, ["second"], pgid=602)

    assert registry.metrics()["capacity_rejections_total"] == 1


def test_core_runner_releases_owner_lease_in_finally() -> None:
    queue = MagicMock()
    queue.get_nowait.return_value = (
        "ok",
        AnsibleResult(status="successful", rc=0).model_dump(),
    )
    process = MagicMock()
    process.pid = 303
    process.is_alive.return_value = False
    context = MagicMock()
    context.Queue.return_value = queue
    context.Process.return_value = process
    registry = MagicMock()
    lease = MagicMock()
    registry.lease_owned_process.return_value = lease

    with (
        patch(
            "general_ludd.ansible.core_runner.multiprocessing.get_context",
            return_value=context,
        ),
        patch(
            "general_ludd.process.registry.default_registry",
            return_value=registry,
        ),
    ):
        result = CoreAnsibleRunner()._run_with_timeout(
            timeout=1.0,
            playbook_path="/tmp/lease.yml",
        )

    assert result.status == "successful"
    registry.lease_owned_process.assert_called_once_with(
        process,
        command=["ansible-playbook-runner", "/tmp/lease.yml"],
        origin="ansible_runner",
    )
    lease.release.assert_called_once_with()
