"""Mock-driven coverage for the uncovered branches of routers/processes.py.

test_processes_router.py exercises the happy path (list/stats/signal against a
real short-lived child) plus the unmanaged-PID confinement (404). The branches
it does NOT reach are:

* signal endpoint's ProcessRegistryError status-code mapping (409 identity /
  disappeared, 400 allow-list / generic, 500 unexpected exception)
* stats endpoint's exception translation (403 AccessDenied, 404 NoSuchProcess,
  404 generic Exception)
* the per-field try/except degradation inside _collect_stats (cpu_percent,
  io_counters, num_fds, num_ctx_switches, open_files)
* _read_proc_locks parsing / absence behavior

Every test here uses unittest.mock — no real process is signalled, listed, or
inspected.
"""

from __future__ import annotations

import signal as _signal
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app
from general_ludd.process.registry import (
    ManagedProcess,
    ProcessRegistry,
    ProcessRegistryError,
)
from general_ludd.routers import processes as processes_router


@pytest.fixture
def app() -> Any:
    return create_daemon_app(tick_interval=0.01)


@pytest.fixture
def fake_registry() -> Iterator[MagicMock]:
    """Replace the router's default_registry() with a controllable MagicMock."""
    reg = MagicMock(spec=ProcessRegistry)
    with patch.object(processes_router, "default_registry", return_value=reg):
        yield reg


@pytest.fixture
def managed_record() -> ManagedProcess:
    return ManagedProcess(
        pid=999999,
        command=["fake", "--flag"],
        pgid=999998,
        job_id="job-1",
        project_id="proj-1",
        origin="test",
        registered_at=1000.0,
        create_time=999.0,
    )


async def _get(app: Any, path: str) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def _post(app: Any, path: str, **kwargs: Any) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, **kwargs)


# ===== list endpoint ===================================================

@pytest.mark.asyncio
async def test_list_empty_returns_canonical_schema(app: Any, fake_registry: MagicMock):
    fake_registry.list.return_value = []
    resp = await _get(app, "/admin/processes")
    assert resp.status_code == 200
    assert resp.json() == {"processes": [], "count": 0}


@pytest.mark.asyncio
async def test_list_includes_full_record_schema(
    app: Any, fake_registry: MagicMock, managed_record: ManagedProcess
):
    fake_registry.list.return_value = [managed_record]
    fake_registry.is_alive.return_value = True
    resp = await _get(app, "/admin/processes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    rec = data["processes"][0]
    assert set(rec) == {
        "pid",
        "command",
        "pgid",
        "job_id",
        "project_id",
        "origin",
        "registered_at",
        "create_time",
        "alive",
    }
    assert rec["pid"] == 999999
    assert rec["command"] == ["fake", "--flag"]
    assert rec["pgid"] == 999998
    assert rec["job_id"] == "job-1"
    assert rec["project_id"] == "proj-1"
    assert rec["origin"] == "test"
    assert rec["registered_at"] == 1000.0
    assert rec["create_time"] == 999.0
    assert rec["alive"] is True


@pytest.mark.asyncio
async def test_list_marks_dead_record_not_alive(
    app: Any, fake_registry: MagicMock, managed_record: ManagedProcess
):
    fake_registry.list.return_value = [managed_record]
    fake_registry.is_alive.return_value = False
    resp = await _get(app, "/admin/processes")
    assert resp.status_code == 200
    assert resp.json()["processes"][0]["alive"] is False


# ===== stats endpoint confinement =====================================

@pytest.mark.asyncio
async def test_stats_unmanaged_pid_returns_404_with_reason(
    app: Any, fake_registry: MagicMock
):
    fake_registry.get.return_value = None
    resp = await _get(app, "/admin/processes/4242/stats")
    assert resp.status_code == 404
    assert "not a live gludd-managed process" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_stats_registered_but_dead_pid_returns_404(
    app: Any, fake_registry: MagicMock, managed_record: ManagedProcess
):
    fake_registry.get.return_value = managed_record
    fake_registry.is_alive.return_value = False
    resp = await _get(app, "/admin/processes/999999/stats")
    assert resp.status_code == 404
    assert "not a live gludd-managed process" in resp.json()["detail"]


# ===== stats endpoint exception translation ===========================

@pytest.mark.asyncio
async def test_stats_access_denied_returns_403(
    app: Any, fake_registry: MagicMock, managed_record: ManagedProcess
):
    import psutil

    fake_registry.get.return_value = managed_record
    fake_registry.is_alive.return_value = True
    with patch.object(processes_router, "_collect_stats") as mock_collect:
        mock_collect.side_effect = psutil.AccessDenied()
        resp = await _get(app, "/admin/processes/999999/stats")
    assert resp.status_code == 403
    assert "permission denied" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_stats_no_such_process_returns_404(
    app: Any, fake_registry: MagicMock, managed_record: ManagedProcess
):
    import psutil

    fake_registry.get.return_value = managed_record
    fake_registry.is_alive.return_value = True
    with patch.object(processes_router, "_collect_stats") as mock_collect:
        mock_collect.side_effect = psutil.NoSuchProcess(999999)
        resp = await _get(app, "/admin/processes/999999/stats")
    assert resp.status_code == 404
    assert "no longer available" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_stats_generic_exception_returns_404(
    app: Any, fake_registry: MagicMock, managed_record: ManagedProcess
):
    fake_registry.get.return_value = managed_record
    fake_registry.is_alive.return_value = True
    with patch.object(processes_router, "_collect_stats") as mock_collect:
        mock_collect.side_effect = RuntimeError("unexpected boom")
        resp = await _get(app, "/admin/processes/999999/stats")
    assert resp.status_code == 404
    assert "no longer available" in resp.json()["detail"]


# ===== signal endpoint happy path =====================================

@pytest.mark.asyncio
async def test_signal_managed_pid_returns_200(
    app: Any, fake_registry: MagicMock
):
    fake_registry.resolve_signal.return_value = int(_signal.SIGTERM)
    fake_registry.signal.return_value = None
    resp = await _post(
        app,
        "/admin/processes/999999/signal",
        json={"signal": "SIGTERM", "group": False},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "pid": 999999,
        "signal": "SIGTERM",
        "group": False,
    }


@pytest.mark.asyncio
async def test_signal_group_true_echoed_in_response(
    app: Any, fake_registry: MagicMock
):
    fake_registry.resolve_signal.return_value = int(_signal.SIGUSR1)
    fake_registry.signal.return_value = None
    resp = await _post(
        app,
        "/admin/processes/999999/signal",
        json={"signal": "SIGUSR1", "group": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["signal"] == "SIGUSR1"
    assert body["group"] is True


@pytest.mark.asyncio
async def test_signal_defaults_when_body_omitted(
    app: Any, fake_registry: MagicMock
):
    fake_registry.resolve_signal.return_value = int(_signal.SIGTERM)
    fake_registry.signal.return_value = None
    resp = await _post(
        app,
        "/admin/processes/999999/signal",
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["signal"] == "SIGTERM"
    assert body["group"] is False


# ===== signal endpoint status-code mapping ============================

@pytest.mark.asyncio
async def test_signal_unmanaged_pid_returns_404_with_reason(
    app: Any, fake_registry: MagicMock
):
    fake_registry.resolve_signal.return_value = int(_signal.SIGTERM)
    fake_registry.signal.side_effect = ProcessRegistryError(
        "refusing to signal pid 4242: not a gludd-managed process"
    )
    resp = await _post(
        app,
        "/admin/processes/4242/signal",
        json={"signal": "SIGTERM"},
    )
    assert resp.status_code == 404
    assert "not a gludd-managed process" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_signal_identity_check_failed_returns_409(
    app: Any, fake_registry: MagicMock
):
    fake_registry.resolve_signal.return_value = int(_signal.SIGTERM)
    fake_registry.signal.side_effect = ProcessRegistryError(
        "refusing to signal pid 999999: process is gone or its PID was "
        "reused by a different process (identity check failed)"
    )
    resp = await _post(
        app,
        "/admin/processes/999999/signal",
        json={"signal": "SIGTERM"},
    )
    assert resp.status_code == 409
    assert "identity check failed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_signal_process_disappeared_returns_409(
    app: Any, fake_registry: MagicMock
):
    fake_registry.resolve_signal.return_value = int(_signal.SIGTERM)
    fake_registry.signal.side_effect = ProcessRegistryError(
        "process 999999 disappeared before the signal was delivered"
    )
    resp = await _post(
        app,
        "/admin/processes/999999/signal",
        json={"signal": "SIGTERM"},
    )
    assert resp.status_code == 409
    assert "disappeared" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_signal_allowlist_violation_returns_400(
    app: Any, fake_registry: MagicMock
):
    fake_registry.resolve_signal.side_effect = ProcessRegistryError(
        "signal 'SIGBOMB' is not in the allow-list"
    )
    resp = await _post(
        app,
        "/admin/processes/999999/signal",
        json={"signal": "SIGBOMB"},
    )
    assert resp.status_code == 400
    assert "allow-list" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_signal_permission_denied_returns_400(
    app: Any, fake_registry: MagicMock
):
    """Registry's PermissionError message falls to the else branch (400).

    The signal endpoint maps known ProcessRegistryError substrings to specific
    status codes; an unmapped refusal (including 'not permitted') lands in the
    generic 400 bucket. The 403 mapping exists only on the stats endpoint.
    """
    fake_registry.resolve_signal.return_value = int(_signal.SIGTERM)
    fake_registry.signal.side_effect = ProcessRegistryError(
        "not permitted to signal process 999999"
    )
    resp = await _post(
        app,
        "/admin/processes/999999/signal",
        json={"signal": "SIGTERM"},
    )
    assert resp.status_code == 400
    assert "not permitted" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_signal_unexpected_exception_returns_500(
    app: Any, fake_registry: MagicMock
):
    fake_registry.resolve_signal.return_value = int(_signal.SIGTERM)
    fake_registry.signal.side_effect = RuntimeError("kernel on fire")
    resp = await _post(
        app,
        "/admin/processes/999999/signal",
        json={"signal": "SIGTERM"},
    )
    assert resp.status_code == 500
    assert resp.json()["detail"] == "internal error delivering signal"


# ===== _collect_stats direct unit tests ===============================

def _build_proc_mock(**overrides: Any) -> MagicMock:
    """Build a MagicMock standing in for psutil.Process with configurable fields."""
    proc = MagicMock()
    proc.cpu_percent.return_value = overrides.get("cpu_percent", 12.5)

    mem = MagicMock()
    mem.rss = overrides.get("rss", 4096)
    mem.vms = overrides.get("vms", 8192)
    proc.memory_info.return_value = mem

    proc.num_threads.return_value = overrides.get("num_threads", 3)
    proc.status.return_value = overrides.get("status", "running")

    if "io_counters" in overrides:
        proc.io_counters.return_value = overrides["io_counters"]
    if "num_fds" in overrides:
        proc.num_fds.return_value = overrides["num_fds"]
    if "num_ctx_switches" in overrides:
        proc.num_ctx_switches.return_value = overrides["num_ctx_switches"]
    if "open_files" in overrides:
        proc.open_files.return_value = overrides["open_files"]
    return proc


def _patch_collect_deps(proc: MagicMock) -> Any:
    """Context-manager stack mocking psutil.Process and _read_proc_locks."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("psutil.Process", return_value=proc))
    stack.enter_context(
        patch.object(processes_router, "_read_proc_locks", return_value=[])
    )
    return stack


def test_collect_stats_full_schema_with_all_fields():
    ioc = MagicMock(read_bytes=100, write_bytes=200, read_count=10, write_count=20)
    ctx = MagicMock(voluntary=5, involuntary=2)
    proc = _build_proc_mock(
        cpu_percent=42.0,
        rss=111,
        vms=222,
        num_threads=4,
        status="sleeping",
        io_counters=ioc,
        num_fds=7,
        num_ctx_switches=ctx,
        open_files=[MagicMock(), MagicMock(), MagicMock()],
    )
    with _patch_collect_deps(proc):
        result = processes_router._collect_stats(999999)
    assert result["pid"] == 999999
    assert result["cpu_percent"] == 42.0
    assert result["memory"] == {"rss": 111, "vms": 222}
    assert result["io"] == {
        "read_bytes": 100,
        "write_bytes": 200,
        "read_count": 10,
        "write_count": 20,
    }
    assert result["num_fds"] == 7
    assert result["num_threads"] == 4
    assert result["num_ctx_switches"] == {"voluntary": 5, "involuntary": 2}
    assert result["status"] == "sleeping"
    assert result["open_files"] == 3
    assert result["locks"] == []


def test_collect_stats_cpu_percent_exception_defaults_to_zero():
    proc = _build_proc_mock()
    proc.cpu_percent.side_effect = RuntimeError("not permitted")
    with _patch_collect_deps(proc):
        result = processes_router._collect_stats(12345)
    assert result["cpu_percent"] == 0.0


def test_collect_stats_io_counters_unavailable_returns_none():
    proc = _build_proc_mock()
    proc.io_counters.side_effect = AttributeError("not on this platform")
    with _patch_collect_deps(proc):
        result = processes_router._collect_stats(12345)
    assert result["io"] is None


def test_collect_stats_num_fds_unavailable_returns_none():
    proc = _build_proc_mock()
    proc.num_fds.side_effect = AttributeError("not on this platform")
    with _patch_collect_deps(proc):
        result = processes_router._collect_stats(12345)
    assert result["num_fds"] is None


def test_collect_stats_num_ctx_switches_unavailable_returns_none():
    proc = _build_proc_mock()
    proc.num_ctx_switches.side_effect = AttributeError("not on this platform")
    with _patch_collect_deps(proc):
        result = processes_router._collect_stats(12345)
    assert result["num_ctx_switches"] is None


def test_collect_stats_open_files_exception_defaults_to_zero():
    proc = _build_proc_mock()
    proc.open_files.side_effect = PermissionError("denied")
    with _patch_collect_deps(proc):
        result = processes_router._collect_stats(12345)
    assert result["open_files"] == 0


# ===== _read_proc_locks direct unit tests =============================

def test_read_proc_locks_no_file_returns_empty():
    with patch("builtins.open", side_effect=FileNotFoundError()):
        assert processes_router._read_proc_locks(1234) == []


def test_read_proc_locks_filters_to_requested_pid(tmp_path):
    locks_file = tmp_path / "locks"
    locks_file.write_text(
        "1: POSIX ADVISORY WRITE 1234 fd:01:9 0 EOF\n"
        "2: FLOCK ADVISORY READ 5678 fd:02:8 0 10\n"
        "3: POSIX ADVISORY WRITE 1234 fd:03:7 5 20\n"
        "short\n"
    )
    real_open = open

    def fake_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path) == "/proc/locks":
            return real_open(locks_file, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=fake_open):
        result = processes_router._read_proc_locks(1234)
    assert len(result) == 2
    assert result[0] == {
        "type": "POSIX",
        "kind": "ADVISORY",
        "mode": "WRITE",
        "pid": 1234,
        "region": "fd:01:9",
        "start": "0",
        "end": "EOF",
    }
    assert result[1]["start"] == "5"
    assert result[1]["end"] == "20"
    assert result[1]["pid"] == 1234


def test_read_proc_locks_no_match_returns_empty(tmp_path):
    locks_file = tmp_path / "locks"
    locks_file.write_text("1: POSIX ADVISORY WRITE 9999 fd:01:9 0 EOF\n")
    real_open = open

    def fake_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path) == "/proc/locks":
            return real_open(locks_file, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=fake_open):
        assert processes_router._read_proc_locks(1234) == []


def test_read_proc_locks_garbage_pid_field_skipped(tmp_path):
    """A line whose PID field is non-numeric must be skipped, not crash."""
    locks_file = tmp_path / "locks"
    locks_file.write_text("1: POSIX ADVISORY WRITE notapid fd:01:9 0 EOF\n")
    real_open = open

    def fake_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path) == "/proc/locks":
            return real_open(locks_file, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=fake_open):
        assert processes_router._read_proc_locks(1234) == []
