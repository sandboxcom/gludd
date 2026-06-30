"""Unit tests for the deployment optimization config module."""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig
from general_ludd.infra.deployment_optimizer import hardware_profile_for

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_YAML_PATH = Path("config/infra/deployment_optimization.yml")


def _load_config() -> DeploymentOptimizationConfig:
    return DeploymentOptimizationConfig.from_yaml(_YAML_PATH)


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def test_yaml_loading_structure() -> None:
    cfg = _load_config()
    assert cfg.vllm_defaults == {
        "gpu_memory_utilization": 0.90,
        "max_num_seqs": 256,
        "enforce_eager": False,
        "enable_prefix_caching": True,
        "enable_chunked_prefill": True,
    }
    assert cfg.llamacpp_defaults == {
        "flash_attn": True,
        "n_threads": 0,
        "split_mode": "layer",
    }
    assert "h100" in cfg.hardware_presets
    assert cfg.hardware_presets["h100"]["vram_tier"] == "high_vram"
    assert "l40s" in cfg.hardware_presets
    assert cfg.hardware_presets["l40s"]["vram_tier"] == "mid_vram"
    assert "t4" in cfg.hardware_presets
    assert cfg.hardware_presets["t4"]["vram_tier"] == "low_vram"
    assert len(cfg.quantization_recommendations) == 3
    assert cfg.quantization_recommendations[0]["recommendation"] == "fp8"
    assert cfg.quantization_recommendations[1]["recommendation"] == "awq"
    assert cfg.quantization_recommendations[2]["recommendation"] == "q4_k_m"


# ---------------------------------------------------------------------------
# get_preset — vLLM
# ---------------------------------------------------------------------------


def test_get_preset_vllm_high_vram_h100() -> None:
    cfg = _load_config()
    result = cfg.get_preset("vllm", "h100")
    assert result["gpu_memory_utilization"] == 0.90
    assert result["tensor_parallel_size"] == 8
    assert result["dtype"] == "bf16"
    assert result["pipeline_parallel_size"] == 1
    assert result["vram_tier"] == "high_vram"
    assert result["enforce_eager"] is False


def test_get_preset_vllm_mid_vram_l40s() -> None:
    cfg = _load_config()
    result = cfg.get_preset("vllm", "l40s")
    assert result["tensor_parallel_size"] == 1
    assert result["pipeline_parallel_size"] == 2
    assert result["dtype"] == "bf16"
    assert result["vram_tier"] == "mid_vram"


def test_get_preset_vllm_low_vram_t4() -> None:
    cfg = _load_config()
    result = cfg.get_preset("vllm", "t4")
    assert result["tensor_parallel_size"] == 1
    assert result["dtype"] == "fp16"
    assert result["vram_tier"] == "low_vram"


# ---------------------------------------------------------------------------
# get_preset — llama.cpp
# ---------------------------------------------------------------------------


def test_get_preset_llamacpp_high_vram_h100() -> None:
    cfg = _load_config()
    result = cfg.get_preset("llamacpp", "h100")
    assert result["flash_attn"] is True
    assert result["n_threads"] == 0
    assert result["gguf_quant"] == "q8_0"
    assert result["n_gpu_layers"] == -1
    assert result["vram_tier"] == "high_vram"


def test_get_preset_llamacpp_mid_vram_l40s() -> None:
    cfg = _load_config()
    result = cfg.get_preset("llamacpp", "l40s")
    assert result["gguf_quant"] == "q6_k"
    assert result["n_gpu_layers"] == -1
    assert result["flash_attn"] is True


def test_get_preset_llamacpp_low_vram_t4() -> None:
    cfg = _load_config()
    result = cfg.get_preset("llamacpp", "t4")
    assert result["gguf_quant"] == "q4_k_m"
    assert result["n_gpu_layers"] == -1
    assert result["flash_attn"] is True


# ---------------------------------------------------------------------------
# get_preset — kwargs override
# ---------------------------------------------------------------------------


def test_get_preset_explicit_kwargs_override_preset() -> None:
    cfg = _load_config()
    result = cfg.get_preset("vllm", "h100", tensor_parallel_size=4, dtype="fp8")
    assert result["tensor_parallel_size"] == 4
    assert result["dtype"] == "fp8"
    assert result["gpu_memory_utilization"] == 0.90


def test_get_preset_kwargs_override_defaults() -> None:
    cfg = _load_config()
    result = cfg.get_preset("vllm", "t4", gpu_memory_utilization=0.75)
    assert result["gpu_memory_utilization"] == 0.75
    assert result["tensor_parallel_size"] == 1


# ---------------------------------------------------------------------------
# get_preset — unknown engine
# ---------------------------------------------------------------------------


def test_get_preset_unknown_engine_raises_valueerror() -> None:
    cfg = _load_config()
    with pytest.raises(ValueError, match="unknown engine"):
        cfg.get_preset("tensorrt", "h100")


# ---------------------------------------------------------------------------
# validate_against_hardware
# ---------------------------------------------------------------------------


def test_validate_too_large_tensor_parallel_on_non_nvlink_raises() -> None:
    cfg = _load_config()
    hp = hardware_profile_for("l40s", gpu_count=2)
    assert hp.has_nvlink is False
    with pytest.raises(ValueError, match="NVLink"):
        cfg.validate_against_hardware({"tensor_parallel_size": 2}, hp)


def test_validate_fp8_quant_on_non_fp8_gpu_raises() -> None:
    cfg = _load_config()
    hp = hardware_profile_for("rtx_3090")
    assert hp.supports_fp8 is False
    with pytest.raises(ValueError, match="fp8"):
        cfg.validate_against_hardware({"quantization": "fp8"}, hp)


def test_validate_fp8_dtype_on_non_fp8_gpu_raises() -> None:
    cfg = _load_config()
    hp = hardware_profile_for("a100_80")
    assert hp.supports_fp8 is False
    with pytest.raises(ValueError, match="fp8"):
        cfg.validate_against_hardware({"dtype": "fp8"}, hp)


def test_validate_valid_config_passes() -> None:
    cfg = _load_config()
    hp = hardware_profile_for("h100", gpu_count=1)
    cfg.validate_against_hardware(
        {"tensor_parallel_size": 1, "quantization": "fp8"}, hp
    )


def test_validate_insufficient_vram_raises() -> None:
    cfg = _load_config()
    hp = hardware_profile_for("t4")
    with pytest.raises(ValueError, match="does not fit"):
        cfg.validate_against_hardware(
            {"params_b": 70, "gpu_memory_utilization": 0.9}, hp
        )


# ---------------------------------------------------------------------------
# recommend_quantization
# ---------------------------------------------------------------------------


def test_recommend_quantization_large_model_80gb_returns_fp8() -> None:
    cfg = _load_config()
    assert cfg.recommend_quantization(70, 80) == "fp8"


def test_recommend_quantization_medium_model_24gb_returns_awq() -> None:
    cfg = _load_config()
    assert cfg.recommend_quantization(13, 24) == "awq"


def test_recommend_quantization_small_model_16gb_returns_q4_k_m() -> None:
    cfg = _load_config()
    assert cfg.recommend_quantization(7, 16) == "q4_k_m"


def test_recommend_quantization_no_matching_rule_returns_none() -> None:
    cfg = _load_config()
    assert cfg.recommend_quantization(70, 16) is None
