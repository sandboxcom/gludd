"""Unit tests for the managed-process admin router (routers/processes.py).

Covers:
  * Router isolation (bare FastAPI + register, route contract)
  * SignalProcessRequest model validation
  * Endpoint handler error branches (400/404/409/500 for signal, 403/404 for stats)
  * _read_proc_locks parsing (all edge cases)
  * _collect_stats shape (mocked psutil, degradation paths)
  * Integration-style tests via the daemon app (existing tests below)
"""

from __future__ import annotations

import contextlib
import signal as _signal
import subprocess
import sys
from collections.abc import Iterator
from unittest.mock import MagicMock, mock_open, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app
from general_ludd.process.registry import ProcessRegistryError, default_registry
from general_ludd.routers.processes import (
    SignalProcessRequest,
    _collect_stats,
    _read_proc_locks,
    register,
)

# ── Router isolation (bare FastAPI + register) ──────────────────────────


class TestRouterRegistration:
    def test_register_adds_three_routes(self) -> None:
        app = FastAPI()
        before = len(app.routes)
        register(app, {})
        assert len(app.routes) == before + 3

    def test_routes_have_correct_methods_and_paths(self) -> None:
        app = FastAPI()
        register(app, {})
        defaults = {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
        app_routes = {
            (frozenset(r.methods), r.path)
            for r in app.routes
            if r.path not in defaults
        }
        assert (frozenset({"GET"}), "/admin/processes") in app_routes
        assert (frozenset({"POST"}), "/admin/processes/{pid}/signal") in app_routes
        assert (frozenset({"GET"}), "/admin/processes/{pid}/stats") in app_routes
        assert len(app_routes) == 3


# ── SignalProcessRequest model validation ──────────────────────────────


class TestSignalProcessRequestModel:
    def test_defaults_to_sigterm_no_group(self) -> None:
        req = SignalProcessRequest()
        assert req.signal == "SIGTERM"
        assert req.group is False

    def test_custom_signal_and_group(self) -> None:
        req = SignalProcessRequest(signal="SIGKILL", group=True)
        assert req.signal == "SIGKILL"
        assert req.group is True

    def test_signal_is_string(self) -> None:
        req = SignalProcessRequest(signal="SIGSTOP")
        assert isinstance(req.signal, str)

    def test_group_is_bool(self) -> None:
        req = SignalProcessRequest(group=True)
        assert isinstance(req.group, bool)

    def test_empty_signal_string_accepted(self) -> None:
        req = SignalProcessRequest(signal="")
        assert req.signal == ""


# ── Endpoint handler error branches (mocked registry) ──────────────────


@pytest.fixture
def mock_app() -> FastAPI:
    _app = FastAPI()
    register(_app, {})
    return _app


@pytest.fixture
def mock_client(mock_app: FastAPI) -> TestClient:
    return TestClient(mock_app)


class TestSignalProcessErrors:
    def test_resolve_signal_failure_returns_500(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.resolve_signal.side_effect = ValueError("unknown signal BOGUS")
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.post(
                "/admin/processes/42/signal",
                json={"signal": "BOGUS", "group": False},
            )
        assert resp.status_code == 500

    def test_signal_unmanaged_process_returns_404(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = _signal.SIGTERM
        mock_reg.signal.side_effect = ProcessRegistryError(
            "pid 42 is not a gludd-managed process"
        )
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": False},
            )
        assert resp.status_code == 404

    def test_signal_identity_check_failed_returns_409(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = _signal.SIGTERM
        mock_reg.signal.side_effect = ProcessRegistryError(
            "identity check failed for pid 42"
        )
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": False},
            )
        assert resp.status_code == 409

    def test_signal_process_disappeared_returns_409(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = _signal.SIGTERM
        mock_reg.signal.side_effect = ProcessRegistryError(
            "process disappeared for pid 42"
        )
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": False},
            )
        assert resp.status_code == 409

    def test_signal_not_in_allow_list_returns_400(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = _signal.SIGTERM
        mock_reg.signal.side_effect = ProcessRegistryError(
            "signal SIGKILL not in allow-list"
        )
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGKILL", "group": False},
            )
        assert resp.status_code == 400

    def test_signal_other_process_registry_error_returns_400(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = _signal.SIGTERM
        mock_reg.signal.side_effect = ProcessRegistryError("some other reason")
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": False},
            )
        assert resp.status_code == 400

    def test_signal_unexpected_runtime_error_returns_500(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = _signal.SIGTERM
        mock_reg.signal.side_effect = RuntimeError("unexpected failure")
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": False},
            )
        assert resp.status_code == 500
        assert "internal error" in resp.json()["detail"]

    def test_signal_with_group_true_calls_registry_signal_with_group(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = _signal.SIGTERM
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": True},
            )
        assert resp.status_code == 200
        mock_reg.signal.assert_called_once_with(42, _signal.SIGTERM, group=True)


class TestProcessStatsErrors:
    def test_stats_unmanaged_pid_returns_404(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.get.return_value = None
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.get("/admin/processes/999/stats")
        assert resp.status_code == 404
        assert "not a live" in resp.json()["detail"]

    def test_stats_dead_process_returns_404(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.get.return_value = MagicMock()
        mock_reg.is_alive.return_value = False
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = mock_client.get("/admin/processes/42/stats")
        assert resp.status_code == 404

    def test_stats_access_denied_returns_403(
        self, mock_client: TestClient
    ) -> None:
        import psutil

        mock_reg = MagicMock()
        mock_reg.get.return_value = MagicMock()
        mock_reg.is_alive.return_value = True
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ), patch(
            "general_ludd.routers.processes.asyncio.to_thread",
            side_effect=psutil.AccessDenied("access denied"),
        ):
            resp = mock_client.get("/admin/processes/42/stats")
        assert resp.status_code == 403
        assert "permission denied" in resp.json()["detail"]

    def test_stats_no_such_process_returns_404(
        self, mock_client: TestClient
    ) -> None:
        import psutil

        mock_reg = MagicMock()
        mock_reg.get.return_value = MagicMock()
        mock_reg.is_alive.return_value = True
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ), patch(
            "general_ludd.routers.processes.asyncio.to_thread",
            side_effect=psutil.NoSuchProcess(42),
        ):
            resp = mock_client.get("/admin/processes/42/stats")
        assert resp.status_code == 404
        assert "no longer available" in resp.json()["detail"]

    def test_stats_unexpected_exception_returns_404(
        self, mock_client: TestClient
    ) -> None:
        mock_reg = MagicMock()
        mock_reg.get.return_value = MagicMock()
        mock_reg.is_alive.return_value = True
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ), patch(
            "general_ludd.routers.processes.asyncio.to_thread",
            side_effect=RuntimeError("unexpected"),
        ):
            resp = mock_client.get("/admin/processes/42/stats")
        assert resp.status_code == 404


# ── _read_proc_locks unit tests ────────────────────────────────────────


class TestReadProcLocks:
    def test_parses_valid_lock_line(self) -> None:
        line = "1: POSIX  ADVISORY  WRITE 1234 08:01:9999 0 EOF\n"
        with patch("builtins.open", mock_open(read_data=line)) as _m:
            result = _read_proc_locks(1234)
        assert len(result) == 1
        assert result[0] == {
            "type": "POSIX",
            "kind": "ADVISORY",
            "mode": "WRITE",
            "pid": 1234,
            "region": "08:01:9999",
            "start": "0",
            "end": "EOF",
        }

    def test_filters_out_lines_for_other_pids(self) -> None:
        lines = (
            "1: POSIX  ADVISORY  WRITE 1234 08:01:9999 0 EOF\n"
            "2: POSIX  ADVISORY  READ 5678 08:01:8888 0 EOF\n"
        )
        with patch("builtins.open", mock_open(read_data=lines)) as _m:
            result = _read_proc_locks(1234)
        assert len(result) == 1
        assert result[0]["pid"] == 1234

    def test_skips_line_with_less_than_8_fields(self) -> None:
        line = "1: POSIX  ADVISORY  WRITE\n"
        with patch("builtins.open", mock_open(read_data=line)) as _m:
            result = _read_proc_locks(1234)
        assert result == []

    def test_skips_line_with_non_integer_pid_field(self) -> None:
        line = "1: POSIX  ADVISORY  WRITE NAN 08:01:9999 0 EOF\n"
        with patch("builtins.open", mock_open(read_data=line)) as _m:
            result = _read_proc_locks(1234)
        assert result == []

    def test_file_not_found_returns_empty_list(self) -> None:
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = _read_proc_locks(1234)
        assert result == []

    def test_permission_error_returns_empty_list(self) -> None:
        with patch("builtins.open", side_effect=PermissionError):
            result = _read_proc_locks(1234)
        assert result == []

    def test_any_exception_returns_empty_list(self) -> None:
        with patch("builtins.open", side_effect=RuntimeError("boom")):
            result = _read_proc_locks(1234)
        assert result == []

    def test_empty_file_returns_empty_list(self) -> None:
        with patch("builtins.open", mock_open(read_data="")) as _m:
            result = _read_proc_locks(1234)
        assert result == []


# ── _collect_stats unit tests (psutil mocked globally) ─────────────────


class TestCollectStats:
    def test_returns_all_fields_expected_shape(self) -> None:
        fake_proc = MagicMock()
        fake_proc.cpu_percent.return_value = 5.5
        fake_proc.memory_info.return_value = MagicMock(rss=1024000, vms=2048000)
        fake_proc.io_counters.return_value = MagicMock(
            read_bytes=100, write_bytes=200, read_count=3, write_count=4,
        )
        fake_proc.num_fds.return_value = 10
        fake_proc.num_ctx_switches.return_value = MagicMock(
            voluntary=50, involuntary=30,
        )
        fake_proc.open_files.return_value = [MagicMock()] * 3
        fake_proc.num_threads.return_value = 2
        fake_proc.status.return_value = "running"

        with patch("psutil.Process", return_value=fake_proc):
            result = _collect_stats(42)

        assert result["pid"] == 42
        assert result["cpu_percent"] == 5.5
        assert result["memory"] == {"rss": 1024000, "vms": 2048000}
        assert result["io"] == {
            "read_bytes": 100,
            "write_bytes": 200,
            "read_count": 3,
            "write_count": 4,
        }
        assert result["num_fds"] == 10
        assert result["num_threads"] == 2
        assert result["num_ctx_switches"] == {"voluntary": 50, "involuntary": 30}
        assert result["status"] == "running"
        assert result["open_files"] == 3
        assert isinstance(result["locks"], list)

    def test_io_counters_unavailable_degraded_to_none(self) -> None:
        fake_proc = MagicMock()
        fake_proc.cpu_percent.return_value = 0.0
        fake_proc.memory_info.return_value = MagicMock(rss=0, vms=0)
        fake_proc.io_counters.side_effect = AttributeError("no io")
        fake_proc.num_fds.return_value = 0
        fake_proc.num_ctx_switches.side_effect = Exception
        fake_proc.open_files.side_effect = Exception
        fake_proc.num_threads.return_value = 1
        fake_proc.status.return_value = "idle"

        with patch("psutil.Process", return_value=fake_proc):
            result = _collect_stats(1)

        assert result["pid"] == 1
        assert result["cpu_percent"] >= 0
        assert result["io"] is None
        assert result["num_fds"] == 0
        assert result["num_ctx_switches"] is None
        assert result["open_files"] == 0

    def test_cpu_percent_exception_yields_zero(self) -> None:
        fake_proc = MagicMock()
        fake_proc.cpu_percent.side_effect = ProcessLookupError
        fake_proc.memory_info.return_value = MagicMock(rss=0, vms=0)
        fake_proc.io_counters.side_effect = Exception
        fake_proc.num_fds.side_effect = Exception
        fake_proc.num_ctx_switches.side_effect = Exception
        fake_proc.open_files.side_effect = Exception
        fake_proc.num_threads.return_value = 1
        fake_proc.status.return_value = "zombie"

        with patch("psutil.Process", return_value=fake_proc):
            result = _collect_stats(7)

        assert result["cpu_percent"] == 0.0


# ── Existing integration-style tests (daemon app + real subprocess) ────


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


@pytest.fixture
def managed_child() -> Iterator[int]:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    reg = default_registry()
    reg.register(proc.pid, command=["sleep-30-test-child"], origin="unit_test")
    try:
        yield proc.pid
    finally:
        reg.deregister(proc.pid)
        proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)


def _unmanaged_pid() -> int:
    import os

    return os.getpid()


@pytest.mark.asyncio
async def test_list_processes_includes_managed_child(app, managed_child):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/processes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    pids = {p["pid"] for p in data["processes"]}
    assert managed_child in pids
    rec = next(p for p in data["processes"] if p["pid"] == managed_child)
    assert rec["origin"] == "unit_test"
    assert rec["alive"] is True


@pytest.mark.asyncio
async def test_stats_for_managed_child(app, managed_child):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/admin/processes/{managed_child}/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pid"] == managed_child
    assert "cpu_percent" in data
    assert set(data["memory"]) == {"rss", "vms"}
    assert isinstance(data["memory"]["rss"], int)
    assert isinstance(data["num_threads"], int)
    assert isinstance(data["open_files"], int)
    assert isinstance(data["locks"], list)
    assert "status" in data


@pytest.mark.asyncio
async def test_signal_unmanaged_pid_returns_404(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/admin/processes/{_unmanaged_pid()}/signal",
            json={"signal": "SIGTERM", "group": False},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stats_unmanaged_pid_returns_404(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/admin/processes/{_unmanaged_pid()}/stats")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_signal_managed_child_ok(app, managed_child):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/admin/processes/{managed_child}/signal",
            json={"signal": "SIGCONT", "group": False},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "ok": True,
        "pid": managed_child,
        "signal": "SIGCONT",
        "group": False,
    }
