"""Unit tests for routers/processes.py — managed-process admin API.

Covers _read_proc_locks, _collect_stats, SignalProcessRequest model, and all
three registered routes (list_processes, signal_process, process_stats).

All OS interaction is mocked — no real process is spawned, inspected, or
signalled.
"""

from __future__ import annotations

import io
import signal as _signal
from unittest.mock import MagicMock, patch

import httpx
import psutil
import pytest
from fastapi import FastAPI

from general_ludd.process.registry import ManagedProcess, ProcessRegistry, ProcessRegistryError
from general_ludd.routers.processes import (
    SignalProcessRequest,
    _collect_stats,
    _read_proc_locks,
    register,
)


# ---------------------------------------------------------------------------
# _read_proc_locks
# ---------------------------------------------------------------------------


class TestReadProcLocks:
    """Tests for _read_proc_locks — parsing /proc/locks filtered by PID."""

    def test_read_proc_locks_finds_matching_pid(self) -> None:
        with patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO(
                "1: POSIX  ADVISORY  WRITE 42 fd:01:9999 0 EOF\n"
                "2: FLOCK  ADVISORY  READ 42 fd:02:8888 100 200\n"
            )
            result = _read_proc_locks(42)

        assert len(result) == 2
        assert result[0] == {
            "type": "POSIX",
            "kind": "ADVISORY",
            "mode": "WRITE",
            "pid": 42,
            "region": "fd:01:9999",
            "start": "0",
            "end": "EOF",
        }
        assert result[1]["type"] == "FLOCK"

    def test_read_proc_locks_skips_other_pids(self) -> None:
        with patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO(
                "1: POSIX  ADVISORY  WRITE 99 fd:01:9999 0 EOF\n"
                "2: FLOCK  ADVISORY  READ 42 fd:02:8888 100 200\n"
                "3: POSIX  ADVISORY  WRITE 99 fd:03:7777 0 EOF\n"
            )
            result = _read_proc_locks(42)

        assert len(result) == 1
        assert result[0]["pid"] == 42

    def test_read_proc_locks_file_missing(self) -> None:
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = _read_proc_locks(42)
        assert result == []

    def test_read_proc_locks_malformed_line_skipped(self) -> None:
        with patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO(
                "1: short\n"  # fewer than 8 fields
                "2: POSIX  ADVISORY  WRITE 42 fd:01:9999 0 EOF\n"
            )
            result = _read_proc_locks(42)

        assert len(result) == 1
        assert result[0]["pid"] == 42

    def test_read_proc_locks_non_integer_pid_skipped(self) -> None:
        with patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO(
                "1: POSIX  ADVISORY  WRITE abc fd:01:9999 0 EOF\n"
                "2: FLOCK  ADVISORY  READ 42 fd:02:8888 100 200\n"
            )
            result = _read_proc_locks(42)

        assert len(result) == 1
        assert result[0]["pid"] == 42

    def test_read_proc_locks_empty_file(self) -> None:
        with patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO("")
            result = _read_proc_locks(42)
        assert result == []


# ---------------------------------------------------------------------------
# _collect_stats
# ---------------------------------------------------------------------------


def _mock_psutil_process() -> MagicMock:
    """Build a psutil.Process mock returning plausible snapshot data."""
    mock_proc = MagicMock()
    mock_proc.cpu_percent.return_value = 5.0
    mock_proc.memory_info.return_value = MagicMock(rss=2048, vms=4096)
    mock_proc.io_counters.return_value = MagicMock(
        read_bytes=10, write_bytes=20, read_count=1, write_count=2,
    )
    mock_proc.num_fds.return_value = 4
    mock_proc.num_ctx_switches.return_value = MagicMock(
        voluntary=7, involuntary=9,
    )
    mock_proc.num_threads.return_value = 3
    mock_proc.status.return_value = "running"
    mock_proc.open_files.return_value = []
    return mock_proc


class TestCollectStats:
    """Tests for _collect_stats — psutil snapshot with graceful degradation."""

    @staticmethod
    def _patch_psutil_process(mock_proc):
        """Patch psutil.Process (lazy-imported inside _collect_stats)."""
        import psutil
        return patch.object(psutil, "Process", return_value=mock_proc)

    def test_collect_stats_shape(self) -> None:
        mock_proc = _mock_psutil_process()
        with self._patch_psutil_process(mock_proc), patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO("")
            result = _collect_stats(42)

        expected_keys = {
            "pid", "cpu_percent", "memory", "io", "num_fds",
            "num_threads", "num_ctx_switches", "status", "open_files", "locks",
        }
        assert set(result) == expected_keys
        assert result["pid"] == 42

    def test_collect_stats_cpu_percent_fallback(self) -> None:
        mock_proc = _mock_psutil_process()
        mock_proc.cpu_percent.side_effect = RuntimeError("permission denied")
        with self._patch_psutil_process(mock_proc), patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO("")
            result = _collect_stats(42)

        assert result["cpu_percent"] == 0.0

    def test_collect_stats_io_fallback(self) -> None:
        mock_proc = _mock_psutil_process()
        mock_proc.io_counters.side_effect = AttributeError("not available")
        with self._patch_psutil_process(mock_proc), patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO("")
            result = _collect_stats(42)

        assert result["io"] is None

    def test_collect_stats_num_fds_fallback(self) -> None:
        mock_proc = _mock_psutil_process()
        mock_proc.num_fds.side_effect = PermissionError
        with self._patch_psutil_process(mock_proc), patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO("")
            result = _collect_stats(42)

        assert result["num_fds"] is None

    def test_collect_stats_num_ctx_switches_fallback(self) -> None:
        mock_proc = _mock_psutil_process()
        mock_proc.num_ctx_switches.side_effect = RuntimeError("unavailable")
        with self._patch_psutil_process(mock_proc), patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO("")
            result = _collect_stats(42)

        assert result["num_ctx_switches"] is None

    def test_collect_stats_open_files_fallback(self) -> None:
        mock_proc = _mock_psutil_process()
        mock_proc.open_files.side_effect = PermissionError
        with self._patch_psutil_process(mock_proc), patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO("")
            result = _collect_stats(42)

        assert result["open_files"] == 0

    def test_collect_stats_num_threads(self) -> None:
        mock_proc = _mock_psutil_process()
        mock_proc.num_threads.return_value = 8
        with self._patch_psutil_process(mock_proc), patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO("")
            result = _collect_stats(42)

        assert result["num_threads"] == 8

    def test_collect_stats_status(self) -> None:
        mock_proc = _mock_psutil_process()
        mock_proc.status.return_value = "sleeping"
        with self._patch_psutil_process(mock_proc), patch("builtins.open") as mock_open:
            mock_open.return_value = io.StringIO("")
            result = _collect_stats(42)

        assert result["status"] == "sleeping"


# ---------------------------------------------------------------------------
# SignalProcessRequest model
# ---------------------------------------------------------------------------


class TestSignalProcessRequest:
    """Tests for the SignalProcessRequest Pydantic model."""

    def test_signal_request_defaults(self) -> None:
        req = SignalProcessRequest()
        assert req.signal == "SIGTERM"
        assert req.group is False

    def test_signal_request_custom_values(self) -> None:
        req = SignalProcessRequest(signal="SIGKILL", group=True)
        assert req.signal == "SIGKILL"
        assert req.group is True


# ---------------------------------------------------------------------------
# Route tests — fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    """Minimal FastAPI app with only the processes router registered."""
    a = FastAPI()
    register(a, {})
    return a


def _managed_record(pid: int = 4242) -> ManagedProcess:
    """A synthetic managed-process record — no real OS process."""
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
    """A ProcessRegistry pre-loaded with a synthetic record."""
    reg = ProcessRegistry()
    reg._procs[pid] = _managed_record(pid)
    return reg


# ---------------------------------------------------------------------------
# list_processes route
# ---------------------------------------------------------------------------


class TestListProcesses:
    """Tests for GET /admin/processes."""

    @pytest.mark.asyncio
    async def test_list_processes_empty_registry(self, app: FastAPI) -> None:
        reg = ProcessRegistry()
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/processes")

        assert resp.status_code == 200
        assert resp.json() == {"processes": [], "count": 0}

    @pytest.mark.asyncio
    async def test_list_processes_with_processes(self, app: FastAPI) -> None:
        pid = 4242
        reg = _registry_with(pid=pid)
        transport = httpx.ASGITransport(app=app)
        with (
            patch(
                "general_ludd.routers.processes.default_registry", return_value=reg,
            ),
            patch("psutil.Process") as mock_proc_cls,
        ):
            mock_proc = MagicMock()
            mock_proc.create_time.return_value = 1000.0
            mock_proc_cls.return_value = mock_proc
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/processes")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"processes", "count"}
        assert body["count"] == 1
        row = body["processes"][0]
        assert row["pid"] == pid
        assert row["origin"] == "unit_test"
        assert row["alive"] is True

    @pytest.mark.asyncio
    async def test_list_processes_includes_alive_status(self, app: FastAPI) -> None:
        pid = 4242
        reg = _registry_with(pid=pid)
        transport = httpx.ASGITransport(app=app)
        with (
            patch(
                "general_ludd.routers.processes.default_registry", return_value=reg,
            ),
            patch("psutil.Process") as mock_proc_cls,
        ):
            mock_proc = MagicMock()
            mock_proc.create_time.return_value = 1000.0
            mock_proc_cls.return_value = mock_proc
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/processes")

        row = resp.json()["processes"][0]
        assert "alive" in row
        assert isinstance(row["alive"], bool)


# ---------------------------------------------------------------------------
# signal_process route
# ---------------------------------------------------------------------------


class TestSignalProcess:
    """Tests for POST /admin/processes/{pid}/signal."""

    @pytest.mark.asyncio
    async def test_signal_process_ok(self, app: FastAPI) -> None:
        pid = 4242
        reg = MagicMock()
        reg.resolve_signal.return_value = int(_signal.SIGTERM)
        reg.signal.return_value = None
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/admin/processes/{pid}/signal",
                    json={"signal": "SIGTERM", "group": False},
                )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"ok": True, "pid": pid, "signal": "SIGTERM", "group": False}
        reg.resolve_signal.assert_called_once_with("SIGTERM")

    @pytest.mark.asyncio
    async def test_signal_unmanaged_pid_returns_404(self, app: FastAPI) -> None:
        pid = 99999
        reg = MagicMock()
        reg.resolve_signal.return_value = int(_signal.SIGTERM)
        reg.signal.side_effect = ProcessRegistryError(
            f"refusing to signal pid {pid}: not a gludd-managed process"
        )
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/admin/processes/{pid}/signal",
                    json={"signal": "SIGTERM"},
                )

        assert resp.status_code == 404
        assert "not a gludd-managed process" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_signal_disallowed_signal_returns_400(self, app: FastAPI) -> None:
        pid = 4242
        reg = MagicMock()
        reg.resolve_signal.side_effect = ProcessRegistryError(
            "signal 'SIGBOGUS' is not in the allow-list (SIGCONT, SIGTERM)"
        )
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/admin/processes/{pid}/signal",
                    json={"signal": "SIGBOGUS"},
                )

        assert resp.status_code == 400
        assert "allow-list" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_signal_identity_check_failed_returns_409(self, app: FastAPI) -> None:
        pid = 4242
        reg = MagicMock()
        reg.resolve_signal.return_value = int(_signal.SIGTERM)
        reg.signal.side_effect = ProcessRegistryError(
            f"refusing to signal pid {pid}: process is gone or its PID was "
            f"reused by a different process (identity check failed)"
        )
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/admin/processes/{pid}/signal",
                    json={"signal": "SIGTERM"},
                )

        assert resp.status_code == 409
        assert "identity check failed" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_signal_process_disappeared_returns_409(self, app: FastAPI) -> None:
        pid = 4242
        reg = MagicMock()
        reg.resolve_signal.return_value = int(_signal.SIGTERM)
        reg.signal.side_effect = ProcessRegistryError(
            f"process {pid} disappeared before the signal was delivered"
        )
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/admin/processes/{pid}/signal",
                    json={"signal": "SIGTERM"},
                )

        assert resp.status_code == 409
        assert "disappeared" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_signal_unexpected_exception_returns_500(self, app: FastAPI) -> None:
        pid = 4242
        reg = MagicMock()
        reg.resolve_signal.return_value = int(_signal.SIGTERM)
        reg.signal.side_effect = RuntimeError("something exploded")
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/admin/processes/{pid}/signal",
                    json={"signal": "SIGTERM"},
                )

        assert resp.status_code == 500
        assert "internal error" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_signal_with_group_true(self, app: FastAPI) -> None:
        pid = 4242
        reg = MagicMock()
        reg.resolve_signal.return_value = int(_signal.SIGTERM)
        reg.signal.return_value = None
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/admin/processes/{pid}/signal",
                    json={"signal": "SIGTERM", "group": True},
                )

        assert resp.status_code == 200
        assert resp.json()["group"] is True


# ---------------------------------------------------------------------------
# process_stats route
# ---------------------------------------------------------------------------


class TestProcessStats:
    """Tests for GET /admin/processes/{pid}/stats."""

    @pytest.mark.asyncio
    async def test_stats_managed_process(self, app: FastAPI) -> None:
        pid = 4242
        reg = _registry_with(pid=pid)
        transport = httpx.ASGITransport(app=app)
        with (
            patch(
                "general_ludd.routers.processes.default_registry", return_value=reg,
            ),
            patch("psutil.Process") as mock_proc_cls,
        ):
            mock_proc = _mock_psutil_process()
            mock_proc.create_time.return_value = 1000.0
            mock_proc_cls.return_value = mock_proc
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/admin/processes/{pid}/stats")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pid"] == pid
        assert body["cpu_percent"] == 5.0
        assert body["memory"] == {"rss": 2048, "vms": 4096}

    @pytest.mark.asyncio
    async def test_stats_unmanaged_pid_returns_404(self, app: FastAPI) -> None:
        reg = ProcessRegistry()
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/admin/processes/99999/stats")

        assert resp.status_code == 404
        assert "not a live gludd-managed process" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_stats_registered_but_dead_returns_404(self, app: FastAPI) -> None:
        pid = 4242
        reg = MagicMock()
        reg.get.return_value = _managed_record(pid)
        reg.is_alive.return_value = False
        transport = httpx.ASGITransport(app=app)
        with patch(
            "general_ludd.routers.processes.default_registry", return_value=reg,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/admin/processes/{pid}/stats")

        assert resp.status_code == 404
        assert "not a live gludd-managed process" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_stats_access_denied_returns_403(self, app: FastAPI) -> None:
        pid = 4242
        reg = MagicMock()
        reg.get.return_value = _managed_record(pid)
        reg.is_alive.return_value = True
        transport = httpx.ASGITransport(app=app)
        with (
            patch(
                "general_ludd.routers.processes.default_registry", return_value=reg,
            ),
            patch(
                "general_ludd.routers.processes._collect_stats",
                side_effect=psutil.AccessDenied(pid),
            ),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/admin/processes/{pid}/stats")

        assert resp.status_code == 403
        assert str(pid) in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_stats_no_such_process_returns_404(self, app: FastAPI) -> None:
        pid = 4242
        reg = MagicMock()
        reg.get.return_value = _managed_record(pid)
        reg.is_alive.return_value = True
        transport = httpx.ASGITransport(app=app)
        with (
            patch(
                "general_ludd.routers.processes.default_registry", return_value=reg,
            ),
            patch(
                "general_ludd.routers.processes._collect_stats",
                side_effect=psutil.NoSuchProcess(pid),
            ),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/admin/processes/{pid}/stats")

        assert resp.status_code == 404
        assert str(pid) in resp.json()["detail"]
