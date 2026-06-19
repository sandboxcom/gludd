"""Unit tests for src/general_ludd/infra/local_inference.py.

No subprocess spawning, no asyncio.run, no real server connections.
Covers validators, LocalServerConfig, LocalServer, and LocalInferenceManager sync methods.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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

# ---------------------------------------------------------------------------
# _has_shell_metachars
# ---------------------------------------------------------------------------

def test_has_shell_metachars_clean_string():
    assert _has_shell_metachars("cleanmodel/path") is False


def test_has_shell_metachars_semicolon():
    assert _has_shell_metachars("model;rm") is True


def test_has_shell_metachars_pipe():
    assert _has_shell_metachars("model|evil") is True


def test_has_shell_metachars_dollar():
    assert _has_shell_metachars("$HOME") is True


def test_has_shell_metachars_space():
    assert _has_shell_metachars("model name") is True


# ---------------------------------------------------------------------------
# _validate_model
# ---------------------------------------------------------------------------

def test_validate_model_valid():
    assert _validate_model("mistral-7b") == "mistral-7b"


def test_validate_model_path():
    assert _validate_model("/models/llama.gguf") == "/models/llama.gguf"


def test_validate_model_empty_raises():
    with pytest.raises(ValueError, match="non-empty string"):
        _validate_model("")


def test_validate_model_non_string_raises():
    with pytest.raises(ValueError):
        _validate_model(None)  # type: ignore[arg-type]


def test_validate_model_leading_dash_raises():
    with pytest.raises(ValueError, match="argv flag injection"):
        _validate_model("--evil")


def test_validate_model_shell_metachars_raises():
    with pytest.raises(ValueError, match="forbidden"):
        _validate_model("model;whoami")


# ---------------------------------------------------------------------------
# _validate_host
# ---------------------------------------------------------------------------

def test_validate_host_localhost():
    assert _validate_host("localhost") == "localhost"


def test_validate_host_loopback_ip():
    assert _validate_host("127.0.0.1") == "127.0.0.1"


def test_validate_host_nonloopback_rejected_by_default():
    with pytest.raises(ValueError, match="loopback"):
        _validate_host("192.168.1.1")


def test_validate_host_nonloopback_allowed_explicitly():
    assert _validate_host("compute-node-01", allow_nonloopback=True) == "compute-node-01"


def test_validate_host_empty_raises():
    with pytest.raises(ValueError, match="non-empty string"):
        _validate_host("")


def test_validate_host_shell_metachars_raises():
    with pytest.raises(ValueError, match="invalid host"):
        _validate_host("host;evil")


# ---------------------------------------------------------------------------
# _validate_port
# ---------------------------------------------------------------------------

def test_validate_port_valid():
    assert _validate_port(8000) == 8000


def test_validate_port_min():
    assert _validate_port(1) == 1


def test_validate_port_max():
    assert _validate_port(65535) == 65535


def test_validate_port_zero_raises():
    with pytest.raises(ValueError, match="out of range"):
        _validate_port(0)


def test_validate_port_above_max_raises():
    with pytest.raises(ValueError, match="out of range"):
        _validate_port(65536)


def test_validate_port_bool_raises():
    with pytest.raises(ValueError, match="must be an int"):
        _validate_port(True)


def test_validate_port_string_raises():
    with pytest.raises(ValueError, match="must be an int"):
        _validate_port("8000")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _validate_extra_args
# ---------------------------------------------------------------------------

def test_validate_extra_args_valid():
    args = ["--tensor-parallel-size", "2"]
    assert _validate_extra_args(args) == args


def test_validate_extra_args_empty_list():
    assert _validate_extra_args([]) == []


def test_validate_extra_args_metachars_raises():
    with pytest.raises(ValueError, match="forbidden"):
        _validate_extra_args(["--flag", "val;evil"])


def test_validate_extra_args_non_string_raises():
    with pytest.raises(ValueError, match="must be strings"):
        _validate_extra_args([123])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# LocalServerConfig defaults
# ---------------------------------------------------------------------------

def test_local_server_config_defaults():
    cfg = LocalServerConfig()
    assert cfg.engine == "vllm"
    assert cfg.host == "localhost"
    assert cfg.port == 8000
    assert cfg.gpu_layers == -1
    assert cfg.context_size == 4096
    assert cfg.extra_args == []
    assert cfg.startup_timeout == 120.0
    assert cfg.allow_nonloopback is False


# ---------------------------------------------------------------------------
# LocalServer dataclass / properties
# ---------------------------------------------------------------------------

def test_local_server_not_running_by_default():
    cfg = LocalServerConfig()
    srv = LocalServer(server_id="s0", config=cfg)
    assert srv.is_running is False
    assert srv.uptime_seconds == 0.0


def test_local_server_is_running_with_process():
    cfg = LocalServerConfig()
    srv = LocalServer(
        server_id="s1",
        config=cfg,
        status="running",
        process=MagicMock(),
        started_at=1_000_000.0,
    )
    assert srv.is_running is True
    assert srv.uptime_seconds > 0.0


def test_local_server_uptime_zero_when_stopped():
    cfg = LocalServerConfig()
    srv = LocalServer(server_id="s2", config=cfg, status="stopped")
    assert srv.uptime_seconds == 0.0


# ---------------------------------------------------------------------------
# LocalInferenceManager — sync methods
# ---------------------------------------------------------------------------

def test_create_server_returns_server():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(model_name="mistral-7b")
    srv = mgr.create_server(cfg)
    assert srv.server_id == "local-0"
    assert srv.endpoint_url == "http://localhost:8000/v1"
    assert srv.status == "stopped"


def test_create_server_increments_id():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(model_name="m1")
    s1 = mgr.create_server(cfg)
    s2 = mgr.create_server(cfg)
    assert s1.server_id == "local-0"
    assert s2.server_id == "local-1"


def test_get_server_found():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(model_name="m1")
    srv = mgr.create_server(cfg)
    assert mgr.get_server(srv.server_id) is srv


def test_get_server_not_found():
    mgr = LocalInferenceManager()
    assert mgr.get_server("nonexistent") is None


def test_list_servers_all():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(model_name="m1")
    mgr.create_server(cfg)
    mgr.create_server(cfg)
    assert len(mgr.list_servers()) == 2


def test_list_servers_status_filter():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(model_name="m1")
    srv = mgr.create_server(cfg)
    # Manually set one server to running
    srv.status = "running"
    srv.process = MagicMock()
    mgr.create_server(cfg)  # second remains "stopped"

    running = mgr.list_servers(status="running")
    stopped = mgr.list_servers(status="stopped")
    assert len(running) == 1
    assert len(stopped) == 1


def test_list_servers_empty():
    mgr = LocalInferenceManager()
    assert mgr.list_servers() == []


def test_get_endpoints_only_running():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(model_name="m1")
    s1 = mgr.create_server(cfg)
    s2 = mgr.create_server(cfg)
    # Mark s1 as running
    s1.status = "running"
    s1.process = MagicMock()
    endpoints = mgr.get_endpoints()
    assert s1.server_id in endpoints
    assert s2.server_id not in endpoints


def test_get_endpoints_empty_when_none_running():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(model_name="m1")
    mgr.create_server(cfg)
    assert mgr.get_endpoints() == {}


def test_remove_server_stopped():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(model_name="m1")
    srv = mgr.create_server(cfg)
    sid = srv.server_id
    mgr.remove_server(sid)
    assert mgr.get_server(sid) is None


def test_remove_server_running_raises():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(model_name="m1")
    srv = mgr.create_server(cfg)
    srv.status = "running"
    srv.process = MagicMock()
    with pytest.raises(RuntimeError, match="Cannot remove running"):
        mgr.remove_server(srv.server_id)


def test_remove_server_nonexistent_noop():
    mgr = LocalInferenceManager()
    # Should not raise
    mgr.remove_server("does-not-exist")


# ---------------------------------------------------------------------------
# LocalInferenceManager — _build_command (sync)
# ---------------------------------------------------------------------------

def test_build_command_vllm():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(engine="vllm", model_name="mistral-7b", host="localhost", port=8000)
    cmd = mgr._build_command(cfg)
    assert cmd[0] == "vllm"
    assert "mistral-7b" in cmd
    assert "--host" in cmd
    assert "--port" in cmd


def test_build_command_llamacpp():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(engine="llamacpp", model_path="/models/q4.gguf", host="localhost", port=8001)
    cmd = mgr._build_command(cfg)
    assert "python3" in cmd
    assert "--model" in cmd
    assert "/models/q4.gguf" in cmd
    assert "--n_gpu_layers" in cmd
    assert "--n_ctx" in cmd


def test_build_command_slurm():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(
        engine="slurm",
        model_name="llama3",
        host="compute-node",
        port=9000,
        allow_nonloopback=True,
    )
    cmd = mgr._build_command(cfg)
    assert cmd[0] == "sbatch"
    assert "--wrap" in cmd


def test_build_command_unknown_engine_raises():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(engine="unknown", model_name="m", host="localhost", port=8000)
    with pytest.raises(ValueError, match="Unsupported engine"):
        mgr._build_command(cfg)


def test_build_command_vllm_with_extra_args():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(
        engine="vllm",
        model_name="mistral-7b",
        host="localhost",
        port=8000,
        extra_args=["--tensor-parallel-size", "2"],
    )
    cmd = mgr._build_command(cfg)
    assert "--tensor-parallel-size" in cmd
    assert "2" in cmd


def test_build_command_rejects_metachar_in_extra_args():
    mgr = LocalInferenceManager()
    cfg = LocalServerConfig(
        engine="vllm",
        model_name="m",
        host="localhost",
        port=8000,
        extra_args=["--flag", "val;evil"],
    )
    with pytest.raises(ValueError, match="forbidden"):
        mgr._build_command(cfg)
