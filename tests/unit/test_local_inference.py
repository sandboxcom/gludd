"""Unit tests for local inference manager (vllm + llama.cpp)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServer,
    LocalServerConfig,
)


class TestLocalServerConfig:
    def test_defaults(self):
        cfg = LocalServerConfig()
        assert cfg.engine == "vllm"
        assert cfg.model_path == ""
        assert cfg.model_name == ""
        assert cfg.host == "localhost"
        assert cfg.port == 8000
        assert cfg.gpu_layers == -1
        assert cfg.context_size == 4096
        assert cfg.extra_args == []

    def test_custom_values(self):
        cfg = LocalServerConfig(
            engine="llamacpp",
            model_path="/models/llama.gguf",
            host="0.0.0.0",
            port=9999,
            gpu_layers=40,
            context_size=8192,
            extra_args=["--no-mmap"],
        )
        assert cfg.engine == "llamacpp"
        assert cfg.model_path == "/models/llama.gguf"
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9999
        assert cfg.gpu_layers == 40
        assert cfg.context_size == 8192
        assert cfg.extra_args == ["--no-mmap"]


class TestLocalServer:
    def _make_server(self, status="stopped"):
        cfg = LocalServerConfig(engine="vllm", model_name="test-model")
        return LocalServer(
            server_id="local-0",
            config=cfg,
            endpoint_url="http://localhost:8000/v1",
            status=status,
            started_at=1000.0 if status == "running" else 0.0,
        )

    def test_uptime_when_stopped(self):
        server = self._make_server(status="stopped")
        assert server.uptime_seconds == 0.0

    @patch("general_ludd.infra.local_inference.time")
    def test_uptime_when_running(self, mock_time):
        mock_time.time.return_value = 1010.0
        server = self._make_server(status="running")
        assert server.uptime_seconds == pytest.approx(10.0)

    def test_is_running_false_when_stopped(self):
        server = self._make_server(status="stopped")
        assert server.is_running is False

    def test_is_running_true(self):
        server = self._make_server(status="running")
        mock_proc = MagicMock()
        server.process = mock_proc
        assert server.is_running is True

    def test_is_running_false_when_no_process(self):
        server = self._make_server(status="running")
        server.process = None
        assert server.is_running is False


class TestCreateServer:
    def test_create_vllm_server(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="llama3")
        server = mgr.create_server(cfg)
        assert server.server_id == "local-0"
        assert server.config.engine == "vllm"
        assert server.config.model_name == "llama3"
        assert server.endpoint_url == "http://localhost:8000/v1"
        assert server.status == "stopped"

    def test_create_llamacpp_server(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="llamacpp",
            model_path="/data/models/llama.gguf",
            host="0.0.0.0",
            port=9000,
        )
        server = mgr.create_server(cfg)
        assert server.server_id == "local-0"
        assert server.config.engine == "llamacpp"
        assert server.endpoint_url == "http://0.0.0.0:9000/v1"

    def test_create_multiple_servers_increments_id(self):
        mgr = LocalInferenceManager()
        s1 = mgr.create_server(LocalServerConfig(model_name="a"))
        s2 = mgr.create_server(LocalServerConfig(model_name="b"))
        assert s1.server_id == "local-0"
        assert s2.server_id == "local-1"


class TestBuildCommand:
    def test_vllm_command_with_model_name(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="llama3", host="0.0.0.0", port=9999)
        cmd = mgr._build_command(cfg)
        assert cmd[:4] == ["vllm", "serve", "llama3", "--host"]
        assert "0.0.0.0" in cmd
        assert "--port" in cmd
        assert "9999" in cmd

    def test_vllm_command_falls_back_to_model_path(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_path="/models/llama.gguf")
        cmd = mgr._build_command(cfg)
        assert cmd[2] == "/models/llama.gguf"

    def test_llamacpp_command(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="llamacpp",
            model_path="/data/llama.gguf",
            gpu_layers=40,
            context_size=8192,
        )
        cmd = mgr._build_command(cfg)
        assert cmd[0] == "python3"
        assert cmd[1] == "-m"
        assert cmd[2] == "llama_cpp.server"
        assert "--model" in cmd
        assert "/data/llama.gguf" in cmd
        assert "--n_gpu_layers" in cmd
        assert "40" in cmd
        assert "--n_ctx" in cmd
        assert "8192" in cmd

    def test_extra_args_appended(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="vllm",
            model_name="m",
            extra_args=["--tensor-parallel-size", "2"],
        )
        cmd = mgr._build_command(cfg)
        assert cmd[-2:] == ["--tensor-parallel-size", "2"]

    def test_unsupported_engine_raises(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="ollama", model_name="m")
        with pytest.raises(ValueError, match="Unsupported engine"):
            mgr._build_command(cfg)


class TestListAndGetServers:
    def test_list_all_servers(self):
        mgr = LocalInferenceManager()
        mgr.create_server(LocalServerConfig(model_name="a"))
        mgr.create_server(LocalServerConfig(model_name="b"))
        assert len(mgr.list_servers()) == 2

    def test_list_servers_by_status(self):
        mgr = LocalInferenceManager()
        s1 = mgr.create_server(LocalServerConfig(model_name="a"))
        mgr.create_server(LocalServerConfig(model_name="b"))
        s1.status = "running"
        s1.process = MagicMock()
        running = mgr.list_servers(status="running")
        assert len(running) == 1
        assert running[0].server_id == "local-0"

    def test_get_server_found(self):
        mgr = LocalInferenceManager()
        mgr.create_server(LocalServerConfig(model_name="a"))
        server = mgr.get_server("local-0")
        assert server is not None
        assert server.server_id == "local-0"

    def test_get_server_not_found(self):
        mgr = LocalInferenceManager()
        assert mgr.get_server("local-99") is None


class TestRemoveServer:
    def test_remove_stopped_server(self):
        mgr = LocalInferenceManager()
        mgr.create_server(LocalServerConfig(model_name="a"))
        mgr.remove_server("local-0")
        assert mgr.get_server("local-0") is None

    def test_remove_running_server_raises(self):
        mgr = LocalInferenceManager()
        server = mgr.create_server(LocalServerConfig(model_name="a"))
        server.status = "running"
        server.process = MagicMock()
        with pytest.raises(RuntimeError, match="Cannot remove running server"):
            mgr.remove_server("local-0")

    def test_remove_nonexistent_is_noop(self):
        mgr = LocalInferenceManager()
        mgr.remove_server("local-99")


class TestGetEndpoints:
    def test_endpoints_only_running(self):
        mgr = LocalInferenceManager()
        s1 = mgr.create_server(LocalServerConfig(model_name="a"))
        mgr.create_server(LocalServerConfig(model_name="b", port=8001))
        s1.status = "running"
        s1.process = MagicMock()
        endpoints = mgr.get_endpoints()
        assert endpoints == {"local-0": "http://localhost:8000/v1"}

    def test_no_endpoints_when_none_running(self):
        mgr = LocalInferenceManager()
        mgr.create_server(LocalServerConfig(model_name="a"))
        assert mgr.get_endpoints() == {}


class TestStartServer:
    @pytest.mark.asyncio
    async def test_start_creates_subprocess(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="llama3")
        mgr.create_server(cfg)
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        with patch(
            "general_ludd.infra.local_inference.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            result = await mgr.start_server("local-0")
            mock_exec.assert_called_once()
            assert result.status == "running"
            assert result.pid == 12345
            assert result.process is mock_proc

    @pytest.mark.asyncio
    async def test_start_publishes_event(self):
        bus = EventBus()
        mgr = LocalInferenceManager(event_bus=bus)
        cfg = LocalServerConfig(engine="vllm", model_name="llama3")
        mgr.create_server(cfg)
        events = []
        bus.subscribe("custom", lambda e: events.append(e))
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        with patch("general_ludd.infra.local_inference.asyncio.create_subprocess_exec", return_value=mock_proc):
            await mgr.start_server("local-0")
        assert len(events) == 1
        assert events[0].payload["name"] == "local_server_started"
        assert events[0].payload["server_id"] == "local-0"

    @pytest.mark.asyncio
    async def test_start_nonexistent_raises(self):
        mgr = LocalInferenceManager()
        with pytest.raises(ValueError, match="not found"):
            await mgr.start_server("local-99")

    @pytest.mark.asyncio
    async def test_start_already_running_is_noop(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="llama3")
        server = mgr.create_server(cfg)
        server.status = "running"
        server.process = MagicMock()
        with patch("general_ludd.infra.local_inference.asyncio.create_subprocess_exec") as mock_exec:
            result = await mgr.start_server("local-0")
            mock_exec.assert_not_called()
            assert result.server_id == "local-0"


class TestStopServer:
    @pytest.mark.asyncio
    async def test_stop_terminates_process(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="llama3")
        server = mgr.create_server(cfg)
        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        server.process = mock_proc
        server.status = "running"
        server.pid = 12345
        await mgr.stop_server("local-0")
        mock_proc.terminate.assert_called_once()
        assert server.status == "stopped"
        assert server.process is None
        assert server.pid is None

    @pytest.mark.asyncio
    async def test_stop_kills_on_timeout(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="llama3")
        server = mgr.create_server(cfg)
        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)
        server.process = mock_proc
        server.status = "running"
        await mgr.stop_server("local-0")
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_nonexistent_is_noop(self):
        mgr = LocalInferenceManager()
        await mgr.stop_server("local-99")

    @pytest.mark.asyncio
    async def test_stop_already_stopped_is_noop(self):
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="llama3")
        server = mgr.create_server(cfg)
        server.status = "stopped"
        server.process = None
        await mgr.stop_server("local-0")
        assert server.status == "stopped"


class TestStopAll:
    @pytest.mark.asyncio
    async def test_stop_all_servers(self):
        mgr = LocalInferenceManager()
        s1 = mgr.create_server(LocalServerConfig(model_name="a"))
        s2 = mgr.create_server(LocalServerConfig(model_name="b", port=8001))
        for s in [s1, s2]:
            mock_proc = AsyncMock()
            mock_proc.returncode = None
            mock_proc.wait = AsyncMock()
            s.process = mock_proc
            s.status = "running"
            s.pid = 12345
        await mgr.stop_all()
        assert s1.status == "stopped"
        assert s2.status == "stopped"
