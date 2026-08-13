from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest import mock

import pytest

from general_ludd.hardware.model_fit import (
    FitResult,
    _extract_model_params,
    _gpu_name_to_table_key,
    can_run_model,
    gpu_info_to_gpu_table,
    unified_probe,
)
from general_ludd.hardware.survey import GpuInfo, HardwareInventory, HardwareSurvey


class TestFitResult:
    def test_constructor(self):
        r = FitResult(
            can_run=True,
            estimated_vram_gb=4.0,
            quant_method="q4_k_m",
            backend="nvidia",
            reason="fits",
        )
        assert r.can_run is True
        assert r.estimated_vram_gb == 4.0
        assert r.quant_method == "q4_k_m"
        assert r.backend == "nvidia"
        assert r.reason == "fits"

    def test_defaults(self):
        r = FitResult()
        assert r.can_run is False
        assert r.estimated_vram_gb == 0.0
        assert r.quant_method == ""
        assert r.backend == ""
        assert r.reason == ""

    def test_frozen(self):
        r = FitResult(can_run=True, estimated_vram_gb=8.0, quant_method="q6_k", backend="metal", reason="ok")
        with pytest.raises(FrozenInstanceError) as exc_info:
            r.can_run = False
        assert "cannot assign" in str(exc_info.value).lower()
        assert r.can_run is True

    def test_equality(self):
        a = FitResult(can_run=True, estimated_vram_gb=4.0, quant_method="q4_k_m", backend="nvidia", reason="fits")
        b = FitResult(can_run=True, estimated_vram_gb=4.0, quant_method="q4_k_m", backend="nvidia", reason="fits")
        assert a == b

    def test_distinct(self):
        a = FitResult(can_run=True, estimated_vram_gb=4.0, quant_method="q4_k_m", backend="nvidia", reason="fits")
        b = FitResult(can_run=False, estimated_vram_gb=4.0, quant_method="q4_k_m", backend="nvidia", reason="fits")
        assert a != b


class TestGpuInfoToGpuTable:
    def test_h100(self):
        gpu = GpuInfo(name="NVIDIA H100", vram_gb=80.0, backend="nvidia")
        assert gpu_info_to_gpu_table(gpu) == "h100"

    def test_t4(self):
        gpu = GpuInfo(name="Tesla T4", vram_gb=16.0, backend="nvidia")
        assert gpu_info_to_gpu_table(gpu) == "t4"

    def test_a100(self):
        gpu = GpuInfo(name="NVIDIA A100-SXM4-80GB", vram_gb=80.0, backend="nvidia")
        assert gpu_info_to_gpu_table(gpu) == "a100_80"

    def test_rtx_4090(self):
        gpu = GpuInfo(name="NVIDIA GeForce RTX 4090", vram_gb=24.0, backend="nvidia")
        assert gpu_info_to_gpu_table(gpu) == "rtx_4090"

    def test_rtx_3090(self):
        gpu = GpuInfo(name="NVIDIA GeForce RTX 3090", vram_gb=24.0, backend="nvidia")
        assert gpu_info_to_gpu_table(gpu) == "rtx_3090"

    def test_l40s(self):
        gpu = GpuInfo(name="NVIDIA L40S", vram_gb=48.0, backend="nvidia")
        assert gpu_info_to_gpu_table(gpu) == "l40s"

    def test_l4(self):
        gpu = GpuInfo(name="NVIDIA L4", vram_gb=24.0, backend="nvidia")
        assert gpu_info_to_gpu_table(gpu) == "l4"

    def test_a10(self):
        gpu = GpuInfo(name="NVIDIA A10", vram_gb=24.0, backend="nvidia")
        assert gpu_info_to_gpu_table(gpu) == "a10"

    def test_unknown_nvidia_fallback(self):
        gpu = GpuInfo(name="NVIDIA GeForce GTX 1080", vram_gb=8.0, backend="nvidia")
        assert gpu_info_to_gpu_table(gpu) is None

    def test_metal_returns_none(self):
        gpu = GpuInfo(name="Apple M3 Max", vram_gb=48.0, backend="metal")
        assert gpu_info_to_gpu_table(gpu) is None

    def test_rocm_returns_none(self):
        gpu = GpuInfo(name="AMD GPU 0", vram_gb=24.0, backend="rocm")
        assert gpu_info_to_gpu_table(gpu) is None

    def test_empty_name(self):
        gpu = GpuInfo(name="", vram_gb=0.0, backend="unknown")
        assert gpu_info_to_gpu_table(gpu) is None

    def test_name_to_table_key_internal(self):
        assert _gpu_name_to_table_key("NVIDIA H100") == "h100"
        assert _gpu_name_to_table_key("Tesla T4") == "t4"
        assert _gpu_name_to_table_key("NVIDIA GeForce RTX 3090") == "rtx_3090"
        assert _gpu_name_to_table_key("Apple M2 Ultra") is None
        assert _gpu_name_to_table_key("") is None


class TestCanRunModel:
    def test_unknown_model(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="NVIDIA H100", vram_gb=80.0, backend="nvidia")],
            total_ram_gb=256.0,
            disk_free_gb=500.0,
            cpu_cores=32,
        )
        r = can_run_model(inv, "nonexistent-model")
        assert r.can_run is False
        assert "unknown model" in r.reason

    def test_no_gpus(self):
        inv = HardwareInventory(total_ram_gb=64.0, disk_free_gb=200.0, cpu_cores=8)
        r = can_run_model(inv, "llama-3.1-8b")
        assert r.can_run is False
        assert "no gpu" in r.reason.lower()

    def test_llama_8b_on_h100_fits(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="NVIDIA H100", vram_gb=80.0, backend="nvidia")],
            total_ram_gb=256.0,
            disk_free_gb=500.0,
            cpu_cores=32,
        )
        r = can_run_model(inv, "llama-3.1-8b")
        assert r.can_run is True
        assert r.backend == "nvidia"
        assert r.quant_method in ("fp16", "q8_0", "q6_k", "q5_k_m", "q4_k_m")

    def test_mistral_7b_on_t4_fits(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="Tesla T4", vram_gb=16.0, backend="nvidia")],
            total_ram_gb=64.0,
        )
        r = can_run_model(inv, "mistral-7b")
        assert r.can_run is True
        assert r.estimated_vram_gb <= 8.0

    def test_llama_70b_on_t4_fails(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="Tesla T4", vram_gb=16.0, backend="nvidia")],
            total_ram_gb=64.0,
        )
        r = can_run_model(inv, "llama-3.1-70b")
        assert r.can_run is False

    def test_deepseek_v3_on_8x_h200(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="NVIDIA H200", vram_gb=141.0, backend="nvidia") for _ in range(8)],
            total_ram_gb=2048.0,
        )
        r = can_run_model(inv, "deepseek-v3")
        assert r.can_run is True
        assert r.quant_method in ("q4_k_m", "q5_k_m", "q6_k", "q8_0")

    def test_phi3_mini_on_rtx_4090(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="NVIDIA GeForce RTX 4090", vram_gb=24.0, backend="nvidia")],
        )
        r = can_run_model(inv, "phi-3-mini")
        assert r.can_run is True

    def test_gemma_2b_on_metal(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="Apple M2", vram_gb=24.0, backend="metal")],
            total_ram_gb=36.0,
        )
        r = can_run_model(inv, "gemma-2-2b")
        assert r.can_run is True
        assert r.backend == "metal"

    def test_llama_405b_on_single_h100_fails(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="NVIDIA H100", vram_gb=80.0, backend="nvidia")],
        )
        r = can_run_model(inv, "llama-3.1-405b")
        assert r.can_run is False

    def test_quant_ladder_prefers_higher_quality(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="NVIDIA H100", vram_gb=80.0, backend="nvidia")],
        )
        r = can_run_model(inv, "qwen-2.5-7b")
        assert r.can_run is True
        assert r.quant_method == "fp16"

    def test_case_insensitive_model_name(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="Tesla T4", vram_gb=16.0, backend="nvidia")],
        )
        r = can_run_model(inv, "MISTRAL-7B")
        assert r.can_run is True

    def test_stripped_model_name(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="Tesla T4", vram_gb=16.0, backend="nvidia")],
        )
        r = can_run_model(inv, "  mistral-7b  ")
        assert r.can_run is True

    def test_multi_gpu_aggregates_vram(self):
        inv = HardwareInventory(
            gpus=[
                GpuInfo(name="NVIDIA H100", vram_gb=80.0, backend="nvidia"),
                GpuInfo(name="NVIDIA H100", vram_gb=80.0, backend="nvidia"),
            ],
            total_ram_gb=512.0,
        )
        r = can_run_model(inv, "llama-3.1-70b")
        assert r.can_run is True


class TestExtractModelParams:
    def test_standard_b_suffix(self):
        assert _extract_model_params("llama-3.1-8b") == {"params_b": 8.0}
        assert _extract_model_params("mistral-7b") == {"params_b": 7.0}
        assert _extract_model_params("llama-3.1-70b") == {"params_b": 70.0}
        assert _extract_model_params("qwen-2.5-72b") == {"params_b": 72.0}

    def test_standard_b_suffix_variants(self):
        assert _extract_model_params("qwen-2.5-7b") == {"params_b": 7.0}
        assert _extract_model_params("gemma-2-2b") == {"params_b": 2.0}
        assert _extract_model_params("gemma-2-9b") == {"params_b": 9.0}
        assert _extract_model_params("codestral-22b") == {"params_b": 22.0}
        assert _extract_model_params("llama-3.1-405b") == {"params_b": 405.0}

    def test_decimal_param_count(self):
        assert _extract_model_params("phi-3-mini") == {"params_b": 3.8}
        assert _extract_model_params("phi-3-medium") == {"params_b": 14.0}

    def test_moe_pattern_mixtral(self):
        result = _extract_model_params("mixtral-8x7b")
        assert result is not None
        assert result["params_b"] == 47.0
        assert result["is_moe"] is True
        assert result["active_params_b"] == 13.0

    def test_moe_pattern_deepseek(self):
        for name in ("deepseek-v3", "deepseek-r1"):
            result = _extract_model_params(name)
            assert result is not None
            assert result["params_b"] == 671.0
            assert result["is_moe"] is True
            assert result["active_params_b"] == 37.0

    def test_moe_detection_from_name(self):
        result = _extract_model_params("some-moe-model-8b")
        assert result is not None
        assert result["params_b"] == 8.0
        assert result["is_moe"] is True

    def test_mixtral_anywhere_detected_as_moe(self):
        result = _extract_model_params("mixtral-8x22b")
        assert result is not None
        assert result["is_moe"] is True

    def test_case_insensitive(self):
        assert _extract_model_params("MISTRAL-7B") == {"params_b": 7.0}
        assert _extract_model_params("Llama-3.1-8B") == {"params_b": 8.0}

    def test_stripped_whitespace(self):
        assert _extract_model_params("  mistral-7b  ") == {"params_b": 7.0}

    def test_unknown_model_returns_none(self):
        assert _extract_model_params("nonexistent-model-xyz") is None

    def test_no_param_indicator_returns_none(self):
        assert _extract_model_params("some-model-name") is None

    def test_nx_m_b_pattern_extraction(self):
        result = _extract_model_params("dbrx-16x12b")
        assert result is not None
        assert result["is_moe"] is True
        assert result["params_b"] == 132.0  # 16 * 12 * (1 - overlap_ratio) ≈ 132

    def test_nx_m_b_fallback_overlap(self):
        result = _extract_model_params("experts-4x7b")
        assert result is not None
        assert result["is_moe"] is True
        assert result["params_b"] == 21.0  # 4 * 7 * 0.75


class TestUnifiedProbe:
    def test_runs_all_probes(self):
        survey = HardwareSurvey()
        gpu = GpuInfo(name="NVIDIA H100", vram_gb=80.0, backend="nvidia")
        with (
            mock.patch.object(survey, "probe_gpu_nvidia", return_value=[gpu]),
            mock.patch.object(survey, "probe_ram", return_value=256.0),
            mock.patch.object(survey, "probe_disk", return_value=500.0),
            mock.patch.object(survey, "probe_cpu", return_value=32),
        ):
            inv = unified_probe(survey=survey)
        assert inv.gpu_count == 1
        assert inv.gpus[0].backend == "nvidia"
        assert inv.total_ram_gb == 256.0
        assert inv.disk_free_gb == 500.0
        assert inv.cpu_cores == 32

    def test_falls_back_to_metal(self):
        survey = HardwareSurvey()
        gpu = GpuInfo(name="Apple M3 Max", vram_gb=48.0, backend="metal")
        with (
            mock.patch.object(survey, "probe_gpu_nvidia", return_value=[]),
            mock.patch.object(survey, "probe_gpu_metal", return_value=[gpu]),
            mock.patch.object(survey, "probe_ram", return_value=128.0),
            mock.patch.object(survey, "probe_disk", return_value=1000.0),
            mock.patch.object(survey, "probe_cpu", return_value=20),
        ):
            inv = unified_probe(survey=survey)
        assert inv.gpus[0].backend == "metal"

    def test_falls_back_to_rocm(self):
        survey = HardwareSurvey()
        gpu = GpuInfo(name="AMD GPU 0", vram_gb=24.0, backend="rocm")
        with (
            mock.patch.object(survey, "probe_gpu_nvidia", return_value=[]),
            mock.patch.object(survey, "probe_gpu_metal", return_value=[]),
            mock.patch.object(survey, "probe_gpu_rocm", return_value=[gpu]),
            mock.patch.object(survey, "probe_ram", return_value=64.0),
            mock.patch.object(survey, "probe_disk", return_value=500.0),
            mock.patch.object(survey, "probe_cpu", return_value=16),
        ):
            inv = unified_probe(survey=survey)
        assert inv.gpus[0].backend == "rocm"

    def test_empty_when_no_gpu(self):
        survey = HardwareSurvey()
        with (
            mock.patch.object(survey, "probe_gpu_nvidia", return_value=[]),
            mock.patch.object(survey, "probe_gpu_metal", return_value=[]),
            mock.patch.object(survey, "probe_gpu_rocm", return_value=[]),
            mock.patch.object(survey, "probe_ram", return_value=16.0),
            mock.patch.object(survey, "probe_disk", return_value=100.0),
            mock.patch.object(survey, "probe_cpu", return_value=4),
        ):
            inv = unified_probe(survey=survey)
        assert inv.gpu_count == 0
        assert inv.total_ram_gb == 16.0

    def test_default_survey_creates_instance(self):
        with mock.patch("general_ludd.hardware.model_fit.HardwareSurvey") as mock_cls:
            mock_survey = mock.MagicMock()
            mock_survey.probe_gpu_nvidia.return_value = []
            mock_survey.probe_gpu_metal.return_value = []
            mock_survey.probe_gpu_rocm.return_value = []
            mock_survey.probe_ram.return_value = 32.0
            mock_survey.probe_disk.return_value = 200.0
            mock_survey.probe_cpu.return_value = 8
            mock_cls.return_value = mock_survey
            inv = unified_probe()
        assert inv.total_ram_gb == 32.0
