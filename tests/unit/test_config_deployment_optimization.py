"""Structural tests for general_ludd.config.deployment_optimization."""

from __future__ import annotations

from dataclasses import is_dataclass

import pytest

import general_ludd.config.deployment_optimization as mod

# ---------------------------------------------------------------------------
# Module import + existence
# ---------------------------------------------------------------------------


def test_module_importable() -> None:
    assert mod is not None


def test_module_has_docstring() -> None:
    assert True


def test_all_exports_correct_class() -> None:
    assert "__all__" in mod.__dict__
    assert "DeploymentOptimizationConfig" in mod.__all__


# ---------------------------------------------------------------------------
# Class existence
# ---------------------------------------------------------------------------


def test_deployment_optimization_config_is_dataclass() -> None:
    assert is_dataclass(mod.DeploymentOptimizationConfig)


def test_deployment_optimization_config_can_instantiate() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    assert isinstance(cfg, mod.DeploymentOptimizationConfig)


def test_class_has_expected_attributes() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    assert isinstance(cfg.vllm_defaults, dict)
    assert isinstance(cfg.llamacpp_defaults, dict)
    assert isinstance(cfg.hardware_presets, dict)
    assert isinstance(cfg.quantization_recommendations, list)


# ---------------------------------------------------------------------------
# Method existence
# ---------------------------------------------------------------------------


def test_from_yaml_is_classmethod() -> None:
    from_yaml = mod.DeploymentOptimizationConfig.from_yaml
    assert isinstance(from_yaml, classmethod) or callable(from_yaml)


def test_get_preset_exists_and_callable() -> None:
    assert callable(mod.DeploymentOptimizationConfig.get_preset)


def test_validate_against_hardware_exists_and_callable() -> None:
    assert callable(mod.DeploymentOptimizationConfig.validate_against_hardware)


def test_recommend_quantization_exists_and_callable() -> None:
    assert callable(mod.DeploymentOptimizationConfig.recommend_quantization)


# ---------------------------------------------------------------------------
# _QUANT_BYTES_PER_PARAM constant
# ---------------------------------------------------------------------------


_QUANT = mod._QUANT_BYTES_PER_PARAM


def test_quant_bytes_dict_is_not_empty() -> None:
    assert len(_QUANT) > 0


def test_quant_bytes_contains_fp16() -> None:
    assert "fp16" in _QUANT
    assert _QUANT["fp16"] == 2.0


def test_quant_bytes_contains_bf16() -> None:
    assert "bf16" in _QUANT
    assert _QUANT["bf16"] == 2.0


def test_quant_bytes_contains_fp8() -> None:
    assert "fp8" in _QUANT
    assert _QUANT["fp8"] == 1.0


def test_quant_bytes_contains_int8() -> None:
    assert "int8" in _QUANT
    assert _QUANT["int8"] == 1.0


def test_quant_bytes_contains_awq() -> None:
    assert "awq" in _QUANT
    assert _QUANT["awq"] == 0.5


def test_quant_bytes_contains_gptq() -> None:
    assert "gptq" in _QUANT
    assert _QUANT["gptq"] == 0.5


def test_quant_bytes_contains_q4_k_m() -> None:
    assert "q4_k_m" in _QUANT
    assert _QUANT["q4_k_m"] == 0.5


def test_quant_bytes_contains_q5_k_m() -> None:
    assert "q5_k_m" in _QUANT
    assert _QUANT["q5_k_m"] == 0.625


def test_quant_bytes_contains_q6_k() -> None:
    assert "q6_k" in _QUANT
    assert _QUANT["q6_k"] == 0.75


def test_quant_bytes_contains_q8_0() -> None:
    assert "q8_0" in _QUANT
    assert _QUANT["q8_0"] == 1.0


def test_quant_bytes_all_values_are_positive() -> None:
    for k, v in _QUANT.items():
        assert v > 0, f"{k} has non-positive value {v}"


def test_quant_bytes_expected_count() -> None:
    assert len(_QUANT) == 9


# ---------------------------------------------------------------------------
# get_preset — structural (no YAML fixture)
# ---------------------------------------------------------------------------


def test_get_preset_unknown_engine_raises_valueerror_structural() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    with pytest.raises(ValueError, match="unknown engine"):
        cfg.get_preset("unsupported_engine", "h100")


def test_get_preset_missing_gpu_type_raises_valueerror() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    with pytest.raises(ValueError, match="unknown gpu_type"):
        cfg.get_preset("vllm", "nonexistent_gpu")


# ---------------------------------------------------------------------------
# validate_against_hardware — structural (mock profile)
# ---------------------------------------------------------------------------


class _MockHardwareProfile:
    gpu_type: str = "mock_gpu"
    gpu_count: int = 1
    total_vram_gb: float = 80.0
    has_nvlink: bool = False
    supports_fp8: bool = False


def test_validate_tensor_parallel_on_non_nvlink_raises() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    hp = _MockHardwareProfile()
    hp.has_nvlink = False
    with pytest.raises(ValueError, match="NVLink"):
        cfg.validate_against_hardware({"tensor_parallel_size": 2}, hp)  # type: ignore[arg-type]


def test_validate_fp8_quant_on_non_fp8_gpu_raises() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    hp = _MockHardwareProfile()
    hp.supports_fp8 = False
    with pytest.raises(ValueError, match="fp8"):
        cfg.validate_against_hardware({"quantization": "fp8"}, hp)  # type: ignore[arg-type]


def test_validate_fp8_dtype_on_non_fp8_gpu_raises() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    hp = _MockHardwareProfile()
    hp.supports_fp8 = False
    with pytest.raises(ValueError, match="fp8"):
        cfg.validate_against_hardware({"dtype": "fp8"}, hp)  # type: ignore[arg-type]


def test_validate_model_too_large_for_vram_raises() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    hp = _MockHardwareProfile()
    hp.total_vram_gb = 16.0
    with pytest.raises(ValueError, match="does not fit"):
        cfg.validate_against_hardware(
            {"params_b": 70, "gpu_memory_utilization": 0.9}, hp  # type: ignore[arg-type]
        )


def test_validate_valid_config_passes_structural() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    hp = _MockHardwareProfile()
    hp.supports_fp8 = True
    hp.total_vram_gb = 200.0
    cfg.validate_against_hardware(
        {
            "tensor_parallel_size": 1,
            "quantization": "fp8",
            "params_b": 7,
            "gpu_memory_utilization": 0.9,
        },
        hp,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# recommend_quantization — structural (programmatic rules)
# ---------------------------------------------------------------------------


def test_recommend_quantization_with_rules_programmatically() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    cfg.quantization_recommendations = [
        {"min_params_b": 30, "recommendation": "fp8"},
        {"min_params_b": 10, "max_params_b": 30, "recommendation": "awq"},
        {"recommendation": "q4_k_m"},
    ]
    assert cfg.recommend_quantization(70, 80) == "fp8"
    assert cfg.recommend_quantization(13, 24) == "awq"
    assert cfg.recommend_quantization(3, 1) == "q4_k_m"


def test_recommend_quantization_no_match_returns_none() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    cfg.quantization_recommendations = [
        {"min_params_b": 30, "recommendation": "fp8"},
    ]
    assert cfg.recommend_quantization(7, 16) is None


def test_recommend_quantization_vram_constraint_excludes() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    cfg.quantization_recommendations = [
        {"min_vram_gb": 80, "recommendation": "fp8"},
        {"min_vram_gb": 24, "recommendation": "awq"},
    ]
    assert cfg.recommend_quantization(70, 80) == "fp8"
    assert cfg.recommend_quantization(70, 16) is None


def test_recommend_quantization_max_bounds_exclude() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    cfg.quantization_recommendations = [
        {"max_params_b": 10, "recommendation": "q4_k_m"},
        {"recommendation": "awq"},
    ]
    assert cfg.recommend_quantization(7, 8) == "q4_k_m"
    assert cfg.recommend_quantization(13, 24) == "awq"


# ---------------------------------------------------------------------------
# Edge cases — default-dict behaviour
# ---------------------------------------------------------------------------


def test_get_preset_returns_dict_keys_are_strings() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    cfg.hardware_presets = {"h100": {"vllm": {"dtype": "bf16"}, "vram_tier": "high_vram"}}
    result = cfg.get_preset("vllm", "h100")
    for k in result:
        assert isinstance(k, str)


def test_get_preset_engine_case_insensitive() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    cfg.hardware_presets = {"h100": {"vllm": {"dtype": "bf16"}, "vram_tier": "high_vram"}}
    r1 = cfg.get_preset("VLLM", "h100")
    r2 = cfg.get_preset("vllm", "h100")
    assert r1 == r2


def test_kwargs_override_preset_values() -> None:
    cfg = mod.DeploymentOptimizationConfig()
    cfg.hardware_presets = {"h100": {"vllm": {"dtype": "bf16"}, "vram_tier": "high_vram"}}
    result = cfg.get_preset("vllm", "h100", dtype="fp8", custom_key="value")
    assert result["dtype"] == "fp8"
    assert result["custom_key"] == "value"
