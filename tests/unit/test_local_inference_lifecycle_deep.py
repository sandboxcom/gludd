"""Lifecycle tests for LocalInferenceManager start/stop/health-check.

Covers: start timeout, health check polling, graceful shutdown, force kill,
port conflict, multiple servers, event emission, and edge cases.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import EventType
from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServerConfig,
)


def _make_config(**overrides: Any) -> LocalServerConfig:
    defaults: dict[str, Any] = dict(engine="vllm", model_name="test-model", startup_timeout=10.0)
    defaults.update(overrides)
    return LocalServerConfig(**defaults)


def _make_manager(*, event_bus=None) -> LocalInferenceManager:
    return LocalInferenceManager(event_bus=event_bus)


def _make_mock_process(pid=12345, returncode=None) -> AsyncMock:
    proc = AsyncMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.wait = AsyncMock()
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b"")
    proc.stdout = AsyncMock()
    return proc


# ── start timeout ───────────────────────────────────────────────────────────


class TestStartTimeout:
    def test_startup_timeout_raises_runtime_error(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=0.5)
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=None)

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch("general_ludd.infra.local_inference.httpx.AsyncClient") as mock_client,
        ):
            mock_client.return_value.__aenter__ = AsyncMock()
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("refused"),
            )

            with pytest.raises(RuntimeError):
                asyncio.run(mgr.start_server(server.server_id))

            assert server.status == "error"

    def test_startup_timeout_zero_skips_health_probe(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process()

        with patch(
            "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
            return_value=proc,
        ):
            result = asyncio.run(mgr.start_server(server.server_id))
            assert result.status == "running"

    def test_timeout_logs_stderr_on_failure(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=0.5)
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=None)
        proc.stderr.read = AsyncMock(side_effect=[b"cuda out of memory", b""])

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch("general_ludd.infra.local_inference.httpx.AsyncClient") as mock_client,
        ):
            mock_client.return_value.__aenter__ = AsyncMock()
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("refused"),
            )

            with pytest.raises(RuntimeError, match="cuda out of memory"):
                asyncio.run(mgr.start_server(server.server_id))


# ── health check polling ────────────────────────────────────────────────────


class TestHealthCheckPolling:
    def test_health_check_succeeds_on_first_poll(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=10.0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch("general_ludd.infra.local_inference.httpx.AsyncClient") as mock_client,
        ):
            mock_client.return_value.__aenter__ = AsyncMock()
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)

            result = asyncio.run(mgr.start_server(server.server_id))
            assert result.status == "running"
            assert result.pid == 12345

    def test_health_check_succeeds_after_retries(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=10.0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        get_call_count = [0]

        async def failing_then_ok(*args, **kwargs):
            get_call_count[0] += 1
            if get_call_count[0] <= 2:
                raise httpx.ConnectError("refused")
            return mock_resp

        mock_get = AsyncMock(side_effect=failing_then_ok)

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch("general_ludd.infra.local_inference.httpx.AsyncClient") as mock_client,
        ):
            mock_client.return_value.__aenter__ = AsyncMock()
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.__aenter__.return_value.get = mock_get

            result = asyncio.run(mgr.start_server(server.server_id))
            assert result.status == "running"
            assert get_call_count[0] >= 3

    def test_health_check_non_200_retries(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=5.0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=None)

        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_loading = MagicMock()
        mock_loading.status_code = 503
        get_calls = [0]

        async def flaky(*args, **kwargs):
            get_calls[0] += 1
            if get_calls[0] <= 2:
                return mock_loading
            return mock_ok

        async def no_sleep(_: float) -> None:
            return

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch("general_ludd.infra.local_inference.httpx.AsyncClient") as mock_client,
        ):
            mock_client.return_value.__aenter__ = AsyncMock()
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(side_effect=flaky)

            with patch("general_ludd.infra.local_inference.asyncio.sleep", no_sleep):
                result = asyncio.run(mgr.start_server(server.server_id))
                assert result.status == "running"


# ── graceful shutdown ───────────────────────────────────────────────────────


class TestGracefulShutdown:
    def test_stop_server_sends_sigterm_and_awaits_exit(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=None)
        proc.wait = AsyncMock(return_value=0)
        server.process = proc
        server.status = "running"
        server.pid = proc.pid

        with (
            patch("general_ludd.infra.local_inference.os.killpg") as mock_killpg,
            patch("general_ludd.infra.local_inference.os.getpgid", return_value=proc.pid),
        ):
            asyncio.run(mgr.stop_server(server.server_id))

            mock_killpg.assert_called_once()
            proc.wait.assert_called_once()
            assert server.status == "stopped"
            assert server.process is None
            assert server.pid is None

    def test_stop_nonexistent_server_is_noop(self):
        mgr = _make_manager()
        asyncio.run(mgr.stop_server("nonexistent"))
        assert len(mgr.list_servers()) == 0

    def test_stop_already_stopped_server_is_noop(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        server.status = "stopped"

        with patch("general_ludd.infra.local_inference.os.killpg") as mock_killpg:
            asyncio.run(mgr.stop_server(server.server_id))
            mock_killpg.assert_not_called()

    def test_stop_server_with_completed_process_is_noop(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=0)
        server.process = proc
        server.status = "running"

        with patch("general_ludd.infra.local_inference.os.killpg") as mock_killpg:
            asyncio.run(mgr.stop_server(server.server_id))
            mock_killpg.assert_not_called()


# ── force kill ──────────────────────────────────────────────────────────────


class TestForceKill:
    def test_sigterm_timeout_escalates_to_sigkill(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=None)
        proc.wait = AsyncMock(side_effect=[TimeoutError(), TimeoutError()])
        server.process = proc
        server.status = "running"
        server.pid = proc.pid

        with (
            patch("general_ludd.infra.local_inference.os.killpg") as mock_killpg,
            patch("general_ludd.infra.local_inference.os.getpgid", return_value=proc.pid),
        ):
            asyncio.run(mgr.stop_server(server.server_id))

            assert mock_killpg.call_count == 2
            assert server.status == "stopped"

    def test_sigkill_then_stop_cleans_up(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=None)
        proc.wait = AsyncMock(return_value=0)
        server.process = proc
        server.status = "running"
        server.pid = proc.pid

        with (
            patch("general_ludd.infra.local_inference.os.killpg") as mock_killpg,
            patch("general_ludd.infra.local_inference.os.getpgid", return_value=proc.pid),
        ):
            asyncio.run(mgr.stop_server(server.server_id))

            mock_killpg.assert_called_once()
            assert server.status == "stopped"


# ── port conflict / early exit ──────────────────────────────────────────────


class TestPortConflictEarlyExit:
    def test_subprocess_exits_before_ready_raises(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=5.0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=1)
        proc.stderr.read = AsyncMock(side_effect=[b"Address already in use", b""])

        with patch(
            "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
            return_value=proc,
        ):
            with pytest.raises(RuntimeError, match=r"exited.*before becoming ready"):
                asyncio.run(mgr.start_server(server.server_id))

            assert server.status == "error"

    def test_early_exit_stderr_in_exception_message(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=5.0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=1)
        proc.stderr.read = AsyncMock(side_effect=[b"Port 8000 already in use. Try another.", b""])

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            pytest.raises(RuntimeError, match="Port 8000 already in use"),
        ):
            asyncio.run(mgr.start_server(server.server_id))


# ── multiple servers ────────────────────────────────────────────────────────


class TestMultipleServers:
    def test_create_multiple_servers_unique_ids(self):
        mgr = _make_manager()
        s1 = mgr.create_server(_make_config(port=8000))
        s2 = mgr.create_server(_make_config(port=8001))
        s3 = mgr.create_server(_make_config(port=8002))

        assert s1.server_id != s2.server_id != s3.server_id
        assert s1.server_id == "local-0"
        assert s2.server_id == "local-1"
        assert s3.server_id == "local-2"

    def test_create_multiple_distinct_endpoints(self):
        mgr = _make_manager()
        s1 = mgr.create_server(_make_config(host="localhost", port=8000))
        s2 = mgr.create_server(_make_config(host="127.0.0.1", port=9999))

        assert s1.endpoint_url == "http://localhost:8000/v1"
        assert s2.endpoint_url == "http://127.0.0.1:9999/v1"

    def test_stop_all_terminates_all_servers(self):
        mgr = _make_manager()
        servers = []
        for port in (8000, 8001, 8002):
            cfg = _make_config(port=port)
            s = mgr.create_server(cfg)
            servers.append(s)

        for s in servers:
            proc = _make_mock_process(pid=10000 + int(s.server_id.split("-")[1]))
            proc.wait = AsyncMock(return_value=0)
            s.process = proc
            s.status = "running"
            s.pid = proc.pid

        with (
            patch("general_ludd.infra.local_inference.os.killpg"),
            patch("general_ludd.infra.local_inference.os.getpgid", return_value=9999),
        ):
            asyncio.run(mgr.stop_all())

        for s in servers:
            assert s.status == "stopped"

    def test_list_servers_filter_by_status(self):
        mgr = _make_manager()
        s1 = mgr.create_server(_make_config(port=8000))
        s2 = mgr.create_server(_make_config(port=8001))
        s3 = mgr.create_server(_make_config(port=8002))

        s1.status = "running"
        s2.status = "running"
        s3.status = "error"

        assert len(mgr.list_servers(status="running")) == 2
        assert len(mgr.list_servers(status="error")) == 1
        assert len(mgr.list_servers(status="stopped")) == 0
        assert len(mgr.list_servers()) == 3

    def test_get_endpoints_only_running(self):
        mgr = _make_manager()
        s1 = mgr.create_server(_make_config(port=8000))
        s2 = mgr.create_server(_make_config(port=8001))
        s3 = mgr.create_server(_make_config(port=8002))

        s1.status = "running"
        s1.process = MagicMock()
        s2.status = "error"
        s3.status = "stopped"

        eps = mgr.get_endpoints()
        assert len(eps) == 1
        assert eps[s1.server_id] == "http://localhost:8000/v1"

    def test_remove_running_server_raises(self):
        mgr = _make_manager()
        s = mgr.create_server(_make_config())
        s.status = "running"
        s.process = MagicMock()

        with pytest.raises(RuntimeError, match="Cannot remove running server"):
            mgr.remove_server(s.server_id)

    def test_remove_stopped_server_succeeds(self):
        mgr = _make_manager()
        s = mgr.create_server(_make_config())
        mgr.remove_server(s.server_id)
        assert mgr.get_server(s.server_id) is None


# ── event emission ──────────────────────────────────────────────────────────


class TestEventEmission:
    def test_start_server_emits_deploy_started_event(self):
        bus = EventBus()
        events = []
        bus.subscribe(EventType.MODEL_DEPLOY_STARTED, events.append)
        mgr = _make_manager(event_bus=bus)
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process()

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch.object(mgr, "_wait_for_ready", new=AsyncMock()),
        ):
            asyncio.run(mgr.start_server(server.server_id))

        assert len(events) >= 1
        assert events[0].payload["server_id"] == server.server_id

    def test_start_server_emits_model_ready_event(self):
        bus = EventBus()
        ready_events = []
        bus.subscribe(EventType.MODEL_READY, ready_events.append)
        mgr = _make_manager(event_bus=bus)
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process()

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch.object(mgr, "_wait_for_ready", new=AsyncMock()),
        ):
            asyncio.run(mgr.start_server(server.server_id))

        assert len(ready_events) == 1
        assert ready_events[0].payload["server_id"] == server.server_id
        assert ready_events[0].payload["pid"] == 12345

    def test_no_event_bus_does_not_crash_on_emit(self):
        mgr = _make_manager(event_bus=None)
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process()

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch.object(mgr, "_wait_for_ready", new=AsyncMock()),
        ):
            result = asyncio.run(mgr.start_server(server.server_id))
            assert result.status == "running"


# ── edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_start_nonexistent_server_raises(self):
        mgr = _make_manager()
        with pytest.raises(ValueError, match="not found"):
            asyncio.run(mgr.start_server("bogus-id"))

    def test_start_already_running_server_noop(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        server.status = "running"
        server.process = MagicMock()

        result = asyncio.run(mgr.start_server(server.server_id))
        assert result is server

    def test_slurm_engine_does_not_use_subprocess(self):
        mgr = _make_manager()
        cfg = _make_config(engine="slurm", model_name="test-model", host="localhost")
        server = mgr.create_server(cfg)

        with patch(
            "general_ludd.infra.local_inference.SlurmAdapter.submit",
            return_value=42,
        ):
            result = asyncio.run(mgr.start_server(server.server_id))
            assert result.status == "submitted"

    def test_ansible_fallback_calls_playbook(self):
        mgr = _make_manager()
        cfg = _make_config(engine="vllm", model_name="test-model")
        server = mgr.create_server(cfg)

        fake_adapter = MagicMock()
        fake_adapter.run_playbook = MagicMock()
        fake_adapter.run_playbook.return_value = {
            "status": "success",
            "rc": 0,
            "facts": {
                "gludd_local_server": {
                    "status": "running",
                    "pid": 99999,
                }
            },
        }
        mgr.ansible_adapter = fake_adapter

        result = asyncio.run(mgr.start_server(server.server_id))
        assert result.status == "running"
        assert result.pid == 99999
        fake_adapter.run_playbook.assert_called_once()

    def test_ansible_playbook_failure_marks_error(self):
        mgr = _make_manager()
        bus = EventBus()
        error_events = []
        bus.subscribe(EventType.MODEL_ERROR, error_events.append)
        mgr = _make_manager(event_bus=bus)
        cfg = _make_config()
        server = mgr.create_server(cfg)

        fake_adapter = MagicMock()
        fake_adapter.run_playbook = MagicMock()
        fake_adapter.run_playbook.return_value = {
            "status": "failure",
            "rc": 1,
            "error": "GPU not found",
        }
        mgr.ansible_adapter = fake_adapter

        with pytest.raises(RuntimeError, match="ansible playbook failed"):
            asyncio.run(mgr.start_server(server.server_id))

        assert server.status == "error"
        assert len(error_events) == 1
