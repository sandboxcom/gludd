"""Unit tests for the deployment optimizer (#76)."""

from __future__ import annotations

import pytest

from general_ludd.infra.deployment_optimizer import (
    GPU_TABLE,
    ModelProfile,
    hardware_profile_for,
    kv_cache_bytes,
    recommend_config,
)

# ---------------------------------------------------------------------------
# Fixtures: representative model profiles
# ---------------------------------------------------------------------------


def llama3_8b() -> ModelProfile:
    # 32 layers, GQA 8 KV heads, head_dim 128 (hidden 4096 / 32 attn heads).
    return ModelProfile(
        name="meta-llama/Meta-Llama-3-8B-Instruct",
        num_layers=32,
        num_kv_heads=8,
        head_dim=128,
        params_b=8.0,
    )


def llama3_70b() -> ModelProfile:
    return ModelProfile(
        name="meta-llama/Meta-Llama-3-70B-Instruct",
        num_layers=80,
        num_kv_heads=8,
        head_dim=128,
        params_b=70.0,
    )


def deepseek_v3() -> ModelProfile:
    return ModelProfile(
        name="deepseek-ai/DeepSeek-V3",
        num_layers=61,
        num_kv_heads=128,
        head_dim=128,
        params_b=671.0,
        is_moe=True,
        native_quant="fp8",
        active_params_b=37.0,
    )


# ---------------------------------------------------------------------------
# KV-cache formula
# ---------------------------------------------------------------------------


class TestKVCacheFormula:
    def test_formula_matches_hand_computation(self) -> None:
        model = llama3_8b()
        # 2 * 32 * 1024 * 16 * 8 * 128 * 2 bytes
        expected = 2 * 32 * 1024 * 16 * 8 * 128 * 2
        assert kv_cache_bytes(model, max_len=1024, max_seqs=16, dtype="fp16") == expected

    def test_fp8_kv_halves_bytes(self) -> None:
        model = llama3_8b()
        fp16 = kv_cache_bytes(model, 1024, 16, "fp16")
        fp8 = kv_cache_bytes(model, 1024, 16, "fp8")
        assert fp8 == fp16 // 2

    def test_uses_kv_heads_not_attn_heads(self) -> None:
        # GQA: 8 KV heads. Doubling KV heads must exactly double the cache.
        a = ModelProfile("m", num_layers=4, num_kv_heads=8, head_dim=64, params_b=1.0)
        b = ModelProfile("m", num_layers=4, num_kv_heads=16, head_dim=64, params_b=1.0)
        assert kv_cache_bytes(b, 512, 8) == 2 * kv_cache_bytes(a, 512, 8)

    def test_scales_linearly_in_len_and_seqs(self) -> None:
        model = llama3_8b()
        base = kv_cache_bytes(model, 1024, 4)
        assert kv_cache_bytes(model, 2048, 4) == 2 * base
        assert kv_cache_bytes(model, 1024, 8) == 2 * base

    def test_rejects_nonpositive(self) -> None:
        model = llama3_8b()
        with pytest.raises(ValueError):
            kv_cache_bytes(model, 0, 4)
        with pytest.raises(ValueError):
            kv_cache_bytes(model, 1024, 0)

    def test_rejects_unknown_dtype(self) -> None:
        with pytest.raises(ValueError):
            kv_cache_bytes(llama3_8b(), 1024, 4, dtype="float4")


# ---------------------------------------------------------------------------
# Hardware table / profile mapping
# ---------------------------------------------------------------------------


class TestHardwareTable:
    def test_known_gpus_present(self) -> None:
        for key in ("h100", "h200", "a100_80", "l40s", "l4", "rtx_4090", "rtx_3090"):
            assert key in GPU_TABLE

    def test_consumer_no_nvlink(self) -> None:
        hp = hardware_profile_for("rtx_4090", gpu_count=2)
        assert hp.has_nvlink is False
        assert hp.supports_fp8 is True  # 4090 has fp8

    def test_3090_no_fp8(self) -> None:
        assert hardware_profile_for("rtx_3090").supports_fp8 is False

    def test_ada_l40s_pcie_only_but_fp8(self) -> None:
        hp = hardware_profile_for("l40s", gpu_count=2)
        assert hp.has_nvlink is False
        assert hp.supports_fp8 is True

    def test_hopper_nvlink_and_fp8(self) -> None:
        hp = hardware_profile_for("h100", gpu_count=8)
        assert hp.has_nvlink is True
        assert hp.supports_fp8 is True

    def test_single_gpu_never_nvlink(self) -> None:
        # Even a Hopper card is not NVLink-TP-able alone.
        assert hardware_profile_for("h100", gpu_count=1).has_nvlink is False

    def test_cloud_instance_mapping(self) -> None:
        hp = hardware_profile_for("", instance="p5")
        assert hp.gpu_type == "h100"
        assert hp.gpu_count == 8
        assert hp.has_nvlink is True

    def test_cloud_g6e_pcie(self) -> None:
        hp = hardware_profile_for("", instance="g6e")
        assert hp.gpu_type == "l40s"
        assert hp.has_nvlink is False

    def test_unknown_gpu_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            hardware_profile_for("gpu_9999")

    def test_unknown_instance_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            hardware_profile_for("", instance="zz-mega")


# ---------------------------------------------------------------------------
# recommend_config — vLLM
# ---------------------------------------------------------------------------


class TestRecommendVLLM:
    def test_tp1_on_non_nvlink_multi_gpu(self) -> None:
        # 2x L40S (PCIe only) -> must NOT use TP.
        hw = hardware_profile_for("l40s", gpu_count=2)
        cfg = recommend_config(llama3_8b(), hw, "vllm")
        assert cfg["tensor_parallel_size"] == 1
        # PCIe-only multi-GPU should fall back to pipeline parallel.
        assert cfg["pipeline_parallel_size"] == 2

    def test_tp_used_on_nvlink_and_divides_heads(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=8)
        cfg = recommend_config(llama3_70b(), hw, "vllm")
        tp = cfg["tensor_parallel_size"]
        assert isinstance(tp, int) and tp > 1
        assert 8 % tp == 0  # divides kv heads
        assert tp <= 8

    def test_fp8_only_when_supported(self) -> None:
        # A100 (no fp8) must never pick fp8 dtype/quant.
        hw = hardware_profile_for("a100_80", gpu_count=1)
        cfg = recommend_config(llama3_8b(), hw, "vllm")
        assert cfg["dtype"] != "fp8"
        assert cfg["quantization"] != "fp8"
        assert cfg["kv_cache_dtype"] != "fp8"

    def test_fp8_available_on_capable_card(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        cfg = recommend_config(llama3_8b(), hw, "vllm")
        # KV is fp8 on a capable card.
        assert cfg["kv_cache_dtype"] == "fp8"

    def test_quant_picked_to_fit_vram(self) -> None:
        # 70B (~140GB bf16) on a single 24GB 4090 cannot fit bf16; must pick an
        # aggressive quant — and 4090 has fp8 so fp8/awq are options.
        hw = hardware_profile_for("rtx_4090", gpu_count=1)
        # 70B even at 4-bit (~35GB) won't fit 24GB -> should raise (fail-closed).
        with pytest.raises(ValueError):
            recommend_config(llama3_70b(), hw, "vllm")

    def test_8b_fits_a100_bf16(self) -> None:
        hw = hardware_profile_for("a100_80", gpu_count=1)
        cfg = recommend_config(llama3_8b(), hw, "vllm")
        assert cfg["dtype"] == "bf16"
        assert cfg["max_model_len"] >= 256
        assert cfg["max_model_len"] % 256 == 0

    def test_capable_card_does_not_enforce_eager(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        cfg = recommend_config(llama3_8b(), hw, "vllm")
        assert cfg["enforce_eager"] is False

    def test_max_model_len_fits_kv_pool(self) -> None:
        hw = hardware_profile_for("a100_80", gpu_count=1)
        seqs = 64
        cfg = recommend_config(llama3_8b(), hw, "vllm", max_num_seqs=seqs)
        max_len = cfg["max_model_len"]
        assert isinstance(max_len, int)
        kv = kv_cache_bytes(llama3_8b(), max_len, seqs, "fp16")
        # KV for the recommended length must be < total VRAM (sanity bound).
        assert kv < hw.total_vram_gb * 1e9

    def test_deepseek_v3_native_fp8_on_hopper(self) -> None:
        hw = hardware_profile_for("h200", gpu_count=8)
        cfg = recommend_config(deepseek_v3(), hw, "vllm")
        assert cfg["quantization"] == "fp8"

    def test_gmu_bounds_enforced(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        with pytest.raises(ValueError):
            recommend_config(llama3_8b(), hw, "vllm", gpu_memory_utilization=0.99)

    def test_unknown_engine_rejected(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        with pytest.raises(ValueError):
            recommend_config(llama3_8b(), hw, "tensorrt")


# ---------------------------------------------------------------------------
# recommend_config — llama.cpp
# ---------------------------------------------------------------------------


class TestRecommendLlamaCpp:
    def test_gguf_quant_and_flash_attn(self) -> None:
        hw = hardware_profile_for("rtx_4090", gpu_count=1)
        cfg = recommend_config(llama3_8b(), hw, "llamacpp")
        assert cfg["engine"] == "llamacpp"
        assert str(cfg["gguf_quant"]).startswith("q")
        assert cfg["flash_attn"] is True

    def test_no_tensor_parallel(self) -> None:
        hw = hardware_profile_for("rtx_4090", gpu_count=2)
        cfg = recommend_config(llama3_8b(), hw, "llamacpp")
        assert cfg["tensor_parallel_size"] == 1

    def test_full_offload_when_fits(self) -> None:
        # 8B Q4 (~5GB) fits 24GB easily -> all layers on GPU.
        hw = hardware_profile_for("rtx_4090", gpu_count=1)
        cfg = recommend_config(llama3_8b(), hw, "llamacpp")
        assert cfg["n_gpu_layers"] == -1

    def test_partial_offload_when_too_big(self) -> None:
        # 70B at q4 (~35GB) does NOT fit a single 24GB 4090 -> partial offload.
        hw = hardware_profile_for("rtx_4090", gpu_count=1)
        cfg = recommend_config(llama3_70b(), hw, "llamacpp")
        ngl = cfg["n_gpu_layers"]
        assert isinstance(ngl, int) and 0 <= ngl < 80
