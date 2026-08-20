"""Deep tests for terraform.py private helpers and edge-case logic.

Covers:
  - _profile_integer / _profile_float / _profile_boolean / _profile_string
  - _engine_serve_cmd workload-aware flags (vLLM + llama.cpp)
  - _user_data_script workload_type emission
  - _override_apply boolean, zero-numeric, unknown-field edges
  - build_tfvars with deployment_optimization_config + hardware_preset
  - build_azure_containerapp_tfvars validation error paths
  - _generate_azure_containerapp module source rewrite
  - TerraformGenerator with state_backend_selector prepends backend block
  - _terraform_assets_root resolution
  - Legacy output aliases in all module-style provider generators
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.terraform import (
    TerraformGenerator,
    _container_image,
    _default_image,
    _engine_serve_cmd,
    _override_apply,
    _profile_boolean,
    _profile_float,
    _profile_integer,
    _profile_string,
    _terraform_assets_root,
    _user_data_script,
    escape_tfvar_value,
)
from general_ludd.infra.terraform_state import (
    StateBackendConfig,
    StateBackendSelector,
)


def _config(**overrides: object) -> ComputeConfig:
    defaults: dict[str, object] = {
        "provider": ComputeProvider.AWS,
        "gpu_type": GPUType.T4,
        "gpu_count": 1,
        "engine": InferenceEngine.VLLM,
        "model_name": "meta-llama/Llama-2-7b-hf",
        "allowed_cidr": "0.0.0.0/0",
    }
    defaults.update(overrides)
    return ComputeConfig(**defaults)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════
# _profile_* helper validators
# ═══════════════════════════════════════════════════════════════════════════


class TestProfileInteger:
    def test_returns_default_when_key_missing(self) -> None:
        assert _profile_integer({}, "ctx", 4096) == 4096

    def test_returns_configured_value(self) -> None:
        assert _profile_integer({"ctx": 8192}, "ctx", 4096) == 8192

    def test_rejects_float(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            _profile_integer({"ctx": 3.14}, "ctx", 4096)

    def test_rejects_boolean_true(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            _profile_integer({"ctx": True}, "ctx", 4096)

    def test_rejects_boolean_false(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            _profile_integer({"ctx": False}, "ctx", 4096)

    def test_rejects_string(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            _profile_integer({"ctx": "4096"}, "ctx", 4096)

    def test_rejects_none(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            _profile_integer({"ctx": None}, "ctx", 4096)

    def test_accepts_zero(self) -> None:
        assert _profile_integer({"ctx": 0}, "ctx", 4096) == 0

    def test_accepts_negative(self) -> None:
        assert _profile_integer({"ctx": -5}, "ctx", 4096) == -5


class TestProfileFloat:
    def test_returns_default_when_key_missing(self) -> None:
        assert _profile_float({}, "gmu", 0.90) == 0.90

    def test_returns_configured_value(self) -> None:
        assert _profile_float({"gmu": 0.85}, "gmu", 0.90) == 0.85

    def test_accepts_int_as_float(self) -> None:
        assert _profile_float({"gmu": 1}, "gmu", 0.90) == 1.0

    def test_rejects_zero(self) -> None:
        with pytest.raises(ValueError, match="must be between 0 and 1"):
            _profile_float({"gmu": 0}, "gmu", 0.90)

    def test_rejects_above_one(self) -> None:
        with pytest.raises(ValueError, match="must be between 0 and 1"):
            _profile_float({"gmu": 1.01}, "gmu", 0.90)

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="must be between 0 and 1"):
            _profile_float({"gmu": -0.1}, "gmu", 0.90)

    def test_rejects_boolean(self) -> None:
        with pytest.raises(ValueError, match="must be a number"):
            _profile_float({"gmu": True}, "gmu", 0.90)

    def test_rejects_string(self) -> None:
        with pytest.raises(ValueError, match="must be a number"):
            _profile_float({"gmu": "0.90"}, "gmu", 0.90)

    def test_accepts_very_small_positive_float(self) -> None:
        assert _profile_float({"gmu": 0.01}, "gmu", 0.90) == 0.01

    def test_accepts_exactly_one(self) -> None:
        assert _profile_float({"gmu": 1.0}, "gmu", 0.90) == 1.0


class TestProfileBoolean:
    def test_returns_default_when_key_missing(self) -> None:
        assert _profile_boolean({}, "eager", False) is False

    def test_returns_configured_true(self) -> None:
        assert _profile_boolean({"eager": True}, "eager", False) is True

    def test_returns_configured_false(self) -> None:
        assert _profile_boolean({"eager": False}, "eager", True) is False

    def test_rejects_string_true(self) -> None:
        with pytest.raises(ValueError, match="must be a boolean"):
            _profile_boolean({"eager": "true"}, "eager", False)

    def test_rejects_int(self) -> None:
        with pytest.raises(ValueError, match="must be a boolean"):
            _profile_boolean({"eager": 1}, "eager", False)

    def test_rejects_none(self) -> None:
        with pytest.raises(ValueError, match="must be a boolean"):
            _profile_boolean({"eager": None}, "eager", False)


class TestProfileString:
    def test_returns_default_when_key_missing(self) -> None:
        assert _profile_string({}, "quant", "auto") == "auto"

    def test_returns_configured_value(self) -> None:
        assert _profile_string({"quant": "awq"}, "quant", "bf16") == "awq"

    def test_rejects_int(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            _profile_string({"quant": 42}, "quant", "auto")

    def test_rejects_boolean(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            _profile_string({"quant": True}, "quant", "auto")

    def test_rejects_none(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            _profile_string({"quant": None}, "quant", "auto")

    def test_accepts_empty_string(self) -> None:
        assert _profile_string({"quant": ""}, "quant", "auto") == ""


# ═══════════════════════════════════════════════════════════════════════════
# _override_apply deeper edges
# ═══════════════════════════════════════════════════════════════════════════


class TestOverrideApplyDeep:
    def test_boolean_true_override_passes_through(self) -> None:
        ns = SimpleNamespace(enable_structured_outputs=True)
        resolve = _override_apply(ns)
        assert resolve("enable_structured_outputs", False) is True

    def test_boolean_false_override_passes_through(self) -> None:
        ns = SimpleNamespace(enable_structured_outputs=False)
        resolve = _override_apply(ns)
        assert resolve("enable_structured_outputs", True) is False

    def test_zero_int_override_passes_through(self) -> None:
        ns = SimpleNamespace(gpu_count=0)
        resolve = _override_apply(ns)
        assert resolve("gpu_count", 1) == 0

    def test_zero_float_override_passes_through(self) -> None:
        ns = SimpleNamespace(max_cost_usd=0.0)
        resolve = _override_apply(ns)
        assert resolve("max_cost_usd", 10.0) == 0.0

    def test_unknown_field_returns_compute_default(self) -> None:
        ns = SimpleNamespace(gpu_count=4)
        resolve = _override_apply(ns)
        assert resolve("nonexistent_field", 99) == 99

    def test_none_override_falls_back_for_all_types(self) -> None:
        ns = SimpleNamespace(gpu_count=None, model_name=None, max_cost_usd=None)
        resolve = _override_apply(ns)
        assert resolve("gpu_count", 1) == 1
        assert resolve("model_name", "default") == "default"
        assert resolve("max_cost_usd", 5.5) == 5.5


# ═══════════════════════════════════════════════════════════════════════════
# _default_image / _container_image
# ═══════════════════════════════════════════════════════════════════════════


class TestDefaultImage:
    def test_llamacpp_default_image(self) -> None:
        assert _default_image(InferenceEngine.LLAMACPP) == "ghcr.io/ggerganov/llama.cpp:server"

    def test_vllm_default_image(self) -> None:
        assert _default_image(InferenceEngine.VLLM) == "vllm/vllm-openai:latest"


class TestContainerImage:
    def test_uses_explicit_container_image_when_set(self) -> None:
        cfg = _config(container_image="my-registry/my-image:v2")
        assert _container_image(cfg) == "my-registry/my-image:v2"

    def test_falls_back_to_default_when_container_image_is_none(self) -> None:
        cfg = _config(container_image=None, engine=InferenceEngine.VLLM)
        assert _container_image(cfg) == "vllm/vllm-openai:latest"

    def test_falls_back_to_llamacpp_default(self) -> None:
        cfg = _config(container_image=None, engine=InferenceEngine.LLAMACPP)
        assert _container_image(cfg) == "ghcr.io/ggerganov/llama.cpp:server"


# ═══════════════════════════════════════════════════════════════════════════
# _engine_serve_cmd workload-aware deep
# ═══════════════════════════════════════════════════════════════════════════


class TestEngineServeCmdVllm:
    def test_basic_vllm_command_structure(self) -> None:
        cfg = _config(engine=InferenceEngine.VLLM)
        cmd = _engine_serve_cmd(cfg)
        assert "docker run" in cmd
        assert "--gpus all" in cmd
        assert "-p 8000:8000" in cmd
        assert "vllm/vllm-openai:latest" in cmd
        assert "--model" in cmd
        assert "--host" in cmd
        assert "--port 8000" in cmd

    def test_tensor_parallel_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"tensor_parallel": 4},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--tensor-parallel-size 4" in cmd

    def test_tensor_parallel_ignored_when_zero(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"tensor_parallel": 0},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--tensor-parallel-size" not in cmd

    def test_tensor_parallel_ignored_when_one(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"tensor_parallel": 1},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--tensor-parallel-size" not in cmd

    def test_context_length_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="batch_inference",
            deployment_profile={"context_length": 16384},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--max-model-len 16384" in cmd

    def test_context_length_zero_is_ignored(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="batch_inference",
            deployment_profile={"context_length": 0},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--max-model-len" not in cmd

    def test_max_num_seqs_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"max_num_seqs": 128},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--max-num-seqs 128" in cmd

    def test_gpu_memory_utilization_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"gpu_memory_utilization": 0.85},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--gpu-memory-utilization 0.85" in cmd

    def test_enforce_eager_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"enforce_eager": True},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--enforce-eager" in cmd

    def test_enforce_eager_omitted_when_false(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"enforce_eager": False},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--enforce-eager" not in cmd

    def test_quantization_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"quantization": "awq"},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--quantization awq" in cmd

    def test_quantization_bf16_omitted(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"quantization": "bf16"},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--quantization" not in cmd

    def test_quantization_fp16_omitted(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"quantization": "fp16"},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--quantization" not in cmd

    def test_quantization_empty_string_omitted(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={"quantization": ""},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--quantization" not in cmd

    def test_multiple_vllm_flags_combined(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile={
                "tensor_parallel": 2,
                "context_length": 8192,
                "max_num_seqs": 64,
                "gpu_memory_utilization": 0.95,
                "enforce_eager": True,
                "quantization": "gptq",
            },
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--tensor-parallel-size 2" in cmd
        assert "--max-model-len 8192" in cmd
        assert "--max-num-seqs 64" in cmd
        assert "--gpu-memory-utilization 0.95" in cmd
        assert "--enforce-eager" in cmd
        assert "--quantization gptq" in cmd


class TestEngineServeCmdLlamacpp:
    def test_basic_llamacpp_command_structure(self) -> None:
        cfg = _config(engine=InferenceEngine.LLAMACPP)
        cmd = _engine_serve_cmd(cfg)
        assert "docker run" in cmd
        assert "--gpus all" in cmd
        assert "-p 8000:8000" in cmd
        assert "ghcr.io/ggerganov/llama.cpp:server" in cmd
        assert "-m" in cmd
        assert "--host" in cmd
        assert "--port 8000" in cmd

    def test_tensor_split_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.LLAMACPP,
            workload_type="realtime_api",
            deployment_profile={"tensor_parallel": 4},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--tensor-split 4" in cmd

    def test_tensor_split_ignored_when_one(self) -> None:
        cfg = _config(
            engine=InferenceEngine.LLAMACPP,
            workload_type="realtime_api",
            deployment_profile={"tensor_parallel": 1},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--tensor-split" not in cmd

    def test_context_size_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.LLAMACPP,
            workload_type="batch_inference",
            deployment_profile={"context_length": 32768},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--ctx-size 32768" in cmd

    def test_context_size_zero_is_ignored(self) -> None:
        cfg = _config(
            engine=InferenceEngine.LLAMACPP,
            workload_type="batch_inference",
            deployment_profile={"context_length": 0},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--ctx-size" not in cmd

    def test_batch_size_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.LLAMACPP,
            workload_type="batch_inference",
            deployment_profile={"batch_size": 512},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--batch-size 512" in cmd

    def test_threads_flag(self) -> None:
        cfg = _config(
            engine=InferenceEngine.LLAMACPP,
            workload_type="realtime_api",
            deployment_profile={"threads": 8},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--threads 8" in cmd

    def test_threads_zero_is_ignored(self) -> None:
        cfg = _config(
            engine=InferenceEngine.LLAMACPP,
            workload_type="realtime_api",
            deployment_profile={"threads": 0},
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--threads" not in cmd

    def test_multiple_llamacpp_flags_combined(self) -> None:
        cfg = _config(
            engine=InferenceEngine.LLAMACPP,
            workload_type="batch_inference",
            deployment_profile={
                "tensor_parallel": 2,
                "context_length": 16384,
                "batch_size": 256,
                "threads": 16,
            },
        )
        cmd = _engine_serve_cmd(cfg)
        assert "--tensor-split 2" in cmd
        assert "--ctx-size 16384" in cmd
        assert "--batch-size 256" in cmd
        assert "--threads 16" in cmd

    def test_no_workload_flags_when_workload_type_unset(self) -> None:
        cfg = _config(engine=InferenceEngine.LLAMACPP, workload_type="")
        cmd = _engine_serve_cmd(cfg)
        assert "--tensor-split" not in cmd
        assert "--ctx-size" not in cmd
        assert "--batch-size" not in cmd
        assert "--threads" not in cmd


class TestEngineServeCmdNoWorkload:
    def test_vllm_no_workload_flags_when_unset(self) -> None:
        cfg = _config(engine=InferenceEngine.VLLM, workload_type="")
        cmd = _engine_serve_cmd(cfg)
        assert "--tensor-parallel-size" not in cmd
        assert "--max-model-len" not in cmd
        assert "--max-num-seqs" not in cmd
        assert "--gpu-memory-utilization" not in cmd
        assert "--enforce-eager" not in cmd
        assert "--quantization" not in cmd

    def test_workload_type_set_but_empty_profile_still_works(self) -> None:
        cfg = _config(
            engine=InferenceEngine.VLLM,
            workload_type="realtime_api",
            deployment_profile=None,
        )
        cmd = _engine_serve_cmd(cfg)
        assert "docker run" in cmd


# ═══════════════════════════════════════════════════════════════════════════
# _user_data_script
# ═══════════════════════════════════════════════════════════════════════════


class TestUserDataScript:
    def test_always_contains_shebang_and_set_euxo(self) -> None:
        cfg = _config()
        script = _user_data_script(cfg)
        assert script.startswith("#!/bin/bash\n")
        assert "set -euxo pipefail" in script

    def test_contains_docker_run_command(self) -> None:
        cfg = _config()
        script = _user_data_script(cfg)
        assert "docker run" in script

    def test_contains_max_cost_and_timeout_env_vars(self) -> None:
        cfg = _config(max_cost_usd=20.0, timeout_minutes=30.0)
        script = _user_data_script(cfg)
        assert "MAX_COST=20.0" in script
        assert "TIMEOUT_MIN=30.0" in script

    def test_emits_workload_type_when_set(self) -> None:
        cfg = _config(workload_type="realtime_api")
        script = _user_data_script(cfg)
        assert "WORKLOAD_TYPE=realtime_api" in script

    def test_does_not_emit_workload_type_when_empty(self) -> None:
        cfg = _config(workload_type="")
        script = _user_data_script(cfg)
        assert "WORKLOAD_TYPE" not in script


# ═══════════════════════════════════════════════════════════════════════════
# build_tfvars with hardware_preset + deployment_optimization_config
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildTfvarsOptimizationConfig:
    def test_deployment_optimization_config_appends_tfvars(self) -> None:
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        doc = DeploymentOptimizationConfig(
            hardware_presets={
                "t4": {
                    "vllm": {
                        "gpu_memory_utilization": 0.85,
                        "enforce_eager": False,
                        "quantization": "awq",
                    }
                }
            }
        )
        gen = TerraformGenerator(deployment_optimization_config=doc)
        cfg = _config(engine=InferenceEngine.VLLM, gpu_type=GPUType.T4)
        tfvars = gen.build_tfvars(cfg)
        assert "vllm_gpu_memory_utilization = 0.85" in tfvars
        assert "vllm_quantization = " in tfvars and "awq" in tfvars
        assert "vllm_enforce_eager = false" in tfvars

    def test_vram_tier_is_skipped_in_tfvars(self) -> None:
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        doc = DeploymentOptimizationConfig(
            hardware_presets={"t4": {"vllm": {"vram_tier": "24gb", "gpu_memory_utilization": 0.90}}}
        )
        gen = TerraformGenerator(deployment_optimization_config=doc)
        cfg = _config(engine=InferenceEngine.VLLM, gpu_type=GPUType.T4)
        tfvars = gen.build_tfvars(cfg)
        assert "vram_tier" not in tfvars
        assert "vllm_gpu_memory_utilization" in tfvars

    def test_hardware_preset_overrides_optimization_config(self) -> None:
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        doc = DeploymentOptimizationConfig(hardware_presets={"t4": {"vllm": {"gpu_memory_utilization": 0.85}}})
        gen = TerraformGenerator(deployment_optimization_config=doc)
        cfg = _config(engine=InferenceEngine.VLLM, gpu_type=GPUType.T4)
        tfvars = gen.build_tfvars(cfg, hardware_preset={"gpu_memory_utilization": 0.72})
        assert "vllm_gpu_memory_utilization = 0.72" in tfvars

    def test_boolean_value_emitted_as_lowercase(self) -> None:
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        doc = DeploymentOptimizationConfig(
            hardware_presets={"t4": {"vllm": {"enforce_eager": True, "enable_prefix_caching": False}}}
        )
        gen = TerraformGenerator(deployment_optimization_config=doc)
        cfg = _config(engine=InferenceEngine.VLLM, gpu_type=GPUType.T4)
        tfvars = gen.build_tfvars(cfg)
        assert "true" in tfvars
        assert "false" in tfvars

    def test_string_value_emitted_as_escaped(self) -> None:
        from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig

        doc = DeploymentOptimizationConfig(hardware_presets={"t4": {"vllm": {"kv_cache_dtype": "fp8"}}})
        gen = TerraformGenerator(deployment_optimization_config=doc)
        cfg = _config(engine=InferenceEngine.VLLM, gpu_type=GPUType.T4)
        tfvars = gen.build_tfvars(cfg)
        assert '"fp8"' in tfvars


class TestBuildTfvarsTerraformConfig:
    def test_guided_decoding_backend_override(self) -> None:
        ns = SimpleNamespace(guided_decoding_backend="xgrammar")
        gen = TerraformGenerator(terraform_config=ns)
        cfg = _config()
        tfvars = gen.build_tfvars(cfg)
        assert '"xgrammar"' in tfvars
        assert "guided_decoding_backend" in tfvars

    def test_enable_structured_outputs_boolean_override(self) -> None:
        ns = SimpleNamespace(
            guided_decoding_backend="outlines",
            enable_structured_outputs=False,
        )
        gen = TerraformGenerator(terraform_config=ns)
        cfg = _config()
        tfvars = gen.build_tfvars(cfg)
        assert "enable_structured_outputs  = false" in tfvars

    def test_grammar_file_override(self) -> None:
        ns = SimpleNamespace(
            guided_decoding_backend="outlines",
            enable_structured_outputs=True,
            grammar_file="path/to/grammar.gbnf",
        )
        gen = TerraformGenerator(terraform_config=ns)
        cfg = _config()
        tfvars = gen.build_tfvars(cfg)
        assert "grammar_file" in tfvars
        assert "path/to/grammar.gbnf" in tfvars

    def test_grammar_file_omitted_when_empty(self) -> None:
        ns = SimpleNamespace(
            guided_decoding_backend="outlines",
            enable_structured_outputs=True,
            grammar_file="",
        )
        gen = TerraformGenerator(terraform_config=ns)
        cfg = _config()
        tfvars = gen.build_tfvars(cfg)
        assert "grammar_file" not in tfvars

    def test_terraform_config_overrides_compute_defaults(self) -> None:
        ns = SimpleNamespace(
            gpu_count=8,
            model_name="override/model",
            guided_decoding_backend="outlines",
        )
        gen = TerraformGenerator(terraform_config=ns)
        cfg = _config(gpu_count=2, model_name="default/model")
        tfvars = gen.build_tfvars(cfg)
        assert "gpu_count      = 8" in tfvars
        assert '"override/model"' in tfvars


# ═══════════════════════════════════════════════════════════════════════════
# build_azure_containerapp_tfvars
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildAzureContainerAppTfvars:
    def test_builds_correct_tfvars_structure(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            deploy_type="containerapp",
        )
        tfvars = gen.build_azure_containerapp_tfvars(cfg, deployment_name="my-deploy")
        assert "deployment_name = " in tfvars
        assert '"my-deploy"' in tfvars
        assert "region = " in tfvars
        assert "container_image = " in tfvars
        assert "model_name = " in tfvars
        assert "vllm_context_length = " in tfvars
        assert "vllm_max_num_seqs = " in tfvars
        assert "vllm_gpu_memory_utilization = " in tfvars
        assert "vllm_enforce_eager = " in tfvars

    def test_propagates_profile_context_length(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            deploy_type="containerapp",
            deployment_profile={"context_length": 65536},
        )
        tfvars = gen.build_azure_containerapp_tfvars(cfg, deployment_name="d")
        assert "vllm_context_length = 65536" in tfvars

    def test_propagates_profile_quantization(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            deploy_type="containerapp",
            deployment_profile={"quantization": "awq"},
        )
        tfvars = gen.build_azure_containerapp_tfvars(cfg, deployment_name="d")
        assert "vllm_quantization" in tfvars
        assert "awq" in tfvars

    def test_default_quantization_is_empty(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            deploy_type="containerapp",
        )
        tfvars = gen.build_azure_containerapp_tfvars(cfg, deployment_name="d")
        assert "vllm_quantization" in tfvars


# ═══════════════════════════════════════════════════════════════════════════
# _generate_* legacy output aliases
# ═══════════════════════════════════════════════════════════════════════════


class TestGeneratorLegacyOutputs:
    PROVIDERS_WITH_LEGACY_ALIASES: tuple[ComputeProvider, ...] = (
        ComputeProvider.AWS,
        ComputeProvider.GCP,
        ComputeProvider.AZURE,
        ComputeProvider.RUNPOD,
        ComputeProvider.VAST_AI,
        ComputeProvider.LAMBDA_LABS,
        ComputeProvider.MODAL,
        ComputeProvider.COREWEAVE,
        ComputeProvider.DIGITAL_OCEAN,
        ComputeProvider.ORACLE,
    )

    @pytest.mark.parametrize("provider", PROVIDERS_WITH_LEGACY_ALIASES)
    def test_module_style_provider_has_legacy_outputs(self, provider: ComputeProvider) -> None:
        gen = TerraformGenerator()
        cfg = _config(provider=provider, allowed_cidr="127.0.0.1/32")
        out = gen.generate(cfg)
        assert 'output "instance_ip"' in out, f"{provider.value} missing instance_ip alias"
        assert 'output "endpoint_url"' in out, f"{provider.value} missing endpoint_url alias"

    def test_vast_provider_legacy_outputs(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(provider=ComputeProvider.VAST, allowed_cidr="127.0.0.1/32")
        out = gen.generate(cfg)
        assert 'output "instance_ip"' in out
        assert 'output "endpoint_url"' in out

    def test_kubernetes_has_instance_ip_but_no_legacy_aliases(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(provider=ComputeProvider.KUBERNETES)
        out = gen.generate(cfg)
        assert 'output "instance_ip"' in out
        assert 'output "endpoint_url"' in out


class TestGenerateAzureContainerAppModuleRewrite:
    def test_module_source_is_rewritten_to_local(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            deploy_type="containerapp",
        )
        out = gen.generate(cfg)
        assert 'source = "./modules/azure-container-app-vllm"' in out
        assert "../../modules/azure-container-app-vllm" not in out


# ═══════════════════════════════════════════════════════════════════════════
# TerraformGenerator with state_backend_selector
# ═══════════════════════════════════════════════════════════════════════════


class TestGeneratorWithStateBackend:
    def test_generate_prepends_backend_block_when_selector_configured(self) -> None:
        selector = MagicMock(spec=StateBackendSelector)
        cfg = StateBackendConfig(kind="local", path="terraform.tfstate")
        selector.select.return_value = cfg
        gen = TerraformGenerator(state_backend_selector=selector)
        out = gen.generate(_config())
        assert 'backend "local"' in out
        lines = out.splitlines()
        backend_idx = next(i for i, line in enumerate(lines) if 'backend "local"' in line)
        module_idx = next(i for i, line in enumerate(lines) if 'module "' in line)
        assert backend_idx < module_idx, "backend block must come before main HCL body"

    def test_generate_no_backend_when_selector_is_none(self) -> None:
        gen = TerraformGenerator(state_backend_selector=None)
        out = gen.generate(_config())
        assert 'backend "' not in out

    def test_generate_backend_block_preserves_module_body(self) -> None:
        selector = MagicMock(spec=StateBackendSelector)
        selector.select.return_value = StateBackendConfig(kind="local", path="state.tfstate")
        gen = TerraformGenerator(state_backend_selector=selector)
        out = gen.generate(_config(provider=ComputeProvider.AWS))
        assert 'module "vllm_server"' in out
        assert 'output "instance_id"' in out


# ═══════════════════════════════════════════════════════════════════════════
# _terraform_assets_root
# ═══════════════════════════════════════════════════════════════════════════


class TestTerraformAssetsRoot:
    def test_returns_a_directory(self) -> None:
        root = _terraform_assets_root()
        assert root.is_dir(), f"{root} is not a directory"

    def test_root_contains_stacks_and_modules(self) -> None:
        root = _terraform_assets_root()
        assert (root / "stacks").is_dir(), "Missing stacks/ directory"
        assert (root / "modules").is_dir(), "Missing modules/ directory"


# ═══════════════════════════════════════════════════════════════════════════
# materialize for Azure containerapp
# ═══════════════════════════════════════════════════════════════════════════


class TestMaterializeAzureContainerApp:
    def test_materialize_writes_variables_and_outputs(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            deploy_type="containerapp",
        )
        with tempfile.TemporaryDirectory() as tmp:
            gen.materialize(cfg, tmp, deployment_name="test-dep")
            dest = Path(tmp)
            assert (dest / "main.tf").exists()
            assert (dest / "terraform.tfvars").exists()
            assert (dest / "variables.tf").exists()
            assert (dest / "outputs.tf").exists()

    def test_materialize_copies_module_directory(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            deploy_type="containerapp",
        )
        with tempfile.TemporaryDirectory() as tmp:
            gen.materialize(cfg, tmp, deployment_name="test-dep")
            module_dir = Path(tmp) / "modules" / "azure-container-app-vllm"
            assert module_dir.is_dir()

    def test_materialize_copies_every_declared_local_module(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            deploy_type="containerapp",
        )
        with tempfile.TemporaryDirectory() as tmp:
            gen.materialize(cfg, tmp, deployment_name="test-dep")
            dest = Path(tmp)
            main_tf = (dest / "main.tf").read_text(encoding="utf-8")

            assert 'source = "./modules/azure-container-app-vllm"' in main_tf
            assert 'source = "./modules/gpu-cost-watchdog"' in main_tf
            assert (dest / "modules" / "azure-container-app-vllm").is_dir()
            assert (dest / "modules" / "gpu-cost-watchdog").is_dir()

    def test_materialize_non_azure_does_not_create_tfvars(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(provider=ComputeProvider.AWS, region="us-east-1")
        with tempfile.TemporaryDirectory() as tmp:
            gen.materialize(cfg, tmp, deployment_name="test-dep")
            dest = Path(tmp)
            assert (dest / "main.tf").exists()
            assert not (dest / "terraform.tfvars").exists()


# ═══════════════════════════════════════════════════════════════════════════
# _validate_azure_containerapp gpu_count=1 enforcement
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateAzureContainerApp:
    def test_rejects_gpu_count_not_one(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            gpu_count=2,
            engine=InferenceEngine.VLLM,
            deploy_type="containerapp",
        )
        with pytest.raises(ValueError, match="gpu_count=1"):
            gen.generate(cfg)


# ═══════════════════════════════════════════════════════════════════════════
# escape_tfvar_value deep — extended edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEscapeTfvarValueExtended:
    def test_tab_character_passes_through(self) -> None:
        out = escape_tfvar_value("col1\tcol2")
        assert "\t" in out

    def test_percent_sign_passes_through(self) -> None:
        out = escape_tfvar_value("50%")
        assert "50%" in out

    def test_at_sign_passes_through(self) -> None:
        out = escape_tfvar_value("user@host")
        assert "user@host" in out

    def test_colon_passes_through(self) -> None:
        out = escape_tfvar_value("ghcr.io/org/repo:latest")
        assert ":latest" in out

    def test_equals_sign_passes_through(self) -> None:
        out = escape_tfvar_value("key=value")
        assert "key=value" in out[1:-1]

    def test_unquoted_brace_in_body_is_inert(self) -> None:
        out = escape_tfvar_value("x}y")
        assert out == '"x}y"'

    def test_output_always_starts_and_ends_with_double_quote(self) -> None:
        payloads = ["a", "", "${x}", 'with"quote', "line1\nline2", "\\path\\"]
        for p in payloads:
            out = escape_tfvar_value(p)
            assert out.startswith('"'), f"payload {p!r}: output does not start with quote"
            assert out.endswith('"'), f"payload {p!r}: output does not end with quote"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
