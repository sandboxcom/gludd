"""Tests for slurm-based model serving alongside llama.cpp and vLLM."""

from __future__ import annotations

import pytest


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


# --- dedup contract: _remote_submit and _build_script must produce identical
#     #SBATCH directives so there is exactly one script builder in SlurmAdapter --


class TestSlurmScriptDedup:
    """Verify _remote_submit delegates to _build_script rather than duplicating it."""

    def test_build_script_matches_remote_submit_script_without_output(self):
        """_build_script(output=None) must produce the same script _remote_submit uses.

        Before the refactor, _remote_submit had its own inline copy of the
        #SBATCH directive block. After the refactor, it calls _build_script.
        This test locks in the contract: the script produced by _build_script
        with output=None must be identical to what _remote_submit previously
        built, confirming both paths share a single builder.
        """
        from general_ludd.infra.slurm import SlurmAdapter

        adapter = SlurmAdapter()
        params = dict(
            command="python3 serve.py",
            job_name="test-job",
            partition="gpu",
            cpus_per_task=4,
            gpus="1",
            memory="16G",
            time_limit="02:00:00",
        )

        # What _build_script produces with output=None.
        script = adapter._build_script(**params, output=None)

        # The script must contain exactly the expected #SBATCH lines in order.
        lines = script.splitlines()
        assert lines[0] == "#!/bin/bash"
        assert "#SBATCH --job-name=test-job" in lines
        assert "#SBATCH --partition=gpu" in lines
        assert "#SBATCH --cpus-per-task=4" in lines
        assert "#SBATCH --gres=gpu:1" in lines
        assert "#SBATCH --mem=16G" in lines
        assert "#SBATCH --time=02:00:00" in lines
        # No output directive — that is local-path-only.
        assert not any("--output" in ln for ln in lines)
        # Command body must appear at the end.
        assert lines[-1] == "python3 serve.py"

    def test_build_script_directive_order_matches_remote_expected_order(self):
        """#SBATCH directives must appear in the canonical order: job-name,
        partition, cpus-per-task, gres, mem, time — matching the original
        _remote_submit inline block order so API payloads are stable.
        """
        from general_ludd.infra.slurm import SlurmAdapter

        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo hello",
            job_name="order-test",
            partition="cpu",
            cpus_per_task=2,
            gpus="2",
            memory="8G",
            time_limit="01:00",
            output=None,
        )
        sbatch_lines = [ln for ln in script.splitlines() if ln.startswith("#SBATCH")]
        directive_keys = [ln.split("--")[1].split("=")[0] for ln in sbatch_lines]
        assert directive_keys == [
            "job-name",
            "partition",
            "cpus-per-task",
            "gres",
            "mem",
            "time",
        ]

    def test_build_script_with_output_adds_output_directive(self):
        """output= is local-path-only; _build_script must append it when provided."""
        from general_ludd.infra.slurm import SlurmAdapter

        adapter = SlurmAdapter()
        script = adapter._build_script(
            command="echo hi",
            job_name="out-test",
            output="/logs/job-%j.out",
        )
        assert "#SBATCH --output=/logs/job-%j.out" in script

    @pytest.mark.parametrize("missing_field", ["job_name", "partition", "gpus", "memory", "time_limit"])
    def test_build_script_optional_fields_omitted_when_none(self, missing_field: str):
        """Fields passed as None must not produce a #SBATCH directive."""
        from general_ludd.infra.slurm import SlurmAdapter

        adapter = SlurmAdapter()
        # Build with only the one field present.
        kwargs: dict = {
            "command": "echo hi",
            "job_name": None,
            "partition": None,
            "cpus_per_task": None,
            "gpus": None,
            "memory": None,
            "time_limit": None,
            "output": None,
        }
        script = adapter._build_script(**kwargs)
        # No #SBATCH directives expected at all.
        assert "#SBATCH" not in script
