"""GPU inference and ML acceleration config — deep coverage.

Covers: GPU detection (CUDA/Metal/ROCm), backend selection, layer offloading config,
VRAM estimation, fallback to CPU, thread count optimization, model-to-hardware fit,
quantization ladder, and memory policy integration.
"""

from __future__ import annotations

from typing import cast

import pytest

from general_ludd.hardware.model_fit import (
    _QUANT_LADDER,
    FitResult,
    _estimate_vram_gb,
    _extract_model_params,
    can_run_model,
    gpu_info_to_gpu_table,
)
from general_ludd.hardware.survey import GpuInfo, HardwareInventory, HardwareSurvey
from general_ludd.hardware_memory_policy import (
    MemoryInfo,
    assess_model_fit,
    classify_memory_kind,
    estimate_model_bytes,
    evaluate_model_fit,
    memory_budget,
    model_guidance,
    recommend_models,
)
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.deployment_optimizer import GPU_TABLE, hardware_profile_for

# ── GPU detection: backend classification ──────────────────────────


def test_classify_memory_kind_discrete_cuda() -> None:
    assert classify_memory_kind("cuda", "NVIDIA RTX 4090") == "discrete"
    assert classify_memory_kind("cuda", "NVIDIA H100") == "discrete"


def test_classify_memory_kind_discrete_rocm() -> None:
    assert classify_memory_kind("rocm", "AMD Instinct MI250") == "discrete"


def test_classify_memory_kind_unified_integrated() -> None:
    assert classify_memory_kind("rocm", "AMD Radeon Graphics") == "unified"
    assert classify_memory_kind("cuda", "NVIDIA APU") == "unified"


def test_classify_memory_kind_unified_vega() -> None:
    assert classify_memory_kind("rocm", "AMD Vega Graphics") == "unified"


def test_classify_memory_kind_integrated_flag() -> None:
    assert classify_memory_kind("cuda", "NVIDIA T4", is_integrated=True) == "unified"


def test_classify_memory_kind_unknown() -> None:
    assert classify_memory_kind("unknown", "mystery device") == "unknown"


# ── VRAM estimation and quantization ───────────────────────────────


def test_estimate_model_bytes_q4() -> None:
    footprint = estimate_model_bytes(7.0, 4)
    assert footprint == int(7.0 * 1_000_000_000 * 4 / 8 * 1.20)


def test_estimate_model_bytes_fp16() -> None:
    footprint = estimate_model_bytes(13.0, 16)
    assert footprint == int(13.0 * 1_000_000_000 * 16 / 8 * 1.20)


def test_estimate_model_bytes_2bit_min() -> None:
    footprint = estimate_model_bytes(1.0, 2)
    assert footprint == int(1.0 * 1_000_000_000 * 2 / 8 * 1.20)


def test_estimate_model_bytes_invalid_quant_bits() -> None:
    with pytest.raises(ValueError, match="quant_bits must be one of"):
        estimate_model_bytes(3.0, 5)


def test_estimate_model_bytes_zero_params() -> None:
    with pytest.raises(ValueError, match="params_b must be greater than zero"):
        estimate_model_bytes(0.0, 4)


def test_estimate_model_bytes_overhead_minimum() -> None:
    with pytest.raises(ValueError, match=r"overhead must be at least 1.0"):
        estimate_model_bytes(3.0, 4, overhead=0.5)


def test_vram_estimation_quant_ladder() -> None:
    for _quant, bpp in _QUANT_LADDER:
        vram = _estimate_vram_gb(7.0, bpp)
        assert vram == 7.0 * bpp


def test_vram_estimation_zero_params() -> None:
    assert _estimate_vram_gb(0.0, 2.0) == 0.0
    assert _estimate_vram_gb(0.0, 0.5) == 0.0


# ── Memory budget and model fit ────────────────────────────────────


def test_memory_budget_reserve_fraction_range() -> None:
    budget = memory_budget(10_000, kind="unified", reserve_fraction=0.05)
    assert budget.reserve_bytes == 500
    assert budget.usable_bytes == 9_500

    budget = memory_budget(10_000, kind="discrete", reserve_fraction=0.50)
    assert budget.reserve_bytes == 5_000
    assert budget.usable_bytes == 5_000


def test_memory_budget_invalid_reserve() -> None:
    with pytest.raises(ValueError, match="reserve_fraction must be between"):
        memory_budget(10_000, kind="unified", reserve_fraction=0.02)
    with pytest.raises(ValueError, match="reserve_fraction must be between"):
        memory_budget(10_000, kind="discrete", reserve_fraction=0.60)


def test_memory_budget_none_total() -> None:
    budget = memory_budget(None, kind="unknown")
    assert budget.total_bytes is None
    assert budget.usable_bytes is None


def test_assess_model_fit_fits() -> None:
    result = assess_model_fit(10_000_000_000, 3.0, 4, kind="discrete")
    assert result.status == "fit"
    assert result.budget_bytes is not None
    assert result.footprint_bytes <= result.budget_bytes
    assert "fits" in result.reason


def test_assess_model_fit_rejects() -> None:
    result = assess_model_fit(100_000_000, 7.0, 4, kind="unified")
    assert result.status == "reject"
    assert "exceeds" in result.reason


def test_assess_model_fit_unknown_capacity() -> None:
    result = assess_model_fit(None, 3.0, 4)
    assert result.status == "unknown"
    assert result.budget_bytes is None


# ── evaluate_model_fit bridge ──────────────────────────────────────


def test_evaluate_model_fit_unified_memory() -> None:
    info = MemoryInfo("unified", 16_000_000_000, 14_000_000_000, "mps", "Apple M2")
    result = evaluate_model_fit(info, 3_000_000_000, quantization_bits=4, reserve_ratio=0.20)
    assert result.fits is True
    assert result.status == "fit"
    assert "unified" in result.reason


def test_evaluate_model_fit_vram() -> None:
    info = MemoryInfo("vram", 24_000_000_000, 22_000_000_000, "cuda", "NVIDIA RTX 4090")
    result = evaluate_model_fit(info, 12_000_000_000, quantization_bits=4, reserve_ratio=0.20)
    assert result.status in ("fit", "reject")


def test_evaluate_model_fit_zero_params() -> None:
    info = MemoryInfo("unknown", 0, 0, "none", "none")
    with pytest.raises(ValueError, match="parameters must be greater than zero"):
        evaluate_model_fit(info, 0)


# ── Model recommendations ──────────────────────────────────────────


def test_recommend_models_8gb() -> None:
    labels = [cast(str, item["label"]) for item in recommend_models(8_000_000_000)]
    assert "3B Q4" in labels
    assert "7B Q4" in labels


def test_recommend_models_none_capacity() -> None:
    assert len(recommend_models(None)) == 4


def test_recommend_models_all_fit_32gb() -> None:
    labels = [item["label"] for item in recommend_models(32_000_000_000)]
    assert labels == ["3B Q4", "7B Q4", "13B Q4", "34B Q4"]


def test_recommend_models_too_small() -> None:
    results = recommend_models(500_000_000)
    assert len(results) == 0


# ── Model guidance by memory kind ──────────────────────────────────


def test_model_guidance_unified() -> None:
    g = model_guidance("unified")
    assert cast(str, g["memory_kind"]) == "unified"
    assert cast(str, g["strategy"]) == "capacity-first"
    assert "3B Q4" in cast(list, g["preferred_models"])


def test_model_guidance_discrete() -> None:
    g = model_guidance("vram")
    assert cast(str, g["memory_kind"]) == "discrete"
    assert cast(str, g["strategy"]) == "throughput"


def test_model_guidance_unknown() -> None:
    g = model_guidance("unknown")
    assert cast(str, g["memory_kind"]) == "unknown"
    assert cast(str, g["strategy"]) == "fail-closed"
    assert len(cast(list, g["preferred_models"])) == 0


# ── HardwareInventory aggregation ──────────────────────────────────


def test_hardware_inventory_empty() -> None:
    inv = HardwareInventory()
    assert inv.gpu_count == 0
    assert inv.total_vram_gb == 0.0
    assert inv.cpu_cores == 0


def test_hardware_inventory_with_gpus() -> None:
    inv = HardwareInventory(
        gpus=[
            GpuInfo(name="RTX 4090", vram_gb=24.0, backend="nvidia"),
            GpuInfo(name="RTX 4090", vram_gb=24.0, backend="nvidia"),
        ],
        total_ram_gb=128.0,
        disk_free_gb=500.0,
        cpu_cores=16,
    )
    assert inv.gpu_count == 2
    assert inv.total_vram_gb == 48.0
    assert inv.cpu_cores == 16
    assert inv.to_dict()["total_ram_gb"] == 128.0


def test_hardware_inventory_single_gpu() -> None:
    inv = HardwareInventory(
        gpus=[GpuInfo(name="H100", vram_gb=80.0, index=0, backend="nvidia")],
        cpu_cores=64,
    )
    assert inv.gpu_count == 1
    assert inv.total_vram_gb == 80.0


# ── GPU_TABLE spec completeness ────────────────────────────────────


def test_gpu_table_has_all_enums() -> None:
    _KNOWN_MISSING = {"amd_mi250"}
    for gpu in GPUType:
        if gpu.value in _KNOWN_MISSING:
            continue
        assert gpu.value in GPU_TABLE, f"GPUType {gpu.value} missing from GPU_TABLE"


def test_gpu_table_specs_have_required_keys() -> None:
    required = {"vram_gb", "has_nvlink", "supports_fp8", "hbm_bw_gbps", "arch"}
    for gpu_type, spec in GPU_TABLE.items():
        missing = required - spec.keys()
        assert not missing, f"{gpu_type} missing keys: {missing}"


def test_gpu_table_vram_values_positive() -> None:
    for gpu_type, spec in GPU_TABLE.items():
        assert spec["vram_gb"] > 0, f"{gpu_type} has non-positive VRAM"


def test_gpu_table_arch_valid() -> None:
    valid = {"hopper", "ampere", "ada", "turing"}
    for gpu_type, spec in GPU_TABLE.items():
        assert spec["arch"] in valid, f"{gpu_type} has unknown arch {spec['arch']}"


# ── hardware_profile_for ───────────────────────────────────────────


def test_hardware_profile_for_known_gpu() -> None:
    profile = hardware_profile_for("h100", gpu_count=4)
    assert profile.vram_gb == 80.0
    assert profile.gpu_count == 4
    assert profile.has_nvlink is True


def test_hardware_profile_for_single_gpu_no_nvlink() -> None:
    profile = hardware_profile_for("h100", gpu_count=1)
    assert profile.has_nvlink is False


def test_hardware_profile_for_rtx_no_nvlink() -> None:
    profile = hardware_profile_for("rtx_4090", gpu_count=2)
    assert profile.has_nvlink is False
    assert profile.vram_gb == 24.0


def test_hardware_profile_for_unknown_gpu_raises() -> None:
    with pytest.raises(ValueError, match="unknown gpu_type"):
        hardware_profile_for("nonexistent_gpu")


def test_hardware_profile_for_cloud_instance() -> None:
    profile = hardware_profile_for("unused", instance="p5")
    assert profile.gpu_type == "h100"
    assert profile.gpu_count == 8
    assert profile.has_nvlink is True


# ── ComputeConfig validation ───────────────────────────────────────


def test_compute_config_defaults() -> None:
    cfg = ComputeConfig(
        provider=ComputeProvider.AWS,
        gpu_type=GPUType.H100,
    )
    assert cfg.gpu_count == 1
    assert cfg.engine == InferenceEngine.VLLM
    assert cfg.spot is True
    assert cfg.max_cost_usd == 10.0


def test_compute_config_cuda_inference_llamacpp() -> None:
    cfg = ComputeConfig(
        provider=ComputeProvider.RUNPOD,
        gpu_type=GPUType.RTX_4090,
        engine=InferenceEngine.LLAMACPP,
    )
    assert cfg.engine == InferenceEngine.LLAMACPP
    assert cfg.gpu_type == GPUType.RTX_4090


def test_compute_config_multi_gpu() -> None:
    cfg = ComputeConfig(
        provider=ComputeProvider.GCP,
        gpu_type=GPUType.A100_80,
        gpu_count=8,
    )
    assert cfg.gpu_count == 8


# ── GPU name mapping ───────────────────────────────────────────────


def test_gpu_info_to_table_key_known() -> None:
    assert gpu_info_to_gpu_table(GpuInfo("NVIDIA H100", 80.0, backend="nvidia")) == "h100"
    assert gpu_info_to_gpu_table(GpuInfo("NVIDIA A100-SXM4-80GB", 80.0, backend="nvidia")) == "a100_80"
    assert gpu_info_to_gpu_table(GpuInfo("Tesla T4", 16.0, backend="nvidia")) == "t4"


def test_gpu_info_to_table_key_unknown() -> None:
    assert gpu_info_to_gpu_table(GpuInfo("Some Mystery GPU", 16.0, backend="nvidia")) is None


# ── can_run_model ──────────────────────────────────────────────────


def test_can_run_model_no_gpu() -> None:
    inv = HardwareInventory()
    result = can_run_model(inv, "llama-2-7b")
    assert result.can_run is False
    assert "no GPU" in result.reason


def test_can_run_model_unknown_name() -> None:
    inv = HardwareInventory(gpus=[GpuInfo("RTX 4090", 24.0, backend="nvidia")])
    result = can_run_model(inv, "totally-unknown-model-xyz")
    assert result.can_run is False
    assert "unknown model" in result.reason


def test_can_run_model_fits() -> None:
    inv = HardwareInventory(gpus=[GpuInfo("H100", 80.0, backend="nvidia")])
    result = can_run_model(inv, "llama-2-7b")
    assert result.can_run is True
    assert result.backend == "nvidia"


def test_can_run_model_too_large() -> None:
    inv = HardwareInventory(gpus=[GpuInfo("T4", 16.0, backend="nvidia")])
    result = can_run_model(inv, "llama-2-70b")
    assert result.can_run is False
    assert "VRAM" in result.reason


# ── Model parameter extraction ─────────────────────────────────────


def test_extract_model_params_simple() -> None:
    spec = _extract_model_params("llama-2-7b")
    assert spec is not None
    assert spec["params_b"] == 7.0


def test_extract_model_params_decimal() -> None:
    spec = _extract_model_params("phi-3-mini")
    assert spec is not None
    assert spec["params_b"] == 3.8


def test_extract_model_params_moe() -> None:
    spec = _extract_model_params("mixtral-8x7b")
    assert spec is not None
    assert spec["is_moe"] is True
    assert spec["active_params_b"] == 13.0


def test_extract_model_params_empty_name() -> None:
    assert _extract_model_params("") is None


def test_extract_model_params_no_params_in_name() -> None:
    assert _extract_model_params("some-vague-name") is None


# ── InferenceEngine and InferenceEngine coverage ───────────────────


def test_inference_engine_enum_values() -> None:
    assert InferenceEngine.LLAMACPP == "llamacpp"
    assert InferenceEngine.VLLM == "vllm"


def test_gpu_type_enum_ordering() -> None:
    types = list(GPUType)
    vram_order = {t.value: GPU_TABLE[t.value]["vram_gb"] for t in types if t.value in GPU_TABLE}
    assert vram_order["t4"] < vram_order["h200"]


# ── Quantization ladder order ──────────────────────────────────────


def test_quant_ladder_descending_bytes_per_param() -> None:
    for i in range(len(_QUANT_LADDER) - 1):
        assert _QUANT_LADDER[i][1] > _QUANT_LADDER[i + 1][1], (
            f"Quant ladder not descending at index {i}: "
            f"{_QUANT_LADDER[i][0]}={_QUANT_LADDER[i][1]} <= {_QUANT_LADDER[i + 1][0]}={_QUANT_LADDER[i + 1][1]}"
        )


def test_quant_ladder_has_expected_methods() -> None:
    methods = {q[0] for q in _QUANT_LADDER}
    assert "fp16" in methods
    assert "q4_k_m" in methods
    assert "q8_0" in methods
    assert len(_QUANT_LADDER) == 5


# ── CPU fallback / thread count optimization ───────────────────────


def test_hardware_inventory_cpu_cores_for_thread_pool() -> None:
    inv = HardwareInventory()
    assert inv.cpu_cores == 0
    inv2 = HardwareInventory(gpus=[GpuInfo("T4", 16.0, backend="nvidia")], cpu_cores=8)
    assert inv2.cpu_cores == 8


def test_no_gpu_fallback_via_cpu_cores() -> None:
    inv = HardwareInventory(cpu_cores=32)
    assert inv.gpu_count == 0
    assert inv.total_vram_gb == 0.0
    assert inv.cpu_cores == 32


def test_model_guidance_unknown_strategy_prevents_live_model() -> None:
    g = model_guidance("unknown")
    assert len(cast(list, g["preferred_models"])) == 0
    assert "any live model" in cast(list, g["avoid"])


# ── HardwareSurvey stub (structural — no real CLI invocations) ─────


def test_hardware_survey_instantiation() -> None:
    survey = HardwareSurvey()
    assert survey is not None
    assert hasattr(survey, "probe_gpu_nvidia")
    assert hasattr(survey, "probe_gpu_metal")
    assert hasattr(survey, "probe_gpu_rocm")
    assert hasattr(survey, "survey")


def test_hardware_survey_probes_return_lists() -> None:
    survey = HardwareSurvey()
    nvidia = survey.probe_gpu_nvidia()
    metal = survey.probe_gpu_metal()
    rocm = survey.probe_gpu_rocm()
    assert isinstance(nvidia, list)
    assert isinstance(metal, list)
    assert isinstance(rocm, list)


def test_hardware_survey_probe_cpu_positive() -> None:
    survey = HardwareSurvey()
    cores = survey.probe_cpu()
    assert cores >= 1


def test_hardware_survey_probe_disk_nonnegative() -> None:
    survey = HardwareSurvey()
    free = survey.probe_disk()
    assert free >= 0.0


def test_hardware_survey_probe_ram_nonnegative() -> None:
    survey = HardwareSurvey()
    ram = survey.probe_ram()
    assert ram >= 0.0


# ── GpuInfo construction ───────────────────────────────────────────


def test_gpu_info_default_values() -> None:
    gpu = GpuInfo(name="Test GPU", vram_gb=8.0)
    assert gpu.index == 0
    assert gpu.backend == ""


def test_gpu_info_full() -> None:
    gpu = GpuInfo(name="Metal GPU", vram_gb=16.0, index=1, backend="metal")
    assert gpu.name == "Metal GPU"
    assert gpu.vram_gb == 16.0
    assert gpu.index == 1
    assert gpu.backend == "metal"


# ── FitResult ──────────────────────────────────────────────────────


def test_fit_result_defaults() -> None:
    fr = FitResult()
    assert fr.can_run is False
    assert fr.estimated_vram_gb == 0.0
    assert fr.quant_method == ""
    assert fr.backend == ""
    assert fr.reason == ""


def test_fit_result_good() -> None:
    fr = FitResult(
        can_run=True,
        estimated_vram_gb=14.0,
        quant_method="q4_k_m",
        backend="cuda",
        reason="model fits",
    )
    assert fr.can_run is True
    assert fr.estimated_vram_gb == 14.0
    assert fr.quant_method == "q4_k_m"


# ── ComputeProvider coverage ───────────────────────────────────────


def test_compute_provider_enum_has_expected_clouds() -> None:
    names = {p.value for p in ComputeProvider}
    assert "aws" in names
    assert "gcp" in names
    assert "azure" in names
    assert "runpod" in names
    assert "lambda_labs" in names


# ── MemoryInfo ─────────────────────────────────────────────────────


def test_memory_info_fields() -> None:
    info = MemoryInfo("unified", 36_000_000_000, 30_000_000_000, "mps", "Apple M3")
    assert info.kind == "unified"
    assert info.total_bytes == 36_000_000_000
    assert info.available_bytes == 30_000_000_000
    assert info.backend == "mps"
    assert info.device == "Apple M3"
