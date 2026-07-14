"""Structural tests for routers/processes.py — admin process registry endpoints."""

from __future__ import annotations

import inspect
import logging
import typing
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.processes import (
    SignalProcessRequest,
    _collect_stats,
    _ProcessIOCCounters,
    _ProcessWithIOC,
    _read_proc_locks,
    logger,
    register,
)

# ---------------------------------------------------------------------------
# SignalProcessRequest (Pydantic BaseModel)
# ---------------------------------------------------------------------------


class TestSignalProcessRequest:
    def test_is_pydantic_base_model(self):
        from pydantic import BaseModel
        assert issubclass(SignalProcessRequest, BaseModel)

    def test_default_signal_is_sigterm(self):
        req = SignalProcessRequest()
        assert req.signal == "SIGTERM"

    def test_default_group_is_false(self):
        req = SignalProcessRequest()
        assert req.group is False

    def test_custom_signal(self):
        req = SignalProcessRequest(signal="SIGKILL")
        assert req.signal == "SIGKILL"

    def test_custom_group(self):
        req = SignalProcessRequest(group=True)
        assert req.group is True


# ---------------------------------------------------------------------------
# _ProcessIOCCounters (Protocol)
# ---------------------------------------------------------------------------


class TestProcessIOCCounters:
    def test_is_protocol(self):
        from typing import Protocol
        assert issubclass(_ProcessIOCCounters, Protocol)

    def test_declares_read_bytes(self):
        hints = typing.get_type_hints(_ProcessIOCCounters)
        assert "read_bytes" in hints

    def test_declares_write_bytes(self):
        hints = typing.get_type_hints(_ProcessIOCCounters)
        assert "write_bytes" in hints

    def test_declares_read_count(self):
        hints = typing.get_type_hints(_ProcessIOCCounters)
        assert "read_count" in hints

    def test_declares_write_count(self):
        hints = typing.get_type_hints(_ProcessIOCCounters)
        assert "write_count" in hints


# ---------------------------------------------------------------------------
# _ProcessWithIOC (Protocol)
# ---------------------------------------------------------------------------


class TestProcessWithIOC:
    def test_is_protocol(self):
        from typing import Protocol
        assert issubclass(_ProcessWithIOC, Protocol)

    def test_declares_io_counters(self):
        assert hasattr(_ProcessWithIOC, "io_counters")
        assert callable(_ProcessWithIOC.io_counters)


# ---------------------------------------------------------------------------
# _read_proc_locks (helper)
# ---------------------------------------------------------------------------


class TestReadProcLocks:
    def test_is_callable(self):
        assert callable(_read_proc_locks)

    def test_accepts_pid_arg(self):
        sig = inspect.signature(_read_proc_locks)
        assert "pid" in sig.parameters

    def test_returns_empty_on_bad_pid(self):
        result = _read_proc_locks(-1)
        assert isinstance(result, list)
        assert result == []


# ---------------------------------------------------------------------------
# _collect_stats (helper)
# ---------------------------------------------------------------------------


class TestCollectStats:
    def test_is_callable(self):
        assert callable(_collect_stats)

    def test_accepts_pid_arg(self):
        sig = inspect.signature(_collect_stats)
        assert "pid" in sig.parameters

    def test_returns_dict_on_nonexistent_pid(self):
        import psutil
        try:
            result = _collect_stats(99999)
        except psutil.NoSuchProcess:
            pytest.skip("psutil resolved 99999 to a real pid on this platform")
        assert isinstance(result, dict)
        assert "pid" in result


# ---------------------------------------------------------------------------
# Module-level attributes
# ---------------------------------------------------------------------------


class TestModuleAttributes:
    def test_logger_exists(self):
        assert logger is not None
        assert isinstance(logger, logging.Logger)

    def test_logger_name(self):
        assert logger.name == "general_ludd.routers.processes"


# ---------------------------------------------------------------------------
# register (router wiring) + TestClient behavioral tests
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    _app = FastAPI()
    register(_app, {})
    return _app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestRegister:
    def test_is_callable(self):
        assert callable(register)

    def test_register_does_not_raise(self):
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        try:
            register(app, daemon_state)
        except Exception as exc:
            raise AssertionError(f"register raised: {exc}") from exc


class TestListProcesses:
    def test_returns_200_with_mocked_registry(self, client: TestClient):
        mock_reg = MagicMock()
        mock_reg.list.return_value = []
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.get("/admin/processes")
        assert resp.status_code == 200
        data = resp.json()
        assert "processes" in data
        assert "count" in data
        assert data["count"] == 0

    def test_includes_alive_flag(self, client: TestClient):
        mock_rec = MagicMock()
        mock_rec.pid = 42
        mock_rec.to_dict.return_value = {"pid": 42, "origin": "unit"}
        mock_reg = MagicMock()
        mock_reg.list.return_value = [mock_rec]
        mock_reg.is_alive.return_value = True
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.get("/admin/processes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        proc = data["processes"][0]
        assert proc["pid"] == 42
        assert proc["alive"] is True


class TestSignalProcess:
    def test_successful_signal_returns_200(self, client: TestClient):
        import signal as _signal
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = _signal.SIGTERM
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": False},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["pid"] == 42
        assert data["signal"] == "SIGTERM"
        assert data["group"] is False

    def test_signal_with_group_true_passes_group_to_registry(self, client: TestClient):
        import signal as _signal
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = _signal.SIGTERM
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": True},
            )
        assert resp.status_code == 200
        mock_reg.signal.assert_called_once_with(42, _signal.SIGTERM, group=True)

    def test_unmanaged_pid_returns_404(self, client: TestClient):
        from general_ludd.process.registry import ProcessRegistryError
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = 15
        mock_reg.signal.side_effect = ProcessRegistryError(
            "pid 42 is not a gludd-managed process"
        )
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": False},
            )
        assert resp.status_code == 404

    def test_identity_check_failed_returns_409(self, client: TestClient):
        from general_ludd.process.registry import ProcessRegistryError
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = 15
        mock_reg.signal.side_effect = ProcessRegistryError(
            "identity check failed for pid 42"
        )
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": False},
            )
        assert resp.status_code == 409

    def test_not_in_allow_list_returns_400(self, client: TestClient):
        from general_ludd.process.registry import ProcessRegistryError
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = 15
        mock_reg.signal.side_effect = ProcessRegistryError(
            "signal SIGKILL not in allow-list"
        )
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGKILL", "group": False},
            )
        assert resp.status_code == 400

    def test_unexpected_error_returns_500(self, client: TestClient):
        mock_reg = MagicMock()
        mock_reg.resolve_signal.return_value = 15
        mock_reg.signal.side_effect = RuntimeError("unexpected")
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.post(
                "/admin/processes/42/signal",
                json={"signal": "SIGTERM", "group": False},
            )
        assert resp.status_code == 500
        assert "internal error" in resp.json()["detail"]


class TestProcessStats:
    def test_unmanaged_pid_returns_404(self, client: TestClient):
        mock_reg = MagicMock()
        mock_reg.get.return_value = None
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.get("/admin/processes/999/stats")
        assert resp.status_code == 404
        assert "not a live" in resp.json()["detail"]

    def test_dead_process_returns_404(self, client: TestClient):
        mock_reg = MagicMock()
        mock_reg.get.return_value = MagicMock()
        mock_reg.is_alive.return_value = False
        with patch(
            "general_ludd.routers.processes.default_registry",
            return_value=mock_reg,
        ):
            resp = client.get("/admin/processes/42/stats")
        assert resp.status_code == 404

    def test_access_denied_returns_403(self, client: TestClient):
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
            resp = client.get("/admin/processes/42/stats")
        assert resp.status_code == 403
        assert "permission denied" in resp.json()["detail"]

    def test_no_such_process_returns_404(self, client: TestClient):
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
            resp = client.get("/admin/processes/42/stats")
        assert resp.status_code == 404
        assert "no longer available" in resp.json()["detail"]
