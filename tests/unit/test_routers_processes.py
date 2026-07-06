"""Unit tests for routers/processes.py — managed-process admin API.

Targets the handler bodies in ``routers/processes.py`` (the list / signal /
stats routes and the ``_collect_stats`` helper) that ship at ~18.5% coverage.

All psutil interaction is mocked via ``unittest.mock.patch`` — NO real OS
process is ever spawned, inspected, or signalled by this suite. The registry
singleton is replaced with a controllable stub so handler behavior is exercised
without touching the live process table.

Test surface (per task spec):
  1. GET  /admin/processes                — list returns 200 + schema.
  2. GET  /admin/processes/{pid}/stats    — 200 happy path / 404 unmanaged.
  3. POST /admin/processes/{pid}/signal   — 200 happy path (mocked registry.signal).
  4. PSK auth                              — missing/wrong Bearer -> 401.
  5. psutil.NoSuchProcess                  -> 404 with reason.
  6. psutil.AccessDenied                   -> 403 with reason.
"""

from __future__ import annotations

import hmac
import signal as _signal
from typing import Any
from unittest.mock import MagicMock, patch

import psutil
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.process.registry import ManagedProcess, ProcessRegistry
from general_ludd.routers.processes import register

_PSK = "processes-router-unit-test-psk"


# ---------------------------------------------------------------------------
# App / client fixtures
# ---------------------------------------------------------------------------


def _build_app(*, with_psk: bool) -> FastAPI:
    """Build a minimal FastAPI app with only the processes router registered.

    When ``with_psk`` is True, an HTTP middleware mirrors the daemon's PSK gate
    so the auth behavior is exercisable without wiring the full daemon.
    """
    app = FastAPI()
    register(app, {})

    if with_psk:

        @app.middleware("http")
        async def _psk_gate(request: Any, call_next: Any) -> Any:
            auth = request.headers.get("Authorization", "")
            token = (
                auth.removeprefix("Bearer ").strip()
                if auth.startswith("Bearer ")
                else ""
            )
            if not token or not hmac.compare_digest(token, _PSK):
                return JSONResponse(
                    status_code=401, content={"detail": "unauthorized"}
                )
            return await call_next(request)

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app(with_psk=False))


@pytest.fixture
def psk_client() -> TestClient:
    return TestClient(_build_app(with_psk=True))


# ---------------------------------------------------------------------------
# Registry stub helpers
# ---------------------------------------------------------------------------


def _record(pid: int = 4242) -> ManagedProcess:
    """A synthetic managed-process record (no real OS process is created)."""
    return ManagedProcess(
        pid=pid,
        command=["sleep", "100"],
        pgid=None,
        job_id="job-unit",
        project_id="proj-unit",
        origin="unit_test",
        registered_at=1000.0,
        create_time=1000.0,
    )


def _registry_with(pid: int = 4242) -> ProcessRegistry:
    """A ProcessRegistry with a synthetic record pre-loaded into its map."""
    reg = ProcessRegistry()
    reg._procs[pid] = _record(pid)
    return reg


def _wire_psutil_process_mock() -> MagicMock:
    """Build a ``psutil.Process`` mock returning a plausible snapshot.

    ``create_time`` is set to match the synthetic record so the registry's
    identity check passes and the handler reaches ``_collect_stats``.
    """
    mock_proc = MagicMock()
    mock_proc.create_time.return_value = 1000.0
    mock_proc.cpu_percent.return_value = 5.0
    mock_proc.memory_info.return_value = MagicMock(rss=2048, vms=4096)
    mock_proc.io_counters.return_value = MagicMock(
        read_bytes=10, write_bytes=20, read_count=1, write_count=2
    )
    mock_proc.num_fds.return_value = 4
    mock_proc.num_ctx_switches.return_value = MagicMock(
        voluntary=7, involuntary=9
    )
    mock_proc.num_threads.return_value = 3
    mock_proc.status.return_value = "running"
    mock_proc.open_files.return_value = []
    return mock_proc


# ---------------------------------------------------------------------------
# 1. GET /admin/processes — list
# ---------------------------------------------------------------------------


def test_list_processes_returns_200_and_schema(client: TestClient) -> None:
    """List endpoint returns the canonical {processes, count} envelope.

    Each row carries every ManagedProcess field plus the router-added ``alive``
    flag. The registry and psutil are both mocked so no real pid is touched.
    """
    pid = 4242
    reg = _registry_with(pid=pid)
    with (
        patch(
            "general_ludd.routers.processes.default_registry",
            return_value=reg,
        ),
        patch("psutil.Process") as mock_proc_cls,
    ):
        mock_proc_cls.return_value = _wire_psutil_process_mock()
        resp = client.get("/admin/processes")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"processes", "count"}
    assert body["count"] == 1
    row = body["processes"][0]
    for key in (
        "pid",
        "command",
        "pgid",
        "job_id",
        "project_id",
        "origin",
        "registered_at",
        "create_time",
        "alive",
    ):
        assert key in row, f"missing field in row: {key}"
    assert row["pid"] == pid
    assert row["origin"] == "unit_test"
    assert row["alive"] is True


def test_list_processes_empty_registry(client: TestClient) -> None:
    """An empty registry yields count=0 + empty list, still 200."""
    reg = ProcessRegistry()
    with patch(
        "general_ludd.routers.processes.default_registry",
        return_value=reg,
    ):
        resp = client.get("/admin/processes")
    assert resp.status_code == 200
    assert resp.json() == {"processes": [], "count": 0}


# ---------------------------------------------------------------------------
# 2. GET /admin/processes/{pid}/stats — single-process snapshot
# ---------------------------------------------------------------------------


def test_stats_returns_200_for_managed_pid(client: TestClient) -> None:
    """Stats endpoint returns the full snapshot for a live managed pid."""
    pid = 4242
    reg = _registry_with(pid=pid)
    with (
        patch(
            "general_ludd.routers.processes.default_registry",
            return_value=reg,
        ),
        patch("psutil.Process") as mock_proc_cls,
    ):
        mock_proc_cls.return_value = _wire_psutil_process_mock()
        resp = client.get(f"/admin/processes/{pid}/stats")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pid"] == pid
    assert body["cpu_percent"] == 5.0
    assert body["memory"] == {"rss": 2048, "vms": 4096}
    assert body["io"] == {
        "read_bytes": 10,
        "write_bytes": 20,
        "read_count": 1,
        "write_count": 2,
    }
    assert body["num_fds"] == 4
    assert body["num_threads"] == 3
    assert body["num_ctx_switches"] == {"voluntary": 7, "involuntary": 9}
    assert body["status"] == "running"
    assert isinstance(body["open_files"], int)
    assert isinstance(body["locks"], list)


def test_stats_returns_404_for_unmanaged_pid(client: TestClient) -> None:
    """A pid that is not in the registry is refused with 404 (confinement)."""
    reg = ProcessRegistry()  # empty
    with patch(
        "general_ludd.routers.processes.default_registry",
        return_value=reg,
    ):
        resp = client.get("/admin/processes/99999/stats")
    assert resp.status_code == 404
    assert "not a live gludd-managed process" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 3. POST /admin/processes/{pid}/signal — signal delivery
# ---------------------------------------------------------------------------


def test_signal_returns_expected_response(client: TestClient) -> None:
    """Signal happy path returns the canonical {ok, pid, signal, group} body.

    ``registry.signal`` is a no-op MagicMock so no real signal is delivered;
    ``resolve_signal`` is stubbed to return SIGTERM's signum.
    """
    pid = 4242
    reg = MagicMock()
    reg.resolve_signal.return_value = int(_signal.SIGTERM)
    reg.signal.return_value = None
    with patch(
        "general_ludd.routers.processes.default_registry",
        return_value=reg,
    ):
        resp = client.post(
            f"/admin/processes/{pid}/signal",
            json={"signal": "SIGTERM", "group": False},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "pid": pid,
        "signal": "SIGTERM",
        "group": False,
    }
    # Confirm the registry was actually invoked through asyncio.to_thread.
    reg.resolve_signal.assert_called_once_with("SIGTERM")
    reg.signal.assert_called_once_with(pid, int(_signal.SIGTERM), group=False)


def test_signal_unmanaged_pid_returns_404(client: TestClient) -> None:
    """A signal for a pid the registry does not manage is surfaced as 404.

    The registry raises ProcessRegistryError with the canonical refusal
    substring; the router maps that to 404.
    """
    from general_ludd.process.registry import ProcessRegistryError

    pid = 99999
    reg = MagicMock()
    reg.resolve_signal.return_value = int(_signal.SIGTERM)
    reg.signal.side_effect = ProcessRegistryError(
        f"refusing to signal pid {pid}: not a gludd-managed process"
    )
    with patch(
        "general_ludd.routers.processes.default_registry",
        return_value=reg,
    ):
        resp = client.post(
            f"/admin/processes/{pid}/signal",
            json={"signal": "SIGTERM"},
        )

    assert resp.status_code == 404
    assert "not a gludd-managed process" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 4. PSK auth gate (missing / wrong / correct Bearer token)
# ---------------------------------------------------------------------------


def test_psk_missing_bearer_returns_401(psk_client: TestClient) -> None:
    """A request with no Authorization header is refused before the handler."""
    resp = psk_client.get("/admin/processes")
    assert resp.status_code == 401


def test_psk_wrong_bearer_returns_401(psk_client: TestClient) -> None:
    """A request whose Bearer token does not match the configured PSK is 401."""
    resp = psk_client.get(
        "/admin/processes",
        headers={"Authorization": "Bearer definitely-not-the-psk"},
    )
    assert resp.status_code == 401


def test_psk_correct_bearer_passes_gate(psk_client: TestClient) -> None:
    """The correct PSK routes through to the handler (200 with empty body)."""
    reg = ProcessRegistry()
    with patch(
        "general_ludd.routers.processes.default_registry",
        return_value=reg,
    ):
        resp = psk_client.get(
            "/admin/processes",
            headers={"Authorization": f"Bearer {_PSK}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"processes": [], "count": 0}


# ---------------------------------------------------------------------------
# 5. psutil error path — NoSuchProcess -> 404 with reason
# ---------------------------------------------------------------------------


def test_stats_no_such_process_returns_404(client: TestClient) -> None:
    """If psutil reports the pid is gone mid-snapshot, the router returns 404.

    The pid IS registered and passes the identity pre-check (registry mocked
    at the boundary); the failure happens inside ``_collect_stats``
    (psutil.Process(pid) raises), so the exception is caught and surfaced
    with a reason.
    """
    pid = 4242
    reg = MagicMock()
    reg.get.return_value = _record(pid)
    reg.is_alive.return_value = True
    with (
        patch(
            "general_ludd.routers.processes.default_registry",
            return_value=reg,
        ),
        patch(
            "general_ludd.routers.processes._collect_stats",
            side_effect=psutil.NoSuchProcess(pid),
        ),
    ):
        resp = client.get(f"/admin/processes/{pid}/stats")

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert str(pid) in detail


# ---------------------------------------------------------------------------
# 6. psutil error path — AccessDenied -> 403 with reason
# ---------------------------------------------------------------------------


def test_stats_access_denied_returns_403(client: TestClient) -> None:
    """A psutil AccessDenied mid-snapshot is surfaced as 403 (not 404/500).

    Permission denial is a distinct failure mode from absence: the pid exists
    and is managed, but the operator cannot inspect it. The router must map
    that to 403 so callers can distinguish the two.
    """
    pid = 4242
    reg = MagicMock()
    reg.get.return_value = _record(pid)
    reg.is_alive.return_value = True
    with (
        patch(
            "general_ludd.routers.processes.default_registry",
            return_value=reg,
        ),
        patch(
            "general_ludd.routers.processes._collect_stats",
            side_effect=psutil.AccessDenied(pid),
        ),
    ):
        resp = client.get(f"/admin/processes/{pid}/stats")

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert str(pid) in detail
