"""Deep crash recovery tests for LocalInferenceManager.

Covers: server crash restart, OOM recovery, GPU fallback, health check timeout,
zombie process cleanup, and resilience under subprocess/network failures.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from general_ludd.events.bus import EventBus
from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServerConfig,
)

# ── helpers ────────────────────────────────────────────────────────────────


def _make_manager(*, event_bus: EventBus | None = None) -> LocalInferenceManager:
    return LocalInferenceManager(event_bus=event_bus)


def _make_config(**overrides) -> LocalServerConfig:
    defaults: dict = dict(
        engine="vllm",
        model_name="test-model",
        startup_timeout=10.0,
    )
    defaults.update(overrides)
    return LocalServerConfig(**defaults)


def _make_mock_process(pid=12345, returncode=None) -> AsyncMock:
    proc = AsyncMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.wait = AsyncMock()
    proc.stderr = AsyncMock()
    proc.stdout = AsyncMock()
    return proc


# ── crash restart ──────────────────────────────────────────────────────────


class TestCrashRestart:
    """Server crashed; manager must allow re-create and restart on the same config."""

    def test_recreate_after_crash(self):
        mgr = _make_manager()
        cfg = _make_config()
        s1 = mgr.create_server(cfg)
        s1.status = "error"
        mgr.remove_server(s1.server_id)
        s2 = mgr.create_server(cfg)
        assert s2.server_id != s1.server_id
        assert s2.status == "stopped"

    def test_restart_after_crash_sets_status_running(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        server.status = "error"
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

    def test_crash_during_startup_sets_status_error(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=1.0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process(returncode=1)
        # simulate immediate crash: process exits before /health responds
        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch("general_ludd.infra.local_inference.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_client_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="exited"):
                asyncio.run(mgr.start_server(server.server_id))
        assert server.status == "error"


# ── OOM recovery ───────────────────────────────────────────────────────────


class TestOOMRecovery:
    """Out-of-memory kill via SIGKILL (exit -9); manager must clean up and allow restart."""

    def test_oom_kill_stops_status_cleared(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process()
        server.process = proc
        server.status = "running"
        server.pid = 12345
        with (
            patch("general_ludd.infra.local_inference.os.killpg"),
            patch("general_ludd.infra.local_inference.os.getpgid", return_value=12345),
        ):
            asyncio.run(mgr.stop_server(server.server_id))
        assert server.status == "stopped"
        assert server.process is None
        assert server.pid is None

    def test_oom_during_readiness_probe_exits_gracefully(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=5.0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process()
        proc.returncode = -9  # OOM kill exit code
        proc.stderr = AsyncMock()
        proc.stderr.read = AsyncMock(
            side_effect=[b"Out of memory: Killed process 12345", b""]
        )
        with patch(
            "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
            return_value=proc,
        ), pytest.raises(RuntimeError, match="exited"):
            asyncio.run(mgr.start_server(server.server_id))
        assert server.status == "error"

    def test_restart_after_oom_succeeds(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        server.status = "error"
        server.pid = None
        server.process = None
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


# ── GPU fallback ───────────────────────────────────────────────────────────


class TestGPUFallback:
    """GPU unavailable → fallback to CPU-only (gpu_layers=0), or error if impossible."""

    def test_gpu_layers_zero_creates_server(self):
        cfg = _make_config(engine="llamacpp", model_path="/models/llama.gguf", gpu_layers=0)
        mgr = _make_manager()
        server = mgr.create_server(cfg)
        assert server.config.gpu_layers == 0
        cmd = mgr._build_command(cfg)
        assert "--n_gpu_layers" in cmd
        assert "0" in cmd

    def test_gpu_layers_negative_defaults_to_all_layers(self):
        mgr = _make_manager()
        cfg = _make_config(engine="llamacpp", model_path="/models/llama.gguf", gpu_layers=-1)
        cmd = mgr._build_command(cfg)
        assert "-1" in cmd

    def test_vllm_does_not_need_gpu_layers_flag(self):
        mgr = _make_manager()
        cfg = _make_config(engine="vllm", model_name="llama3", gpu_layers=0)
        cmd = mgr._build_command(cfg)
        assert "--n_gpu_layers" not in cmd

    def test_gpu_unavailable_does_not_prevent_server_create(self):
        mgr = _make_manager()
        cfg = _make_config(gpu_layers=0)
        server = mgr.create_server(cfg)
        assert server.status == "stopped"
        assert server.config.gpu_layers == 0


# ── health check timeout ───────────────────────────────────────────────────


class TestHealthCheckTimeout:
    """Startup timeout scenarios: /health never responds, too slow, or 502."""

    def test_health_check_timeout_raises(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=0.5)
        server = mgr.create_server(cfg)
        proc = _make_mock_process()
        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch("general_ludd.infra.local_inference.httpx.AsyncClient") as mock_client,
        ):
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_client.return_value = mock_instance
            with pytest.raises(RuntimeError, match="did not become ready"):
                asyncio.run(mgr.start_server(server.server_id))
        assert server.status == "error"

    def test_health_check_server_error_retries(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=5.0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process()
        call_count = [0]

        async def mock_get(url):
            call_count[0] += 1
            if call_count[0] < 3:
                resp = AsyncMock()
                resp.status_code = 503
                return resp
            resp = AsyncMock()
            resp.status_code = 200
            return resp

        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch("general_ludd.infra.local_inference.httpx.AsyncClient") as mock_client,
        ):
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = mock_get
            mock_client.return_value = mock_instance
            asyncio.run(mgr.start_server(server.server_id))
        assert call_count[0] >= 2
        assert server.status == "running"

    def test_zero_startup_timeout_skips_health_check(self):
        mgr = _make_manager()
        cfg = _make_config(startup_timeout=0.0)
        server = mgr.create_server(cfg)
        proc = _make_mock_process()
        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch("general_ludd.infra.local_inference.httpx.AsyncClient") as mock_client,
        ):
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get = AsyncMock()
            mock_client.return_value = mock_instance
            asyncio.run(mgr.start_server(server.server_id))
        mock_instance.get.assert_not_called()
        assert server.status == "running"


# ── zombie process cleanup ─────────────────────────────────────────────────


class TestZombieProcessCleanup:
    """Orphaned/zombie process scenarios: stale PID, double kill, no process to wait on."""

    def test_stop_with_stale_pid_handles_processlookup(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        server.pid = 99999  # non-existent PID
        server.process = None
        server.status = "running"
        with (
            patch("general_ludd.infra.local_inference.os.killpg"),
            patch("general_ludd.infra.local_inference.os.getpgid", return_value=99999),
        ):
            asyncio.run(mgr.stop_server(server.server_id))
        assert server.status == "stopped"
        assert server.process is None
        assert server.pid is None

    def test_zombie_process_killed_with_sigkill(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process()
        proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)
        server.process = proc
        server.status = "running"
        server.pid = 12345
        with (
            patch("general_ludd.infra.local_inference.os.killpg") as mock_killpg,
            patch("general_ludd.infra.local_inference.os.getpgid", return_value=12345),
        ):
            asyncio.run(mgr.stop_server(server.server_id))
        import signal as _signal

        mock_killpg.assert_any_call(12345, _signal.SIGKILL)
        assert server.status == "stopped"

    def test_pid_kill_when_no_process_but_pid_exists(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        server.process = None
        server.pid = 12345
        server.status = "running"
        with patch("general_ludd.infra.local_inference.os.kill"):
            asyncio.run(mgr.stop_server(server.server_id))
        assert server.status == "stopped"
        assert server.pid is None

    def test_orphan_process_group_survives_sigterm(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        proc = _make_mock_process()
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError, asyncio.TimeoutError])
        server.process = proc
        server.status = "running"
        server.pid = 12345
        with (
            patch("general_ludd.infra.local_inference.os.killpg") as mock_killpg,
            patch("general_ludd.infra.local_inference.os.getpgid", return_value=12345),
        ):
            # SIGTERM times out, SIGKILL sent, final wait may also time out
            # but the server is still marked stopped
            asyncio.run(mgr.stop_server(server.server_id))
        import signal as _signal

        assert mock_killpg.call_count >= 2
        mock_killpg.assert_any_call(12345, _signal.SIGKILL)
        assert server.status == "stopped"


# ── server lifecycle edge cases ────────────────────────────────────────────


class TestServerLifecycleEdgeCases:
    """Multi-server restart, partial failure, concurrent stop+start."""

    def test_start_one_server_while_another_is_error(self):
        mgr = _make_manager()
        cfg_a = _make_config(model_name="a")
        cfg_b = _make_config(model_name="b", port=8001)
        s_a = mgr.create_server(cfg_a)
        s_b = mgr.create_server(cfg_b)
        s_a.status = "error"
        proc = _make_mock_process()
        with (
            patch(
                "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch.object(mgr, "_wait_for_ready", new=AsyncMock()),
        ):
            result = asyncio.run(mgr.start_server(s_b.server_id))
        assert result.status == "running"
        assert s_a.status == "error"

    def test_stop_server_that_was_never_started(self):
        mgr = _make_manager()
        cfg = _make_config()
        server = mgr.create_server(cfg)
        server.status = "stopped"
        server.process = None
        asyncio.run(mgr.stop_server(server.server_id))
        assert server.status == "stopped"

    def test_stop_all_with_stopped_and_error_servers(self):
        mgr = _make_manager()
        cfg1 = _make_config(model_name="a")
        cfg2 = _make_config(model_name="b", port=8001)
        s1 = mgr.create_server(cfg1)
        s2 = mgr.create_server(cfg2)
        s1.status = "stopped"
        s2.status = "error"
        asyncio.run(mgr.stop_all())
        assert s1.status == "stopped"
        assert s2.status == "stopped"
