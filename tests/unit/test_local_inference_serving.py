"""Tests for slurm-based model serving alongside llama.cpp and vLLM."""

from __future__ import annotations


class TestSlurmInference:
    def test_slurm_is_valid_engine_option(self):
        from general_ludd.infra.local_inference import LocalServerConfig
        cfg = LocalServerConfig(engine="slurm", model_path="/models/llama-7b")
        assert cfg.engine == "slurm"

    def test_slurm_build_command_produces_sbatch(self):
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="slurm",
            model_path="/models/llama-7b",
            model_name="llama-7b",
            host="gpu01",
            port=8000,
            extra_args=["--partition=gpu", "--gres=gpu:1"],
        )
        cmd = mgr._build_command(cfg)
        assert "sbatch" in cmd
        assert "--partition=gpu" in cmd
        assert "--gres=gpu:1" in cmd

    def test_slurm_command_includes_model_name(self):
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="slurm",
            model_name="llama-7b",
            extra_args=["--gres=gpu:1"],
        )
        cmd = mgr._build_command(cfg)
        cmd_str = " ".join(cmd)
        assert "llama-7b" in cmd_str

    def test_llamacpp_command_format(self):
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="llamacpp",
            model_path="/models/llama-7b.gguf",
            host="localhost",
            port=8080,
            gpu_layers=32,
            context_size=8192,
        )
        cmd = mgr._build_command(cfg)
        assert "--model" in cmd
        assert "/models/llama-7b.gguf" in cmd
        assert "--port" in cmd
        assert "8080" in cmd
        assert "--n_gpu_layers" in cmd
        assert "32" in cmd

    def test_vllm_command_format(self):
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(
            engine="vllm",
            model_name="meta-llama/Llama-3.2-1B",
            host="0.0.0.0",
            port=8000,
        )
        cmd = mgr._build_command(cfg)
        assert "vllm" in cmd
        assert "serve" in cmd
        assert "meta-llama/Llama-3.2-1B" in cmd

    def test_unsupported_engine_raises(self):
        import pytest

        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="unsupported_engine")
        with pytest.raises(ValueError, match="Unsupported engine"):
            mgr._build_command(cfg)

    def test_local_server_config_defaults(self):
        from general_ludd.infra.local_inference import LocalServerConfig
        cfg = LocalServerConfig()
        assert cfg.engine == "vllm"
        assert cfg.host == "localhost"
        assert cfg.port == 8000
        assert cfg.gpu_layers == -1
        assert cfg.context_size == 4096
        assert cfg.extra_args == []

    def test_local_server_dataclass_fields(self):
        from general_ludd.infra.local_inference import LocalServer, LocalServerConfig
        cfg = LocalServerConfig(engine="vllm", model_name="test")
        server = LocalServer(server_id="test-1", config=cfg, endpoint_url="http://localhost:8000/v1")
        assert server.server_id == "test-1"
        assert server.status == "stopped"
        assert server.is_running is False
        assert server.uptime_seconds == 0.0
        assert server.pid is None

    def test_manager_create_and_list_servers(self):
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm", model_name="test")
        s1 = mgr.create_server(cfg)
        s2 = mgr.create_server(cfg)
        assert s1.server_id != s2.server_id
        servers = mgr.list_servers()
        assert len(servers) == 2
        active = mgr.list_servers(status="running")
        assert len(active) == 0
        stopped = mgr.list_servers(status="stopped")
        assert len(stopped) == 2

    def test_manager_get_and_remove(self):
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
        mgr = LocalInferenceManager()
        cfg = LocalServerConfig(engine="vllm")
        s1 = mgr.create_server(cfg)
        assert mgr.get_server(s1.server_id) is not None
        assert mgr.get_server("nonexistent") is None
        mgr.remove_server(s1.server_id)
        assert mgr.get_server(s1.server_id) is None
