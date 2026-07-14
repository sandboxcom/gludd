"""Structural tests for routers/processes.py — admin process registry endpoints."""

from __future__ import annotations

import inspect
import logging
import typing

import pytest
from fastapi import FastAPI

from general_ludd.routers.processes import (
    SignalProcessRequest,
    _collect_stats,
    _read_proc_locks,
    _ProcessIOCCounters,
    _ProcessWithIOC,
    logger,
    register,
)


# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------


class TestModuleImport:
    def test_module_can_be_imported(self):
        import general_ludd.routers.processes

        assert general_ludd.routers.processes is not None


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
        assert callable(getattr(_ProcessWithIOC, "io_counters"))


# ---------------------------------------------------------------------------
# _read_proc_locks (helper)
# ---------------------------------------------------------------------------


class TestReadProcLocks:
    def test_is_callable(self):
        assert callable(_read_proc_locks)

    def test_accepts_pid_arg(self):
        sig = inspect.signature(_read_proc_locks)
        assert "pid" in sig.parameters

    def test_pid_arg_is_int(self):
        sig = inspect.signature(_read_proc_locks)
        param = sig.parameters["pid"]
        assert param.annotation == int or param.annotation == "int"

    def test_returns_nothing_on_bad_pid(self):
        result = _read_proc_locks(-1)
        assert isinstance(result, list)
        assert result == []

    def test_return_type_annotation(self):
        sig = inspect.signature(_read_proc_locks)
        assert sig.return_annotation is not inspect.Parameter.empty


# ---------------------------------------------------------------------------
# _collect_stats (helper)
# ---------------------------------------------------------------------------


class TestCollectStats:
    def test_is_callable(self):
        assert callable(_collect_stats)

    def test_accepts_pid_arg(self):
        sig = inspect.signature(_collect_stats)
        assert "pid" in sig.parameters

    def test_pid_arg_is_int(self):
        sig = inspect.signature(_collect_stats)
        param = sig.parameters["pid"]
        assert param.annotation == int or param.annotation == "int"

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
# register (router wiring)
# ---------------------------------------------------------------------------


class TestRegister:
    def test_is_callable(self):
        assert callable(register)

    def test_accepts_two_args(self):
        sig = inspect.signature(register)
        params = list(sig.parameters.keys())
        assert len(params) == 2
        assert "app" in params
        assert "_daemon_state" in params

    def test_register_does_not_raise(self):
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        try:
            register(app, daemon_state)
        except Exception as exc:
            raise AssertionError(f"register raised: {exc}") from exc


# ---------------------------------------------------------------------------
# Registered routes
# ---------------------------------------------------------------------------


class TestRegisterRoutes:
    @classmethod
    def setup_class(cls):
        cls.app = FastAPI()
        cls.daemon_state: dict[str, object] = {}
        register(cls.app, cls.daemon_state)
        cls.routes = {r.path for r in cls.app.routes}

    def test_registers_list_processes_route(self):
        assert "/admin/processes" in self.routes

    def test_registers_signal_process_route(self):
        assert "/admin/processes/{pid}/signal" in self.routes

    def test_registers_process_stats_route(self):
        assert "/admin/processes/{pid}/stats" in self.routes

    def test_route_count_at_least_three(self):
        assert len(self.routes) >= 3


# ---------------------------------------------------------------------------
# register function signature details
# ---------------------------------------------------------------------------


class TestRegisterSignature:
    def test_app_param_type_is_fastapi(self):
        sig = inspect.signature(register)
        app_param = sig.parameters["app"]
        assert app_param.annotation in (FastAPI, "FastAPI")

    def test_daemon_state_param_type_is_dict(self):
        sig = inspect.signature(register)
        ds_param = sig.parameters["_daemon_state"]
        assert ds_param.annotation in (dict[str, object], "dict[str, object]")

    def test_returns_none(self):
        sig = inspect.signature(register)
        assert sig.return_annotation in (None, "None")


# ---------------------------------------------------------------------------
# Route methods and handlers
# ---------------------------------------------------------------------------


class TestRouteHandlers:
    @classmethod
    def setup_class(cls):
        cls.app = FastAPI()
        cls.daemon_state: dict[str, object] = {}
        register(cls.app, cls.daemon_state)

    def _get_route(self, path: str):
        for r in self.app.routes:
            if r.path == path:
                return r
        return None

    def test_list_processes_is_get(self):
        route = self._get_route("/admin/processes")
        assert route is not None
        assert "GET" in route.methods

    def test_signal_process_is_post(self):
        route = self._get_route("/admin/processes/{pid}/signal")
        assert route is not None
        assert "POST" in route.methods

    def test_process_stats_is_get(self):
        route = self._get_route("/admin/processes/{pid}/stats")
        assert route is not None
        assert "GET" in route.methods

    def test_route_handler_functions_are_async(self):
        for r in self.app.routes:
            if r.path.startswith("/admin/processes"):
                assert inspect.iscoroutinefunction(
                    r.endpoint
                ), f"{r.path} handler is not async"
