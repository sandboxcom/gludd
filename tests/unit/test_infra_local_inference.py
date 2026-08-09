from __future__ import annotations

import signal
from typing import Any
from unittest import mock

import httpx
import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import (
    EventType,
    ModelReadyEvent,
)
from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServer,
    LocalServerConfig,
    _has_shell_metachars,
    _validate_extra_args,
    _validate_host,
    _validate_model,
    _validate_port,
)


class TestValidationFunctions:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("", False),
            ("abc", False),
            ("hello world", True),
            ("a;b", True),
            ("x&y", True),
            ("a|b", True),
            ("file.txt", False),
            ("path/to/model", False),
            ('a"b', True),
            ("a'b", True),
            ("a(b", True),
            ("a[b", True),
            ("a{b", True),
            ("a*b", True),
            ("a?b", True),
            ("a!b", True),
            ("a#b", True),
            ("a~b", True),
            ("a\nb", True),
            ("a\tb", True),
            ("a\rb", True),
            ("$HOME", True),
            ("`cmd`", True),
            ("--flag", False),
            ("normal_model_name", False),
            ("a\\b", True),
        ],
    )
    def test_has_shell_metachars(self, value: str, expected: bool) -> None:
        assert _has_shell_metachars(value) == expected

    @pytest.mark.parametrize(
        "model",
        [
            "valid-model",
            "Qwen2.5-Coder-7B-Instruct",
            "meta-llama/Llama-3.1-8B",
            "a" * 100,
            "models--org--name",
        ],
    )
    def test_validate_model_accepts_valid(self, model: str) -> None:
        assert _validate_model(model) == model

    @pytest.mark.parametrize(
        "model,error_msg_part",
        [
            ("", "non-empty"),
            ("-bad", "flag injection"),
            ("--help", "flag injection"),
            ("x; rm -rf /", "forbidden"),
            ("$(whoami)", "forbidden"),
        ],
    )
    def test_validate_model_rejects_invalid(self, model: str, error_msg_part: str) -> None:
        with pytest.raises(ValueError, match=error_msg_part):
            _validate_model(model)

    def test_validate_model_rejects_non_string(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            _validate_model(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="non-empty"):
            _validate_model(42)  # type: ignore[arg-type]

    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
    def test_validate_host_accepts_loopback(self, host: str) -> None:
        assert _validate_host(host) == host

    @pytest.mark.parametrize("host", ["192.168.1.1", "example.com", "0.0.0.0"])
    def test_validate_host_rejects_non_loopback_default(self, host: str) -> None:
        with pytest.raises(ValueError, match="not a loopback"):
            _validate_host(host)

    @pytest.mark.parametrize("host", ["192.168.1.1", "example.com"])
    def test_validate_host_accepts_non_loopback_when_allowed(self, host: str) -> None:
        assert _validate_host(host, allow_nonloopback=True) == host

    @pytest.mark.parametrize(
        "host",
        [
            "",
            "host with space",
            "1.2.3.4; echo",
            "-evil",
            "a" * 300,
        ],
    )
    def test_validate_host_rejects_invalid(self, host: str) -> None:
        with pytest.raises(ValueError):
            _validate_host(host)

    def test_validate_host_rejects_non_string(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            _validate_host(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="non-empty"):
            _validate_host([])  # type: ignore[arg-type]

    @pytest.mark.parametrize("port", [1, 80, 443, 8000, 65535])
    def test_validate_port_accepts_valid(self, port: int) -> None:
        assert _validate_port(port) == port

    @pytest.mark.parametrize("port", [0, -1, 65536, 100000])
    def test_validate_port_rejects_out_of_range(self, port: int) -> None:
        with pytest.raises(ValueError, match="out of range"):
            _validate_port(port)

    def test_validate_port_rejects_bool(self) -> None:
        with pytest.raises(ValueError, match="port must be an int"):
            _validate_port(True)
        with pytest.raises(ValueError, match="port must be an int"):
            _validate_port(False)

    def test_validate_port_rejects_non_int(self) -> None:
        with pytest.raises(ValueError, match="port must be an int"):
            _validate_port("8000")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="port must be an int"):
            _validate_port(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="port must be an int"):
            _validate_port(8000.5)  # type: ignore[arg-type]

    def test_validate_extra_args_accepts_valid(self) -> None:
        assert _validate_extra_args(["--n-gpu-layers", "32"]) == ["--n-gpu-layers", "32"]

    def test_validate_extra_args_empty_list(self) -> None:
        assert _validate_extra_args([]) == []

    def test_validate_extra_args_rejects_non_string_entry(self) -> None:
        with pytest.raises(ValueError, match="entries must be strings"):
            _validate_extra_args(["--ok", 123])  # type: ignore[list-item]

    def test_validate_extra_args_rejects_shell_metachar_entry(self) -> None:
        with pytest.raises(ValueError, match="forbidden characters"):
            _validate_extra_args(["--safe", "x; echo bad"])


class TestLocalServerConfig:
    def test_defaults(self) -> None:
        cfg = LocalServerConfig()
        assert cfg.engine == "vllm"
        assert cfg.model_path == ""
        assert cfg.model_name == ""
        assert cfg.host == "localhost"
        assert cfg.port == 8000
        assert cfg.gpu_layers == -1
        assert cfg.context_size == 4096
        assert cfg.extra_args == []
        assert cfg.startup_timeout == 120.0
        assert cfg.allow_nonloopback is False

    def test_custom_config(self) -> None:
        cfg = LocalServerConfig(
            engine="llamacpp",
            model_path="/models/qwen.gguf",
            model_name="qwen",
            host="127.0.0.1",
            port=8080,
            gpu_layers=35,
            context_size=8192,
            extra_args=["--verbose"],
            startup_timeout=300.0,
            allow_nonloopback=True,
        )
        assert cfg.engine == "llamacpp"
        assert cfg.model_path == "/models/qwen.gguf"
        assert cfg.model_name == "qwen"
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8080
        assert cfg.gpu_layers == 35
        assert cfg.context_size == 8192
        assert cfg.extra_args == ["--verbose"]
        assert cfg.startup_timeout == 300.0
        assert cfg.allow_nonloopback is True


class TestLocalServer:
    def test_default_properties(self) -> None:
        cfg = LocalServerConfig()
        srv = LocalServer(server_id="test-1", config=cfg)
        assert srv.server_id == "test-1"
        assert srv.config is cfg
        assert srv.process is None
        assert srv.status == "stopped"
        assert srv.started_at == 0.0
        assert srv.pid is None
        assert srv.stderr_path is None
        assert srv.endpoint_url == ""

    def test_endpoint_url_from_config_host_port(self) -> None:
        cfg = LocalServerConfig(host="127.0.0.1", port=9000)
        srv = LocalServer(server_id="srv", config=cfg, endpoint_url="http://127.0.0.1:9000/v1")
        assert srv.endpoint_url == "http://127.0.0.1:9000/v1"

    def test_uptime_seconds_stopped(self) -> None:
        srv = LocalServer(server_id="s1", config=LocalServerConfig())
        assert srv.uptime_seconds == 0.0

    def test_uptime_seconds_running(self) -> None:
        srv = LocalServer(server_id="s1", config=LocalServerConfig(), status="running", started_at=100.0)
        with mock.patch("general_ludd.infra.local_inference.time.time", return_value=135.0):
            assert srv.uptime_seconds == 35.0

    def test_uptime_seconds_errored(self) -> None:
        srv = LocalServer(server_id="s1", config=LocalServerConfig(), status="error", started_at=100.0)
        assert srv.uptime_seconds == 0.0

    def test_is_running_true(self) -> None:
        srv = LocalServer(server_id="s1", config=LocalServerConfig(), status="running", process=mock.MagicMock())
        assert srv.is_running is True

    def test_is_running_false_no_process(self) -> None:
        srv = LocalServer(server_id="s1", config=LocalServerConfig(), status="running", process=None)
        assert srv.is_running is False

    def test_is_running_false_wrong_status(self) -> None:
        srv = LocalServer(server_id="s1", config=LocalServerConfig(), status="stopped", process=mock.MagicMock())
        assert srv.is_running is False


class TestLocalInferenceManager:
    def test_init_empty(self) -> None:
        mgr = LocalInferenceManager()
        assert mgr.list_servers() == []
        assert mgr.ansible_adapter is None
        assert mgr._next_id == 0

    def test_init_with_event_bus(self) -> None:
        bus = EventBus()
        mgr = LocalInferenceManager(event_bus=bus)
        assert mgr._event_bus is bus

    def test_ansible_adapter_getter_setter(self) -> None:
        mgr = LocalInferenceManager()
        assert mgr.ansible_adapter is None
        fake = object()
        mgr.ansible_adapter = fake  # type: ignore[assignment]
        assert mgr.ansible_adapter is fake
        mgr.ansible_adapter = None
        assert mgr.ansible_adapter is None

    # ── create_server ──────────────────────────────────────────────────

    def test_create_server_sequential_ids(self) -> None:
        mgr = LocalInferenceManager()
        s1 = mgr.create_server(LocalServerConfig())
        s2 = mgr.create_server(LocalServerConfig())
        assert s1.server_id == "local-0"
        assert s2.server_id == "local-1"
        assert s1.endpoint_url == "http://localhost:8000/v1"
        assert s2.endpoint_url == "http://localhost:8000/v1"

    def test_create_server_custom_host_port(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(host="127.0.0.1", port=9000)
        srv = mgr.create_server(cfg)
        assert srv.endpoint_url == "http://127.0.0.1:9000/v1"

    def test_create_server_emits_no_event_without_bus(self) -> None:
        mgr = LocalInferenceManager()
        mgr.create_server(LocalServerConfig())

    # ── list_servers ───────────────────────────────────────────────────

    def test_list_servers_no_filter(self) -> None:
        mgr = LocalInferenceManager()
        s1 = mgr.create_server(LocalServerConfig())
        s2 = mgr.create_server(LocalServerConfig())
        assert mgr.list_servers() == [s1, s2]

    def test_list_servers_filter_by_status(self) -> None:
        mgr = LocalInferenceManager()
        s1 = mgr.create_server(LocalServerConfig())
        s2 = mgr.create_server(LocalServerConfig())
        s2.status = "running"
        assert mgr.list_servers(status="running") == [s2]
        assert mgr.list_servers(status="stopped") == [s1]
        assert mgr.list_servers(status="error") == []

    def test_list_servers_empty(self) -> None:
        mgr = LocalInferenceManager()
        assert mgr.list_servers() == []
        assert mgr.list_servers(status="running") == []

    # ── get_server ─────────────────────────────────────────────────────

    def test_get_server_exists(self) -> None:
        mgr = LocalInferenceManager()
        srv = mgr.create_server(LocalServerConfig())
        assert mgr.get_server("local-0") is srv

    def test_get_server_missing(self) -> None:
        mgr = LocalInferenceManager()
        assert mgr.get_server("nonexistent") is None

    # ── get_endpoints ──────────────────────────────────────────────────

    def test_get_endpoints_returns_only_running(self) -> None:
        mgr = LocalInferenceManager()
        mgr.create_server(LocalServerConfig())
        s2 = mgr.create_server(LocalServerConfig(port=8001))
        s2.status = "running"
        s2.process = mock.MagicMock()
        assert mgr.get_endpoints() == {"local-1": s2.endpoint_url}

    def test_get_endpoints_empty(self) -> None:
        mgr = LocalInferenceManager()
        assert mgr.get_endpoints() == {}

    # ── remove_server ──────────────────────────────────────────────────

    def test_remove_server_stopped(self) -> None:
        mgr = LocalInferenceManager()
        mgr.create_server(LocalServerConfig())
        mgr.remove_server("local-0")
        assert mgr.get_server("local-0") is None

    def test_remove_server_unknown_is_noop(self) -> None:
        mgr = LocalInferenceManager()
        mgr.remove_server("nope")

    def test_remove_server_running_raises(self) -> None:
        mgr = LocalInferenceManager()
        srv = mgr.create_server(LocalServerConfig())
        srv.status = "running"
        srv.process = mock.MagicMock()
        with pytest.raises(RuntimeError, match="Stop it first"):
            mgr.remove_server("local-0")

    # ── _emit ──────────────────────────────────────────────────────────

    def test_emit_no_bus(self) -> None:
        mgr = LocalInferenceManager()
        mgr._emit(ModelReadyEvent(server_id="s", engine="vllm", endpoint_url="http://x:8/v1"))

    def test_emit_with_bus(self) -> None:
        bus = EventBus()
        events: list[Any] = []
        bus.subscribe(EventType.MODEL_READY, lambda e: events.append(e))
        mgr = LocalInferenceManager(event_bus=bus)
        event = ModelReadyEvent(server_id="s", engine="vllm", endpoint_url="http://x:8/v1")
        mgr._emit(event)
        assert len(events) == 1
        assert events[0] is event

    # ── stop_server ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stop_server_unknown_noop(self) -> None:
        mgr = LocalInferenceManager()
        await mgr.stop_server("nope")

    @pytest.mark.asyncio
    async def test_stop_server_stopped_already(self) -> None:
        mgr = LocalInferenceManager()
        srv = mgr.create_server(LocalServerConfig())
        srv.process = None
        srv.pid = 12345
        with mock.patch("os.kill") as mk:
            await mgr.stop_server(srv.server_id)
            mk.assert_not_called()
        assert srv.status == "stopped"
        assert srv.process is None
        assert srv.pid is None

    @pytest.mark.asyncio
    async def test_stop_server_pid_only_fallback(self) -> None:
        mgr = LocalInferenceManager()
        srv = mgr.create_server(LocalServerConfig())
        srv.pid = 99999
        srv.status = "running"
        with mock.patch("os.kill") as mk:
            await mgr.stop_server(srv.server_id)
            mk.assert_called_once_with(99999, signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_stop_server_running_process(self) -> None:
        mgr = LocalInferenceManager()
        srv = mgr.create_server(LocalServerConfig())

        proc = mock.AsyncMock()
        proc.pid = 42
        proc.returncode = None
        srv.process = proc

        with (
            mock.patch("os.getpgid", return_value=42),
            mock.patch("os.killpg") as mk,
        ):
            await mgr.stop_server(srv.server_id)
            mk.assert_called_once_with(42, signal.SIGTERM)

        assert srv.status == "stopped"
        assert srv.process is None
        assert srv.pid is None

    @pytest.mark.asyncio
    async def test_stop_server_process_kill_on_timeout(self) -> None:
        mgr = LocalInferenceManager()
        srv = mgr.create_server(LocalServerConfig())

        proc = mock.AsyncMock()
        proc.pid = 42
        proc.returncode = None
        proc.wait = mock.AsyncMock(side_effect=TimeoutError)
        srv.process = proc

        with (
            mock.patch("os.getpgid", return_value=42),
            mock.patch("os.killpg") as mk,
        ):
            await mgr.stop_server(srv.server_id)
            assert mk.call_count == 2
            first_sig = mk.call_args_list[0][0][1]
            second_sig = mk.call_args_list[1][0][1]
            assert first_sig == signal.SIGTERM
            assert second_sig == signal.SIGKILL

    @pytest.mark.asyncio
    async def test_stop_server_ansible_fallback(self) -> None:
        mgr = LocalInferenceManager()
        mgr.ansible_adapter = mock.MagicMock(  # type: ignore[assignment]
            run_playbook=mock.Mock(return_value={"status": "success", "rc": 0})
        )
        srv = mgr.create_server(LocalServerConfig())
        srv.pid = 12345
        await mgr.stop_server(srv.server_id)
        assert srv.status == "stopped"

    @pytest.mark.asyncio
    async def test_stop_server_ansible_error_fallback_kill(self) -> None:
        mgr = LocalInferenceManager()
        mgr.ansible_adapter = mock.MagicMock(  # type: ignore[assignment]
            run_playbook=mock.Mock(side_effect=RuntimeError("boom"))
        )
        srv = mgr.create_server(LocalServerConfig())
        srv.pid = 12345
        with mock.patch("os.kill") as mk:
            await mgr.stop_server(srv.server_id)
            mk.assert_called_once_with(12345, signal.SIGKILL)
        assert srv.status == "stopped"

    @pytest.mark.asyncio
    async def test_stop_server_cleans_stderr_path(self) -> None:
        mgr = LocalInferenceManager()
        srv = mgr.create_server(LocalServerConfig())
        srv.stderr_path = "/tmp/fake-stderr.log"
        with mock.patch("os.unlink") as mu:
            await mgr.stop_server(srv.server_id)
            mu.assert_called_once_with("/tmp/fake-stderr.log")
        assert srv.stderr_path is None

    # ── stop_all ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stop_all(self) -> None:
        mgr = LocalInferenceManager()
        mgr.create_server(LocalServerConfig())
        mgr.create_server(LocalServerConfig())
        await mgr.stop_all()
        for s in mgr.list_servers():
            assert s.status == "stopped"

    # ── _build_command ─────────────────────────────────────────────────

    def test_build_command_vllm(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="meta-llama/Llama-3.1-8B", host="localhost", port=8000)
        cmd = mgr._build_command(cfg)
        assert cmd == ["vllm", "serve", "meta-llama/Llama-3.1-8B", "--host", "localhost", "--port", "8000"]

    def test_build_command_vllm_with_extra_args(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="vllm",
            model_name="m",
            host="localhost",
            port=8000,
            extra_args=["--gpu-memory-utilization", "0.9"],
        )
        cmd = mgr._build_command(cfg)
        assert cmd[-2:] == ["--gpu-memory-utilization", "0.9"]

    def test_build_command_vllm_uses_model_path_fallback(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_path="/models/foo", host="localhost", port=8000)
        cmd = mgr._build_command(cfg)
        assert cmd[2] == "/models/foo"

    def test_build_command_llamacpp(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="llamacpp",
            model_path="/models/qwen.gguf",
            host="localhost",
            port=8080,
            gpu_layers=35,
            context_size=8192,
        )
        cmd = mgr._build_command(cfg)
        assert cmd[0] == "python3"
        assert cmd[1] == "-m"
        assert cmd[2] == "llama_cpp.server"
        assert "--model" in cmd
        assert "/models/qwen.gguf" in cmd
        assert "--host" in cmd
        assert "--port" in cmd
        assert "8080" in cmd
        assert "--n_gpu_layers" in cmd
        assert "35" in cmd
        assert "--n_ctx" in cmd
        assert "8192" in cmd

    def test_build_command_slurm(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="slurm",
            model_name="qwen",
            host="192.168.1.1",
            port=8000,
            gpu_layers=35,
            context_size=4096,
            allow_nonloopback=True,
        )
        cmd = mgr._build_command(cfg)
        assert cmd[0] == "sbatch"
        assert cmd[-2] == "--wrap"
        wrap = cmd[-1]
        assert "python3 -m llama_cpp.server" in wrap
        assert "--model qwen" in wrap
        assert "--host 192.168.1.1" in wrap
        assert "--port 8000" in wrap
        assert "--n_gpu_layers 35" in wrap
        assert "--n_ctx 4096" in wrap

    def test_build_command_unsupported_engine(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="unsupported")
        with pytest.raises(ValueError, match="Unsupported engine"):
            mgr._build_command(cfg)

    def test_build_command_validates_bad_host(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(host="evil; x", engine="vllm", model_name="m")
        with pytest.raises(ValueError, match="invalid host"):
            mgr._build_command(cfg)

    def test_build_command_validates_bad_port_type(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="m")
        cfg.port = "bad"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="port must be an int"):
            mgr._build_command(cfg)

    def test_build_command_validates_gpu_layers_bool(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="m")
        cfg.gpu_layers = True  # type: ignore[assignment]
        with pytest.raises(ValueError, match="gpu_layers must be an int"):
            mgr._build_command(cfg)

    def test_build_command_validates_context_size_bool(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="m")
        cfg.context_size = False  # type: ignore[assignment]
        with pytest.raises(ValueError, match="context_size must be an int"):
            mgr._build_command(cfg)

    # ── start_server — validation / missing / already-running ──────────

    @pytest.mark.asyncio
    async def test_start_server_missing(self) -> None:
        mgr = LocalInferenceManager()
        with pytest.raises(ValueError, match="not found"):
            await mgr.start_server("nope")

    @pytest.mark.asyncio
    async def test_start_server_already_running(self) -> None:
        mgr = LocalInferenceManager()
        srv = mgr.create_server(LocalServerConfig())
        srv.status = "running"
        srv.process = mock.MagicMock()
        result = await mgr.start_server(srv.server_id)
        assert result is srv

    # ── start_server — slurm ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_start_server_slurm(self) -> None:
        mgr = LocalInferenceManager(event_bus=EventBus())
        cfg = LocalServerConfig(engine="slurm", model_name="qwen", allow_nonloopback=True)
        srv = mgr.create_server(cfg)

        with mock.patch("general_ludd.infra.local_inference.SlurmAdapter") as MockSlurm:
            instance = MockSlurm.return_value
            instance.submit.return_value = "12345"
            result = await mgr.start_server(srv.server_id)
            assert result.status == "submitted"

    @pytest.mark.asyncio
    async def test_start_server_slurm_bad_gpu_layers(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="slurm", model_name="qwen", allow_nonloopback=True, gpu_layers=-1)
        cfg.gpu_layers = True  # type: ignore[assignment]
        srv = mgr.create_server(cfg)
        with pytest.raises(ValueError, match="gpu_layers must be an int"):
            await mgr.start_server(srv.server_id)

    @pytest.mark.asyncio
    async def test_start_server_slurm_bad_context_size(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="slurm", model_name="qwen", allow_nonloopback=True)
        cfg.context_size = "large"  # type: ignore[assignment]
        srv = mgr.create_server(cfg)
        with pytest.raises(ValueError, match="context_size must be an int"):
            await mgr.start_server(srv.server_id)

    # ── start_server — ansible path ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_start_server_via_ansible_success(self) -> None:
        mgr = LocalInferenceManager()
        adapter = mock.MagicMock()
        adapter.run_playbook.return_value = {
            "status": "success",
            "rc": 0,
            "facts": {
                "gludd_local_server": {"status": "running", "pid": 1234},
            },
        }
        mgr.ansible_adapter = adapter  # type: ignore[assignment]
        srv = mgr.create_server(LocalServerConfig(model_name="m"))
        result = await mgr.start_server(srv.server_id)
        assert result.status == "running"
        assert result.pid == 1234
        adapter.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_server_via_ansible_playbook_failure(self) -> None:
        mgr = LocalInferenceManager()
        adapter = mock.MagicMock()
        adapter.run_playbook.return_value = {
            "status": "failure",
            "rc": 1,
            "error": "something broke",
        }
        mgr.ansible_adapter = adapter  # type: ignore[assignment]
        srv = mgr.create_server(LocalServerConfig(model_name="m"))
        with pytest.raises(RuntimeError, match="ansible playbook failed"):
            await mgr.start_server(srv.server_id)
        assert srv.status == "error"

    @pytest.mark.asyncio
    async def test_start_server_via_ansible_playbook_rc_nonzero(self) -> None:
        mgr = LocalInferenceManager()
        adapter = mock.MagicMock()
        adapter.run_playbook.return_value = {"status": "ok", "rc": 1, "msg": "died"}
        mgr.ansible_adapter = adapter  # type: ignore[assignment]
        srv = mgr.create_server(LocalServerConfig(model_name="m"))
        with pytest.raises(RuntimeError, match="ansible playbook failed"):
            await mgr.start_server(srv.server_id)

    @pytest.mark.asyncio
    async def test_start_server_via_ansible_not_running_after(self) -> None:
        mgr = LocalInferenceManager()
        adapter = mock.MagicMock()
        adapter.run_playbook.return_value = {
            "status": "success",
            "rc": 0,
            "facts": {
                "gludd_local_server": {"status": "crashed"},
            },
        }
        mgr.ansible_adapter = adapter  # type: ignore[assignment]
        srv = mgr.create_server(LocalServerConfig(model_name="m"))
        with pytest.raises(RuntimeError, match="not reported as running"):
            await mgr.start_server(srv.server_id)
        assert srv.status == "error"

    @pytest.mark.asyncio
    async def test_start_server_via_ansible_uses_ansible_facts_key(self) -> None:
        mgr = LocalInferenceManager()
        adapter = mock.MagicMock()
        adapter.run_playbook.return_value = {
            "status": "success",
            "rc": 0,
            "ansible_facts": {
                "gludd_local_server": {"status": "running", "pid": 5678},
            },
        }
        mgr.ansible_adapter = adapter  # type: ignore[assignment]
        srv = mgr.create_server(LocalServerConfig(model_name="m"))
        result = await mgr.start_server(srv.server_id)
        assert result.status == "running"
        assert result.pid == 5678

    # ── _wait_for_ready ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_wait_for_ready_skip_on_zero_timeout(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(startup_timeout=0)
        srv = LocalServer(server_id="s", config=cfg)
        await mgr._wait_for_ready(srv)

    @pytest.mark.asyncio
    async def test_wait_for_ready_skip_on_negative_timeout(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(startup_timeout=-1)
        srv = LocalServer(server_id="s", config=cfg)
        await mgr._wait_for_ready(srv)

    @pytest.mark.asyncio
    async def test_wait_for_ready_crashed_process(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(startup_timeout=1.0)
        proc = mock.MagicMock()
        proc.returncode = 1
        srv = LocalServer(server_id="s", config=cfg, process=proc, stderr_path="/tmp/stderr")

        with (
            mock.patch("builtins.open", mock.mock_open(read_data=b"crashed!")),
            pytest.raises(RuntimeError, match="exited"),
        ):
            await mgr._wait_for_ready(srv)

        assert srv.status == "error"

    @pytest.mark.asyncio
    async def test_wait_for_ready_health_check_success(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(host="localhost", port=8000, startup_timeout=5.0)
        proc = mock.MagicMock()
        proc.returncode = None
        srv = LocalServer(server_id="s", config=cfg, process=proc)

        resp = mock.MagicMock()
        resp.status_code = 200

        with mock.patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.get.return_value = resp
            await mgr._wait_for_ready(srv)

    @pytest.mark.asyncio
    async def test_wait_for_ready_timeout(self) -> None:
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(host="localhost", port=8000, startup_timeout=0.5)
        proc = mock.MagicMock()
        proc.returncode = None
        srv = LocalServer(server_id="s", config=cfg, process=proc, stderr_path="/tmp/stderr")

        with (
            mock.patch("general_ludd.infra.local_inference.time.time", side_effect=[0, 0, 0, 1000]),
            mock.patch("httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value.__aenter__.return_value.get.side_effect = httpx.ConnectError("refused")
            with pytest.raises(RuntimeError, match="did not become ready"):
                await mgr._wait_for_ready(srv)

    # ── start_server — subprocess path ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_start_server_via_subprocess_basic(self) -> None:
        mgr = LocalInferenceManager(event_bus=EventBus())
        cfg = LocalServerConfig(
            engine="vllm",
            model_name="m",
            host="localhost",
            port=8000,
            startup_timeout=0,
        )
        srv = mgr.create_server(cfg)

        with mock.patch("asyncio.create_subprocess_exec") as mk:
            proc = mock.AsyncMock()
            proc.pid = 12345
            proc.stderr = mock.AsyncMock()
            proc.stderr.read = mock.AsyncMock(side_effect=[b"log", b""])
            mk.return_value = proc

            result = await mgr.start_server(srv.server_id)
            assert result.status == "running"
            assert result.pid == 12345
