"""Unit tests for workload-aware deployment configuration (#76 workload extension)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deployment_optimizer import (
    WORKLOAD_PROFILES,
    ModelDeploymentProfile,
    ModelProfile,
    WorkloadType,
    hardware_profile_for,
    recommend_config,
)
from general_ludd.infra.terraform import TerraformGenerator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def llama3_8b() -> ModelProfile:
    return ModelProfile(
        name="meta-llama/Meta-Llama-3-8B-Instruct",
        num_layers=32,
        num_kv_heads=8,
        head_dim=128,
        params_b=8.0,
    )


def h100_hw():
    return hardware_profile_for("h100", gpu_count=8)


def a100_80_hw():
    return hardware_profile_for("a100_80", gpu_count=1)


# ---------------------------------------------------------------------------
# 1. WorkloadType enum
# ---------------------------------------------------------------------------


class TestWorkloadTypeEnum:
    def test_all_values_present(self) -> None:
        assert WorkloadType.BATCH_INFERENCE == "batch_inference"
        assert WorkloadType.REALTIME_API == "realtime_api"
        assert WorkloadType.FINE_TUNING == "fine_tuning"
        assert WorkloadType.SPECULATIVE_DECODING == "speculative_decoding"
        assert WorkloadType.EMBEDDING_GENERATION == "embedding_generation"

    def test_is_str_enum(self) -> None:
        assert isinstance(WorkloadType.BATCH_INFERENCE, str)
        assert str(WorkloadType.REALTIME_API) == "realtime_api"

    def test_len_is_5(self) -> None:
        assert len(WorkloadType) == 5


# ---------------------------------------------------------------------------
# 2. ModelDeploymentProfile dataclass
# ---------------------------------------------------------------------------


class TestModelDeploymentProfile:
    def test_defaults_are_none(self) -> None:
        p = ModelDeploymentProfile()
        assert p.context_length is None
        assert p.max_tokens is None
        assert p.batch_size is None
        assert p.tensor_parallel is None
        assert p.gpu_memory_utilization is None
        assert p.quantization is None
        assert p.threads is None
        assert p.max_num_seqs is None
        assert p.enforce_eager is None
        assert p.enable_prefix_caching is None
        assert p.enable_chunked_prefill is None
        assert p.kv_cache_dtype is None

    def test_frozen_raises_on_setattr(self) -> None:
        p = ModelDeploymentProfile()
        with pytest.raises(FrozenInstanceError):
            p.context_length = 2048  # type: ignore[misc]

    def test_apply_merges_only_non_none(self) -> None:
        base: dict[str, object] = {
            "max_model_len": 32768,
            "max_tokens": 4096,
            "batch_size": 64,
            "tensor_parallel_size": 1,
            "gpu_memory_utilization": 0.90,
            "quantization": None,
            "threads": 0,
            "max_num_seqs": 256,
            "enforce_eager": False,
            "enable_prefix_caching": True,
            "enable_chunked_prefill": True,
            "kv_cache_dtype": "auto",
            "engine": "vllm",
            "model": "test",
        }
        profile = ModelDeploymentProfile(batch_size=128, enforce_eager=True)
        merged = profile.apply(base)
        assert merged["batch_size"] == 128
        assert merged["enforce_eager"] is True
        assert merged["max_model_len"] == 32768
        assert merged["max_tokens"] == 4096
        assert merged["engine"] == "vllm"

    def test_apply_empty_profile_is_identity(self) -> None:
        base: dict[str, object] = {"a": 1, "b": 2}
        merged = ModelDeploymentProfile().apply(base)
        assert merged == base
        assert merged is not base

    def test_apply_all_fields(self) -> None:
        base: dict[str, object] = {
            "max_model_len": 0,
            "max_tokens": 0,
            "batch_size": 0,
            "tensor_parallel_size": 0,
            "gpu_memory_utilization": 0.0,
            "quantization": "",
            "threads": 0,
            "max_num_seqs": 0,
            "enforce_eager": True,
            "enable_prefix_caching": True,
            "enable_chunked_prefill": True,
            "kv_cache_dtype": "auto",
        }
        profile = ModelDeploymentProfile(
            context_length=8192,
            max_tokens=1024,
            batch_size=32,
            tensor_parallel=2,
            gpu_memory_utilization=0.85,
            quantization="fp8",
            threads=8,
            max_num_seqs=64,
            enforce_eager=False,
            enable_prefix_caching=False,
            enable_chunked_prefill=False,
            kv_cache_dtype="fp8",
        )
        merged = profile.apply(base)
        assert merged["max_model_len"] == 8192
        assert merged["max_tokens"] == 1024
        assert merged["batch_size"] == 32
        assert merged["tensor_parallel_size"] == 2
        assert merged["gpu_memory_utilization"] == 0.85
        assert merged["quantization"] == "fp8"
        assert merged["threads"] == 8
        assert merged["max_num_seqs"] == 64
        assert merged["enforce_eager"] is False
        assert merged["enable_prefix_caching"] is False
        assert merged["enable_chunked_prefill"] is False
        assert merged["kv_cache_dtype"] == "fp8"


# ---------------------------------------------------------------------------
# 3. WORKLOAD_PROFILES mapping
# ---------------------------------------------------------------------------


class TestWorkloadProfiles:
    def test_all_workload_types_have_profile(self) -> None:
        for wt in WorkloadType:
            assert wt in WORKLOAD_PROFILES, f"missing profile for {wt}"

    def test_batch_inference_profile(self) -> None:
        p = WORKLOAD_PROFILES[WorkloadType.BATCH_INFERENCE]
        assert p.batch_size == 128
        assert p.max_num_seqs == 256
        assert p.enable_prefix_caching is True
        assert p.enable_chunked_prefill is True

    def test_realtime_api_profile(self) -> None:
        p = WORKLOAD_PROFILES[WorkloadType.REALTIME_API]
        assert p.batch_size == 8
        assert p.max_num_seqs == 64
        assert p.enable_chunked_prefill is False
        assert p.enforce_eager is False

    def test_fine_tuning_profile(self) -> None:
        p = WORKLOAD_PROFILES[WorkloadType.FINE_TUNING]
        assert p.context_length == 8192
        assert p.batch_size == 4
        assert p.max_num_seqs == 8
        assert p.enforce_eager is True
        assert p.enable_prefix_caching is False

    def test_speculative_decoding_profile(self) -> None:
        p = WORKLOAD_PROFILES[WorkloadType.SPECULATIVE_DECODING]
        assert p.batch_size == 1
        assert p.max_num_seqs == 32
        assert p.enforce_eager is True
        assert p.enable_prefix_caching is True

    def test_embedding_generation_profile(self) -> None:
        p = WORKLOAD_PROFILES[WorkloadType.EMBEDDING_GENERATION]
        assert p.context_length == 512
        assert p.batch_size == 256
        assert p.max_num_seqs == 512
        assert p.gpu_memory_utilization == 0.95
        assert p.enable_prefix_caching is False


# ---------------------------------------------------------------------------
# 4. recommend_config with workload_type
# ---------------------------------------------------------------------------


class TestRecommendConfigWithWorkload:
    def test_batch_inference_overrides_max_num_seqs(self) -> None:
        hw = a100_80_hw()
        cfg = recommend_config(
            llama3_8b(), hw, "vllm",
            workload_type=WorkloadType.BATCH_INFERENCE,
        )
        assert cfg["max_num_seqs"] == 256
        assert cfg["enable_prefix_caching"] is True

    def test_fine_tuning_overrides_context_and_eager(self) -> None:
        hw = a100_80_hw()
        cfg = recommend_config(
            llama3_8b(), hw, "vllm",
            workload_type=WorkloadType.FINE_TUNING,
        )
        assert cfg["enforce_eager"] is True
        assert cfg["enable_prefix_caching"] is False
        assert cfg["enable_chunked_prefill"] is False

    def test_embedding_sets_high_gmu(self) -> None:
        hw = a100_80_hw()
        cfg = recommend_config(
            llama3_8b(), hw, "vllm",
            workload_type=WorkloadType.EMBEDDING_GENERATION,
        )
        assert cfg["gpu_memory_utilization"] == 0.95
        assert cfg["max_num_seqs"] == 512

    def test_speculative_decoding_single_batch(self) -> None:
        hw = a100_80_hw()
        cfg = recommend_config(
            llama3_8b(), hw, "vllm",
            workload_type=WorkloadType.SPECULATIVE_DECODING,
        )
        assert cfg["batch_size"] == 1
        assert cfg["enforce_eager"] is True

    def test_null_workload_gives_base_config(self) -> None:
        hw = a100_80_hw()
        base = recommend_config(llama3_8b(), hw, "vllm")
        null_cfg = recommend_config(llama3_8b(), hw, "vllm", workload_type=None)
        assert base == null_cfg

    def test_unknown_workload_type_raises(self) -> None:
        hw = a100_80_hw()
        with pytest.raises(ValueError):
            recommend_config(llama3_8b(), hw, "vllm", workload_type="bogus")  # type: ignore[arg-type]

    def test_llamacpp_workload_respected(self) -> None:
        hw = hardware_profile_for("rtx_4090")
        cfg = recommend_config(
            llama3_8b(), hw, "llamacpp",
            workload_type=WorkloadType.REALTIME_API,
        )
        assert cfg["max_num_seqs"] == 64
        assert cfg["batch_size"] == 8

    def test_workload_does_not_override_explicit_kwargs(self) -> None:
        hw = a100_80_hw()
        cfg = recommend_config(
            llama3_8b(), hw, "vllm",
            workload_type=WorkloadType.REALTIME_API,
            max_num_seqs=512,
        )
        assert cfg["max_num_seqs"] == 512

    # ------------------------------------------------------------------
    # TP + NVLink interactions with workload profiles
    # ------------------------------------------------------------------

    def test_tp_on_hopper_with_nvlink(self) -> None:
        hw = h100_hw()
        cfg = recommend_config(
            llama3_8b(), hw, "vllm",
            workload_type=WorkloadType.BATCH_INFERENCE,
        )
        tp = cfg["tensor_parallel_size"]
        assert isinstance(tp, int) and tp >= 1

    def test_tp1_on_pcie_only(self) -> None:
        hw = hardware_profile_for("l40s", gpu_count=2)
        cfg = recommend_config(
            llama3_8b(), hw, "vllm",
            workload_type=WorkloadType.BATCH_INFERENCE,
        )
        assert cfg["tensor_parallel_size"] == 1


# ---------------------------------------------------------------------------
# 5. ComputeConfig workload_type validation
# ---------------------------------------------------------------------------


class TestComputeConfigWorkloadType:
    def test_empty_workload_type_allowed(self) -> None:
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.H100,
            model_name="test/model",
            workload_type="",
        )
        assert cfg.workload_type == ""

    def test_valid_workload_types_accepted(self) -> None:
        for wt in ("batch_inference", "realtime_api", "fine_tuning",
                   "speculative_decoding", "embedding_generation"):
            cfg = ComputeConfig(
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.H100,
                model_name="test/model",
                workload_type=wt,
            )
            assert cfg.workload_type == wt

    def test_invalid_workload_type_raises(self) -> None:
        with pytest.raises(ValueError):
            ComputeConfig(
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.H100,
                model_name="test/model",
                workload_type="invalid_workload",
            )

    def test_deployment_profile_default_is_none(self) -> None:
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.H100,
            model_name="test/model",
        )
        assert cfg.deployment_profile is None

    def test_deployment_profile_passed_through(self) -> None:
        profile: dict[str, object] = {
            "context_length": 8192,
            "batch_size": 64,
        }
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.H100,
            model_name="test/model",
            deployment_profile=profile,
        )
        assert cfg.deployment_profile == profile


# ---------------------------------------------------------------------------
# 6. TerraformGenerator workload-aware tfvars
# ---------------------------------------------------------------------------


class TestTerraformGeneratorWorkloadTfvars:
    def test_emits_workload_type_tfvar(self) -> None:
        gen = TerraformGenerator()
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.H100,
            model_name="test/model",
            workload_type="batch_inference",
        )
        tfvars = gen.build_tfvars(cfg)
        assert 'workload_type              = "batch_inference"' in tfvars

    def test_emits_default_workload_when_empty(self) -> None:
        gen = TerraformGenerator()
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.H100,
            model_name="test/model",
            workload_type="",
        )
        tfvars = gen.build_tfvars(cfg)
        assert 'workload_type              = "batch_inference"' in tfvars

    def test_emits_engine_prefixed_profile_vars(self) -> None:
        gen = TerraformGenerator()
        profile: dict[str, object] = {
            "context_length": 16384,
            "batch_size": 64,
            "max_num_seqs": 128,
            "gpu_memory_utilization": 0.88,
            "enforce_eager": True,
            "enable_prefix_caching": False,
            "quantization": "fp8",
            "threads": 4,
            "kv_cache_dtype": "fp8",
        }
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.H100,
            model_name="test/model",
            workload_type="fine_tuning",
            deployment_profile=profile,
        )
        tfvars = gen.build_tfvars(cfg)
        assert "vllm_context_length = 16384" in tfvars
        assert "vllm_batch_size = 64" in tfvars
        assert "vllm_max_num_seqs = 128" in tfvars
        assert "vllm_gpu_memory_utilization = 0.88" in tfvars
        assert "vllm_enforce_eager = true" in tfvars
        assert "vllm_enable_prefix_caching = false" in tfvars
        assert 'vllm_quantization = "fp8"' in tfvars
        assert "vllm_threads = 4" in tfvars
        assert 'vllm_kv_cache_dtype = "fp8"' in tfvars

    def test_llamacpp_engine_prefix_in_tfvars(self) -> None:
        gen = TerraformGenerator()
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.LLAMACPP,
            model_name="test/model",
            workload_type="batch_inference",
            deployment_profile={"context_length": 4096, "threads": 16},
        )
        tfvars = gen.build_tfvars(cfg)
        assert "llamacpp_context_length = 4096" in tfvars
        assert "llamacpp_threads = 16" in tfvars

    def test_default_profile_values_emitted(self) -> None:
        gen = TerraformGenerator()
        cfg = ComputeConfig(
            provider=ComputeProvider.GCP,
            gpu_type=GPUType.L4,
            model_name="test/model",
            workload_type="",
        )
        tfvars = gen.build_tfvars(cfg)
        assert "vllm_context_length = 32768" in tfvars
        assert "vllm_max_tokens = 4096" in tfvars
        assert "vllm_batch_size = 256" in tfvars
        assert "vllm_tensor_parallel = 0" in tfvars
        assert "vllm_gpu_memory_utilization = 0.9" in tfvars
        assert 'vllm_quantization = ""' in tfvars
        assert "vllm_threads = 0" in tfvars
        assert "vllm_max_num_seqs = 256" in tfvars
        assert "vllm_enforce_eager = false" in tfvars
        assert "vllm_enable_prefix_caching = true" in tfvars
        assert "vllm_enable_chunked_prefill = true" in tfvars
        assert 'vllm_kv_cache_dtype = "auto"' in tfvars


# ---------------------------------------------------------------------------
# 7. _user_data_script workload emission
# ---------------------------------------------------------------------------


class TestUserDataScriptWorkload:
    def test_workload_type_in_environment(self) -> None:
        from general_ludd.infra.terraform import _user_data_script

        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.H100,
            model_name="test/model",
            workload_type="speculative_decoding",
        )
        script = _user_data_script(cfg)
        assert 'WORKLOAD_TYPE=speculative_decoding' in script

    def test_no_workload_type_no_env_line(self) -> None:
        from general_ludd.infra.terraform import _user_data_script

        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.H100,
            model_name="test/model",
            workload_type="",
        )
        script = _user_data_script(cfg)
        assert "WORKLOAD_TYPE" not in script
