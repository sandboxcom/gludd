"""Deep tests for src/general_ludd/infra/deployment_optimizer.py."""

from __future__ import annotations

import pytest

from general_ludd.infra.deployment_optimizer import (
    CLOUD_INSTANCE_TABLE,
    GPU_TABLE,
    WORKLOAD_PROFILES,
    HardwareProfile,
    ModelDeploymentProfile,
    ModelProfile,
    WorkloadType,
    _divides_heads,
    _fit_max_model_len,
    _fit_max_model_len_with_weights,
    _kv_dtype_bytes,
    _quant_bytes_per_param,
    _quant_supported_on_arch,
    _select_quant_dtype,
    _select_tensor_parallel,
    hardware_profile_for,
    kv_cache_bytes,
    recommend_config,
)

# ---------------------------------------------------------------------------
# ModelProfile — weights_bytes
# ---------------------------------------------------------------------------


class TestModelProfileWeightsBytes:
    def test_fp16_weights_llama_8b(self):
        m = ModelProfile(name="llama-8b", num_layers=32, num_kv_heads=8, head_dim=128, params_b=8.0)
        assert m.weights_bytes("fp16") == pytest.approx(16e9)
        assert m.weights_bytes("bf16") == pytest.approx(16e9)

    def test_int8_weights_halve_fp16(self):
        m = ModelProfile(name="test", num_layers=1, num_kv_heads=1, head_dim=64, params_b=1.0)
        assert m.weights_bytes("int8") == pytest.approx(1e9)

    def test_fp8_weights_halve_fp16(self):
        m = ModelProfile(name="test", num_layers=1, num_kv_heads=1, head_dim=64, params_b=1.0)
        assert m.weights_bytes("fp8") == pytest.approx(1e9)

    def test_awq_4bit_quarters_fp16(self):
        m = ModelProfile(name="test", num_layers=1, num_kv_heads=1, head_dim=64, params_b=1.0)
        assert m.weights_bytes("awq") == pytest.approx(0.5e9)

    def test_q4_k_m_quarters_fp16(self):
        m = ModelProfile(name="test", num_layers=1, num_kv_heads=1, head_dim=64, params_b=1.0)
        assert m.weights_bytes("q4_k_m") == pytest.approx(0.5e9)

    def test_q6_k_is_75_percent(self):
        m = ModelProfile(name="test", num_layers=1, num_kv_heads=1, head_dim=64, params_b=1.0)
        assert m.weights_bytes("q6_k") == pytest.approx(0.75e9)

    def test_active_params_not_used_for_weights(self):
        m = ModelProfile(
            name="moe", num_layers=32, num_kv_heads=8, head_dim=128, params_b=100.0, is_moe=True, active_params_b=10.0
        )
        assert m.weights_bytes("fp16") == pytest.approx(200e9)

    def test_unknown_quant_raises(self):
        m = ModelProfile(name="test", num_layers=1, num_kv_heads=1, head_dim=64, params_b=1.0)
        with pytest.raises(ValueError, match="unknown quantization"):
            m.weights_bytes("banana")


# ---------------------------------------------------------------------------
# HardwareProfile
# ---------------------------------------------------------------------------


class TestHardwareProfile:
    def test_total_vram_single_gpu(self):
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0)
        assert hw.total_vram_gb == 80.0

    def test_total_vram_multi_gpu(self):
        hw = HardwareProfile(gpu_type="h100", gpu_count=8, vram_gb=80.0)
        assert hw.total_vram_gb == 640.0

    def test_defaults_safe(self):
        hw = HardwareProfile(gpu_type="t4")
        assert hw.gpu_count == 1
        assert hw.vram_gb == 0.0
        assert hw.has_nvlink is False
        assert hw.supports_fp8 is False


# ---------------------------------------------------------------------------
# _quant_bytes_per_param / _kv_dtype_bytes
# ---------------------------------------------------------------------------


class TestQuantBytesPerParam:
    def test_known_quants(self):
        assert _quant_bytes_per_param("fp16") == 2.0
        assert _quant_bytes_per_param("fp8") == 1.0
        assert _quant_bytes_per_param("awq") == 0.5

    def test_case_insensitive(self):
        assert _quant_bytes_per_param("FP16") == 2.0
        assert _quant_bytes_per_param("AwQ") == 0.5

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            _quant_bytes_per_param("nonexistent")


class TestKvDtypeBytes:
    def test_known_dtypes(self):
        assert _kv_dtype_bytes("fp16") == 2
        assert _kv_dtype_bytes("fp8") == 1
        assert _kv_dtype_bytes("int8") == 1

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            _kv_dtype_bytes("???")


# ---------------------------------------------------------------------------
# kv_cache_bytes — the core sizing primitive
# ---------------------------------------------------------------------------


class TestKvCacheBytes:
    def _make_model(self, **overrides):
        defaults = dict(name="test", num_layers=32, num_kv_heads=8, head_dim=128, params_b=7.0)
        defaults.update(overrides)
        return ModelProfile(**defaults)

    def test_basic_formula(self):
        m = self._make_model()
        result = kv_cache_bytes(m, max_len=4096, max_seqs=256, dtype="fp16")
        expected = 2 * 32 * 4096 * 256 * 8 * 128 * 2
        assert result == expected

    def test_formula_with_fp8_kv(self):
        m = self._make_model()
        result = kv_cache_bytes(m, max_len=4096, max_seqs=256, dtype="fp8")
        expected = 2 * 32 * 4096 * 256 * 8 * 128 * 1
        assert result == expected

    def test_different_num_kv_heads(self):
        m = self._make_model(num_kv_heads=4)
        result = kv_cache_bytes(m, max_len=2048, max_seqs=128, dtype="fp16")
        expected = 2 * 32 * 2048 * 128 * 4 * 128 * 2
        assert result == expected

    def test_single_seq_min_context(self):
        m = self._make_model()
        result = kv_cache_bytes(m, max_len=1, max_seqs=1, dtype="fp16")
        assert result > 0

    def test_zero_max_len_raises(self):
        m = self._make_model()
        with pytest.raises(ValueError, match="positive"):
            kv_cache_bytes(m, max_len=0, max_seqs=1, dtype="fp16")

    def test_zero_max_seqs_raises(self):
        m = self._make_model()
        with pytest.raises(ValueError, match="positive"):
            kv_cache_bytes(m, max_len=1, max_seqs=0, dtype="fp16")

    def test_negative_max_len_raises(self):
        m = self._make_model()
        with pytest.raises(ValueError):
            kv_cache_bytes(m, max_len=-1, max_seqs=1, dtype="fp16")

    def test_returns_int(self):
        m = self._make_model()
        result = kv_cache_bytes(m, max_len=4096, max_seqs=256, dtype="fp16")
        assert isinstance(result, int)

    def test_large_model_kv_overhead(self):
        m = self._make_model(num_layers=80, num_kv_heads=8, head_dim=128, params_b=70.0)
        result = kv_cache_bytes(m, max_len=8192, max_seqs=512, dtype="fp16")
        expected = 2 * 80 * 8192 * 512 * 8 * 128 * 2
        assert result == expected


# ---------------------------------------------------------------------------
# hardware_profile_for
# ---------------------------------------------------------------------------


class TestHardwareProfileFor:
    def test_known_gpu_from_table(self):
        hw = hardware_profile_for("h100", gpu_count=4)
        assert hw.gpu_type == "h100"
        assert hw.gpu_count == 4
        assert hw.vram_gb == 80.0
        assert hw.has_nvlink is True
        assert hw.supports_fp8 is True
        assert hw.arch == "hopper"

    def test_unknown_gpu_raises(self):
        with pytest.raises(ValueError, match="unknown gpu_type"):
            hardware_profile_for("nonexistent_gpu")

    def test_instance_resolution(self):
        hw = hardware_profile_for("", instance="p5")
        assert hw.gpu_type == "h100"
        assert hw.gpu_count == 8
        assert hw.has_nvlink is True

    def test_unknown_instance_raises(self):
        with pytest.raises(ValueError, match="unknown cloud instance"):
            hardware_profile_for("", instance="nonexistent")

    def test_explicit_nvlink_overrides(self):
        hw = hardware_profile_for("a10", gpu_count=2, has_nvlink=True)
        assert hw.gpu_type == "a10"
        assert hw.has_nvlink is True

    def test_single_gpu_nvlink_forces_false(self):
        hw = hardware_profile_for("h100", gpu_count=1)
        assert hw.has_nvlink is False

    def test_case_insensitive_gpu_type(self):
        hw = hardware_profile_for("H100")
        assert hw.gpu_type == "h100"

    def test_case_insensitive_instance(self):
        hw = hardware_profile_for("", instance="P5")
        assert hw.gpu_type == "h100"
        assert hw.gpu_count == 8

    def test_all_gpu_table_entries_resolvable(self):
        for gpu_type in GPU_TABLE:
            hw = hardware_profile_for(gpu_type)
            assert hw.gpu_type == gpu_type.lower()
            assert hw.vram_gb > 0
            assert hw.arch != ""

    def test_all_cloud_instances_resolvable(self):
        for instance in CLOUD_INSTANCE_TABLE:
            hw = hardware_profile_for("", instance=instance)
            assert hw.gpu_type != ""
            assert hw.gpu_count >= 1


# ---------------------------------------------------------------------------
# _quant_supported_on_arch
# ---------------------------------------------------------------------------


class TestQuantSupportedOnArch:
    def test_fp8_supported_on_h200(self):
        hw = HardwareProfile(gpu_type="h200", supports_fp8=True)
        assert _quant_supported_on_arch("fp8", hw)

    def test_fp8_not_supported_on_a100(self):
        hw = HardwareProfile(gpu_type="a100", supports_fp8=False)
        assert not _quant_supported_on_arch("fp8", hw)

    def test_bf16_always_supported(self):
        hw = HardwareProfile(gpu_type="t4", supports_fp8=False)
        assert _quant_supported_on_arch("bf16", hw)

    def test_awq_supported_on_non_fp8_card(self):
        hw = HardwareProfile(gpu_type="a100", supports_fp8=False)
        assert _quant_supported_on_arch("awq", hw)


# ---------------------------------------------------------------------------
# _select_quant_dtype
# ---------------------------------------------------------------------------


class TestSelectQuantDtype:
    def _make_model(self, params_b=7.0):
        return ModelProfile(name="test", num_layers=32, num_kv_heads=8, head_dim=128, params_b=params_b)

    def test_vllm_picks_bf16_when_fits(self):
        m = self._make_model(0.1)
        hw = HardwareProfile(gpu_type="h100", gpu_count=8, vram_gb=80.0)
        quant, dtype = _select_quant_dtype(m, hw, "vllm", 0.90)
        assert quant == "bf16"
        assert dtype == "bf16"

    def test_vllm_falls_back_to_fp8_on_supported_card(self):
        m = self._make_model(300.0)
        hw = HardwareProfile(gpu_type="h100", gpu_count=8, vram_gb=80.0, supports_fp8=True)
        quant, dtype = _select_quant_dtype(m, hw, "vllm", 0.90)
        assert quant == "fp8"
        assert dtype == "fp8"

    def test_vllm_falls_back_to_int8_on_unsupported_fp8_card(self):
        m = self._make_model(50.0)
        hw = HardwareProfile(gpu_type="a100_80", gpu_count=1, vram_gb=80.0, supports_fp8=False)
        quant, dtype = _select_quant_dtype(m, hw, "vllm", 0.90)
        assert quant == "int8"
        assert dtype == "bf16"

    def test_vllm_falls_back_to_awq_when_tighter(self):
        m = self._make_model(100.0)
        hw = HardwareProfile(gpu_type="a100_80", gpu_count=1, vram_gb=80.0, supports_fp8=False)
        quant, dtype = _select_quant_dtype(m, hw, "vllm", 0.90)
        assert quant == "awq"
        assert dtype == "bf16"

    def test_raises_when_nothing_fits(self):
        m = self._make_model(1000.0)
        hw = HardwareProfile(gpu_type="t4", gpu_count=1, vram_gb=16.0)
        with pytest.raises(ValueError, match="does not fit"):
            _select_quant_dtype(m, hw, "vllm", 0.90)

    def test_llamacpp_allow_partial_returns_most_aggressive(self):
        m = self._make_model(1000.0)
        hw = HardwareProfile(gpu_type="t4", gpu_count=1, vram_gb=16.0)
        quant, dtype = _select_quant_dtype(m, hw, "llamacpp", 0.90, allow_partial=True)
        assert quant == "q4_k_m"
        assert dtype == "bf16"

    def test_llamacpp_ladder_picks_highest_quality(self):
        m = self._make_model(0.1)
        hw = HardwareProfile(gpu_type="t4", gpu_count=1, vram_gb=16.0)
        quant, dtype = _select_quant_dtype(m, hw, "llamacpp", 0.90)
        assert quant == "q8_0"
        assert dtype == "bf16"

    def test_native_quant_pinned_when_supported(self):
        m = ModelProfile(
            name="deepseek", num_layers=60, num_kv_heads=8, head_dim=128, params_b=100.0, native_quant="fp8"
        )
        hw = HardwareProfile(gpu_type="h100", gpu_count=8, vram_gb=80.0, supports_fp8=True)
        quant, _dtype = _select_quant_dtype(m, hw, "vllm", 0.90)
        assert quant == "fp8"

    def test_native_quant_dropped_when_arch_mismatch(self):
        m = ModelProfile(
            name="deepseek", num_layers=60, num_kv_heads=8, head_dim=128, params_b=100.0, native_quant="fp8"
        )
        hw = HardwareProfile(gpu_type="a100_80", gpu_count=8, vram_gb=80.0, supports_fp8=False)
        quant, _dtype = _select_quant_dtype(m, hw, "vllm", 0.90)
        assert quant != "fp8"


# ---------------------------------------------------------------------------
# _divides_heads / _select_tensor_parallel
# ---------------------------------------------------------------------------


class TestDividesHeads:
    def test_valid_tp(self):
        m = ModelProfile(name="test", num_layers=32, num_kv_heads=8, head_dim=128, params_b=7.0)
        assert _divides_heads(2, m)
        assert _divides_heads(4, m)
        assert _divides_heads(8, m)

    def test_invalid_tp(self):
        m = ModelProfile(name="test", num_layers=32, num_kv_heads=8, head_dim=128, params_b=7.0)
        assert not _divides_heads(3, m)
        assert not _divides_heads(5, m)
        assert not _divides_heads(7, m)

    def test_tp_zero_always_false(self):
        m = ModelProfile(name="test", num_layers=32, num_kv_heads=8, head_dim=128, params_b=7.0)
        assert not _divides_heads(0, m)


class TestSelectTensorParallel:
    def _make_model(self, num_kv_heads=8):
        return ModelProfile(name="test", num_layers=32, num_kv_heads=num_kv_heads, head_dim=128, params_b=7.0)

    def test_no_nvlink_forces_tp1(self):
        m = self._make_model()
        hw = HardwareProfile(gpu_type="t4", gpu_count=4, has_nvlink=False)
        assert _select_tensor_parallel(m, hw) == 1

    def test_single_gpu_forces_tp1(self):
        m = self._make_model()
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, has_nvlink=True)
        assert _select_tensor_parallel(m, hw) == 1

    def test_nvlink_with_valid_divisor(self):
        m = self._make_model(8)
        hw = HardwareProfile(gpu_type="h100", gpu_count=8, has_nvlink=True)
        assert _select_tensor_parallel(m, hw) == 8

    def test_nvlink_falls_back_to_largest_divisor(self):
        m = self._make_model(8)
        hw = HardwareProfile(gpu_type="h100", gpu_count=6, has_nvlink=True)
        assert _select_tensor_parallel(m, hw) == 4

    def test_nvlink_with_no_divisor_falls_to_tp1(self):
        m = self._make_model(7)
        hw = HardwareProfile(gpu_type="h100", gpu_count=6, has_nvlink=True)
        assert _select_tensor_parallel(m, hw) == 1

    def test_nvlink_4gpu_8heads(self):
        m = self._make_model(8)
        hw = HardwareProfile(gpu_type="h100", gpu_count=4, has_nvlink=True)
        assert _select_tensor_parallel(m, hw) == 4


# ---------------------------------------------------------------------------
# _fit_max_model_len_with_weights / _fit_max_model_len
# ---------------------------------------------------------------------------


class TestFitMaxModelLen:
    def _make_model(self, **overrides):
        defaults = dict(name="test", num_layers=32, num_kv_heads=8, head_dim=128, params_b=7.0)
        defaults.update(overrides)
        return ModelProfile(**defaults)

    def test_basic_fit_returns_sane_value(self):
        m = self._make_model()
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0)
        result = _fit_max_model_len(m, hw, "bf16", 0.90, 256, "fp16", 32768, 1)
        assert result > 0
        assert result % 256 == 0

    def test_tiny_vram_returns_zero(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="t4", gpu_count=1, vram_gb=1.0)
        result = _fit_max_model_len(m, hw, "bf16", 0.90, 256, "fp16", 32768, 1)
        assert result == 0

    def test_caps_at_desired_max_len(self):
        m = self._make_model(params_b=0.1)
        hw = HardwareProfile(gpu_type="h100", gpu_count=8, vram_gb=80.0)
        result = _fit_max_model_len(m, hw, "bf16", 0.90, 256, "fp16", 4096, 1)
        assert result == 4096

    def test_fp8_kv_allows_longer_context(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0, supports_fp8=True)
        with_fp16 = _fit_max_model_len(m, hw, "bf16", 0.90, 256, "fp16", 32768, 1)
        with_fp8 = _fit_max_model_len(m, hw, "bf16", 0.90, 256, "fp8", 32768, 1)
        assert with_fp8 >= with_fp16

    def test_with_weights_direct_api(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0)
        weights_bytes = m.weights_bytes("bf16")
        result = _fit_max_model_len_with_weights(m, hw, weights_bytes, 0.90, 256, "fp16", 32768)
        assert result > 0
        assert result % 256 == 0

    def test_increasing_max_seqs_reduces_context(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0)
        few_seqs = _fit_max_model_len(m, hw, "bf16", 0.90, 64, "fp16", 32768, 1)
        many_seqs = _fit_max_model_len(m, hw, "bf16", 0.90, 512, "fp16", 32768, 1)
        assert many_seqs <= few_seqs


# ---------------------------------------------------------------------------
# ModelDeploymentProfile.apply()
# ---------------------------------------------------------------------------


class TestModelDeploymentProfileApply:
    def test_apply_overrides_none_fields_passthrough(self):
        p = ModelDeploymentProfile(batch_size=128, enable_prefix_caching=True)
        base = {"engine": "vllm", "batch_size": 32, "max_model_len": 32768, "enable_prefix_caching": False}
        result = p.apply(base)
        assert result["batch_size"] == 128
        assert result["enable_prefix_caching"] is True
        assert result["max_model_len"] == 32768

    def test_apply_none_field_does_not_mutate(self):
        p = ModelDeploymentProfile(batch_size=None)
        base = {"batch_size": 32}
        result = p.apply(base)
        assert result["batch_size"] == 32

    def test_apply_all_fields(self):
        p = ModelDeploymentProfile(
            context_length=4096,
            max_tokens=512,
            batch_size=64,
            tensor_parallel=4,
            gpu_memory_utilization=0.85,
            quantization="fp8",
            threads=16,
            max_num_seqs=128,
            enforce_eager=True,
            enable_prefix_caching=True,
            enable_chunked_prefill=False,
            kv_cache_dtype="fp8",
        )
        base = {}
        result = p.apply(base)
        assert result["max_model_len"] == 4096
        assert result["max_tokens"] == 512
        assert result["batch_size"] == 64
        assert result["tensor_parallel_size"] == 4
        assert result["gpu_memory_utilization"] == 0.85
        assert result["quantization"] == "fp8"
        assert result["threads"] == 16
        assert result["max_num_seqs"] == 128
        assert result["enforce_eager"] is True
        assert result["enable_prefix_caching"] is True
        assert result["enable_chunked_prefill"] is False
        assert result["kv_cache_dtype"] == "fp8"


# ---------------------------------------------------------------------------
# WorkloadType + WORKLOAD_PROFILES
# ---------------------------------------------------------------------------


class TestWorkloadProfiles:
    def test_all_workload_types_have_profiles(self):
        for wt in WorkloadType:
            assert wt in WORKLOAD_PROFILES, f"Missing profile for {wt}"

    def test_batch_inference_profile(self):
        p = WORKLOAD_PROFILES[WorkloadType.BATCH_INFERENCE]
        assert p.batch_size == 128
        assert p.max_num_seqs == 256
        assert p.enable_prefix_caching is True
        assert p.enable_chunked_prefill is True

    def test_realtime_api_profile(self):
        p = WORKLOAD_PROFILES[WorkloadType.REALTIME_API]
        assert p.batch_size == 8
        assert p.enable_chunked_prefill is False

    def test_fine_tuning_profile(self):
        p = WORKLOAD_PROFILES[WorkloadType.FINE_TUNING]
        assert p.context_length == 8192
        assert p.batch_size == 4
        assert p.enforce_eager is True

    def test_speculative_decoding_profile(self):
        p = WORKLOAD_PROFILES[WorkloadType.SPECULATIVE_DECODING]
        assert p.batch_size == 1
        assert p.enforce_eager is True

    def test_embedding_generation_profile(self):
        p = WORKLOAD_PROFILES[WorkloadType.EMBEDDING_GENERATION]
        assert p.context_length == 512
        assert p.batch_size == 256
        assert p.gpu_memory_utilization == 0.95

    def test_profiles_are_frozen_dataclass(self):
        p = WORKLOAD_PROFILES[WorkloadType.BATCH_INFERENCE]
        with pytest.raises(AttributeError):
            p.batch_size = 999
        assert p.batch_size == 128


# ---------------------------------------------------------------------------
# recommend_config — public API
# ---------------------------------------------------------------------------


class TestRecommendConfig:
    def _make_model(self, **overrides):
        defaults = dict(name="test", num_layers=32, num_kv_heads=8, head_dim=128, params_b=7.0)
        defaults.update(overrides)
        return ModelProfile(**defaults)

    def test_unsupported_engine_raises(self):
        m = self._make_model()
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0)
        with pytest.raises(ValueError, match="unsupported engine"):
            recommend_config(m, hw, "unsupported")

    def test_vllm_basic_config(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0, supports_fp8=True)
        config = recommend_config(m, hw, "vllm")
        assert config["engine"] == "vllm"
        assert config["model"] == "test"
        assert config["tensor_parallel_size"] >= 1
        assert config["gpu_memory_utilization"] > 0
        assert config["max_model_len"] > 0

    def test_vllm_multi_gpu_nvlink(self):
        m = self._make_model(num_kv_heads=8)
        hw = HardwareProfile(gpu_type="h100", gpu_count=8, vram_gb=80.0, has_nvlink=True, supports_fp8=True)
        config = recommend_config(m, hw, "vllm")
        assert config["tensor_parallel_size"] == 8

    def test_vllm_single_gpu_tp1(self):
        m = self._make_model(params_b=3.0)
        hw = HardwareProfile(gpu_type="a10", gpu_count=1, vram_gb=24.0)
        config = recommend_config(m, hw, "vllm", max_num_seqs=4)
        assert config["tensor_parallel_size"] == 1

    def test_vllm_too_large_model_raises(self):
        m = self._make_model(params_b=200.0)
        hw = HardwareProfile(gpu_type="t4", gpu_count=1, vram_gb=16.0)
        with pytest.raises(ValueError):
            recommend_config(m, hw, "vllm")

    def test_llamacpp_basic_config(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="t4", gpu_count=1, vram_gb=16.0)
        config = recommend_config(m, hw, "llamacpp")
        assert config["engine"] == "llamacpp"
        assert config["model"] == "test"
        assert config["tensor_parallel_size"] == 1
        assert "n_gpu_layers" in config
        assert "n_ctx" in config

    def test_llamacpp_partial_offload(self):
        m = self._make_model(params_b=200.0)
        hw = HardwareProfile(gpu_type="t4", gpu_count=1, vram_gb=16.0)
        config = recommend_config(m, hw, "llamacpp")
        assert config["engine"] == "llamacpp"
        assert config["n_gpu_layers"] != -1

    def test_llamacpp_full_offload(self):
        m = self._make_model(params_b=0.1)
        hw = HardwareProfile(gpu_type="t4", gpu_count=1, vram_gb=16.0)
        config = recommend_config(m, hw, "llamacpp")
        assert config["n_gpu_layers"] == -1

    def test_gpu_memory_utilization_out_of_range_raises(self):
        m = self._make_model()
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0)
        with pytest.raises(ValueError, match="gpu_memory_utilization"):
            recommend_config(m, hw, "vllm", gpu_memory_utilization=0.0)
        with pytest.raises(ValueError, match="gpu_memory_utilization"):
            recommend_config(m, hw, "vllm", gpu_memory_utilization=1.0)

    def test_workload_type_str_resolution(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0, supports_fp8=True)
        config = recommend_config(m, hw, "vllm", workload_type="batch_inference")
        assert config["batch_size"] == 128

    def test_workload_type_enum_resolution(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0, supports_fp8=True)
        config = recommend_config(m, hw, "vllm", workload_type=WorkloadType.FINE_TUNING)
        assert config["max_model_len"] == 8192
        assert config["batch_size"] == 4

    def test_unknown_workload_type_raises(self):
        m = self._make_model()
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0)
        with pytest.raises(ValueError, match="unknown workload_type"):
            recommend_config(m, hw, "vllm", workload_type="banana")

    def test_explicit_overrides_beat_workload(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0, supports_fp8=True)
        config = recommend_config(m, hw, "vllm", workload_type="batch_inference", max_num_seqs=999)
        assert config["max_num_seqs"] == 999

    def test_explicit_gmu_overrides_workload(self):
        m = self._make_model(params_b=7.0)
        hw = HardwareProfile(gpu_type="h100", gpu_count=1, vram_gb=80.0, supports_fp8=True)
        config = recommend_config(m, hw, "vllm", workload_type="fine_tuning", gpu_memory_utilization=0.80)
        assert config["gpu_memory_utilization"] == 0.80


# ---------------------------------------------------------------------------
# GPU_TABLE / CLOUD_INSTANCE_TABLE static data
# ---------------------------------------------------------------------------


class TestGpuTable:
    def test_all_gpus_have_required_keys(self):
        required = {"vram_gb", "has_nvlink", "supports_fp8", "hbm_bw_gbps", "arch"}
        for gpu, spec in GPU_TABLE.items():
            missing = required - spec.keys()
            assert not missing, f"{gpu} missing keys: {missing}"

    def test_hopper_cards_support_fp8(self):
        for gpu, spec in GPU_TABLE.items():
            if spec["arch"] in ("hopper", "ada"):
                assert spec["supports_fp8"], f"{gpu} ({spec['arch']}) should support fp8"

    def test_dc_cards_have_nvlink(self):
        nvlink_gpus = {"h200", "h100", "a100_80", "a100_40", "a40"}
        for gpu in nvlink_gpus:
            assert GPU_TABLE[gpu]["has_nvlink"], f"{gpu} should have nvlink"

    def test_consumer_cards_lack_nvlink(self):
        for gpu in ("rtx_4090", "rtx_3090"):
            assert not GPU_TABLE[gpu]["has_nvlink"], f"{gpu} should not have nvlink"


class TestCloudInstanceTable:
    def test_all_instances_have_required_keys(self):
        required = {"gpu_type", "gpu_count", "has_nvlink"}
        for instance, spec in CLOUD_INSTANCE_TABLE.items():
            missing = required - spec.keys()
            assert not missing, f"{instance} missing keys: {missing}"

    def test_all_instance_gpu_types_in_gpu_table(self):
        for instance, spec in CLOUD_INSTANCE_TABLE.items():
            assert spec["gpu_type"] in GPU_TABLE, f"{instance} gpu_type {spec['gpu_type']!r} not in GPU_TABLE"
