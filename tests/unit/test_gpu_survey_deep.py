"""Deep tests for GPU detection, VRAM estimation, CUDA version parsing, GPU tier classification, and CPU fallback."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.hardware.survey import (
    GpuInfo,
    HardwareInventory,
    HardwareSurvey,
)

# ---------------------------------------------------------------------------
# Helpers for expansion logic (tier, CUDA version, VRAM estimation)
# ---------------------------------------------------------------------------


def _gpu_tier(vram_gb: float, backend: str = "") -> str:
    """Classify a GPU into a tier based on VRAM."""
    if vram_gb < 2.0:
        return "entry"
    if vram_gb < 8.0:
        return "low"
    if vram_gb < 24.0:
        return "mid"
    if vram_gb < 48.0:
        return "high"
    return "datacenter"


def _guess_vram_from_unified(total_ram_bytes: int, ratio: float = 0.67) -> float:
    """Estimate GPU VRAM from Apple Unified Memory total."""
    return round(total_ram_bytes / (1024**3) * ratio, 2)


def _parse_cuda_driver(output: str) -> str | None:
    """Extract CUDA driver version from nvidia-smi output."""
    for line in output.splitlines():
        if "CUDA Version:" in line:
            rest = line.split("CUDA Version:")[-1].strip()
            if not rest:
                return None
            return rest.split()[0] if rest.split() else rest
    return None


# ---------------------------------------------------------------------------
# Metal detection (Apple Silicon + eGPU)
# ---------------------------------------------------------------------------


class TestMetalDetection:
    def test_apple_silicon_m3_max(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Chipset Model: Apple M3 Max\n  Vendor: Apple\n  VRAM (Total): 48 GB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 1
        assert gpus[0].name == "Apple M3 Max"
        assert gpus[0].vram_gb == 48.0
        assert gpus[0].backend == "metal"

    def test_apple_silicon_m1_pro(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Chipset Model: Apple M1 Pro\n  Vendor: Apple\n  VRAM (Total): 16 GB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 1
        assert gpus[0].vram_gb == 16.0
        assert gpus[0].backend == "metal"

    def test_apple_silicon_m4(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Chipset Model: Apple M4\n  Vendor: Apple\n  VRAM (Total): 24 GB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 1
        assert gpus[0].name == "Apple M4"
        assert gpus[0].vram_gb == 24.0

    def test_amd_egpu_via_vendor(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Vendor: AMD\n  VRAM (Total): 8 GB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 1
        assert gpus[0].name == "AMD"
        assert gpus[0].vram_gb == 8.0
        assert gpus[0].backend == "metal"

    def test_intel_egpu_via_vendor(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Vendor: Intel\n  VRAM (Total): 1536 MB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 1
        assert gpus[0].name == "Intel"
        assert gpus[0].vram_gb == 1.5

    def test_unified_memory_fallback_metal_support(self):
        survey = HardwareSurvey()
        survey._probe_ram_bytes = MagicMock(return_value=36 * 1024**3)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Chipset Model: Apple M2\n  Metal Support: Metal 3\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 1
        assert gpus[0].name == "Apple M2"
        assert pytest.approx(gpus[0].vram_gb, rel=0.02) == 24.12

    def test_multi_gpu_system_profiler(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = (
            "Chipset Model: Apple M3 Max\n"
            "  Vendor: Apple\n"
            "  VRAM (Total): 36 GB\n"
            "Chipset Model: AMD Radeon Pro W6900X\n"
            "  VRAM (Total): 32 GB\n"
        )
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 2
        assert gpus[0].name == "Apple M3 Max"
        assert gpus[0].vram_gb == 36.0
        assert gpus[0].index == 0
        assert gpus[1].name == "AMD Radeon Pro W6900X"
        assert gpus[1].vram_gb == 32.0
        assert gpus[1].index == 1
        assert all(g.backend == "metal" for g in gpus)

    def test_system_profiler_dynamic_max_vram(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Chipset Model: Apple M3\n  VRAM (Dynamic, Max): 27 GB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 1
        assert gpus[0].vram_gb == 27.0


# ---------------------------------------------------------------------------
# CUDA version parsing
# ---------------------------------------------------------------------------


class TestCudaVersionParsing:
    def test_parse_cuda_12_4(self):
        output = "| NVIDIA-SMI 550.54.15              Driver Version: 550.54.15      CUDA Version: 12.4     |\n"
        assert _parse_cuda_driver(output) == "12.4"

    def test_parse_cuda_11_8(self):
        output = "| NVIDIA-SMI 525.125.06    Driver Version: 525.125.06    CUDA Version: 11.8     |\n"
        assert _parse_cuda_driver(output) == "11.8"

    def test_parse_cuda_not_present(self):
        output = "No CUDA drivers found\n"
        assert _parse_cuda_driver(output) is None

    def test_parse_cuda_version_empty_output(self):
        assert _parse_cuda_driver("") is None

    def test_parse_cuda_version_malformed(self):
        output = "CUDA Version: \n"
        assert _parse_cuda_driver(output) is None


# ---------------------------------------------------------------------------
# VRAM estimation
# ---------------------------------------------------------------------------


class TestVRAMEstimation:
    def test_unified_memory_guess_36gb(self):
        vram = _guess_vram_from_unified(36 * 1024**3)
        assert pytest.approx(vram, rel=0.01) == 24.12

    def test_unified_memory_guess_18gb(self):
        vram = _guess_vram_from_unified(18 * 1024**3)
        assert pytest.approx(vram, rel=0.01) == 12.06

    def test_unified_memory_guess_96gb(self):
        vram = _guess_vram_from_unified(96 * 1024**3)
        assert pytest.approx(vram, rel=0.01) == 64.32

    def test_unified_memory_guess_custom_ratio(self):
        vram = _guess_vram_from_unified(32 * 1024**3, ratio=0.75)
        assert vram == 24.0

    def test_nvidia_mb_to_gb_conversion(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "RTX 4060, 8188\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_nvidia()
        assert len(gpus) == 1
        assert pytest.approx(gpus[0].vram_gb, rel=0.01) == 7.996

    def test_rocm_mb_to_gb_conversion(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "VRAM: 16384\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_rocm()
        assert len(gpus) == 1
        assert gpus[0].vram_gb == 16.0

    def test_min_vram_filter_zero_gpu(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Weak GPU, 128\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_nvidia()
        assert len(gpus) == 0

    def test_min_vram_filter_below_threshold(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Tiny GPU, 200\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_nvidia()
        assert len(gpus) == 0


# ---------------------------------------------------------------------------
# GPU tier classification
# ---------------------------------------------------------------------------


class TestGpuTierClassification:
    def test_entry_tier(self):
        assert _gpu_tier(0.5) == "entry"
        assert _gpu_tier(1.0) == "entry"

    def test_low_tier(self):
        assert _gpu_tier(2.0) == "low"
        assert _gpu_tier(4.0, backend="nvidia") == "low"

    def test_mid_tier(self):
        assert _gpu_tier(8.0) == "mid"
        assert _gpu_tier(16.0) == "mid"
        assert _gpu_tier(23.0) == "mid"

    def test_high_tier(self):
        assert _gpu_tier(24.0) == "high"
        assert _gpu_tier(40.0) == "high"

    def test_datacenter_tier(self):
        assert _gpu_tier(48.0) == "datacenter"
        assert _gpu_tier(80.0) == "datacenter"
        assert _gpu_tier(192.0) == "datacenter"

    def test_inventory_with_mixed_tiers(self):
        gpus = [
            GpuInfo(name="RTX 4090", vram_gb=24.0, backend="nvidia"),
            GpuInfo(name="Apple M2", vram_gb=8.0, backend="metal"),
            GpuInfo(name="Tesla T4", vram_gb=0.5, backend="nvidia"),
        ]
        tiers = [_gpu_tier(g.vram_gb, g.backend) for g in gpus]
        assert tiers == ["high", "mid", "entry"]

    def test_cpu_fallback_tier(self):
        inv = HardwareInventory(cpu_cores=16, total_ram_gb=64.0)
        assert inv.gpu_count == 0
        assert inv.total_vram_gb == 0.0
        assert inv.cpu_cores == 16
        assert inv.total_ram_gb == 64.0


# ---------------------------------------------------------------------------
# Fallback to CPU
# ---------------------------------------------------------------------------


class TestCpuFallback:
    def test_all_backends_fail_returns_empty(self):
        survey = HardwareSurvey()
        with (
            patch.object(survey, "probe_gpu_nvidia", return_value=[]),
            patch.object(survey, "probe_gpu_metal", return_value=[]),
            patch.object(survey, "probe_gpu_rocm", return_value=[]),
        ):
            assert survey.probe_gpus() == []

    def test_survey_falls_back_to_cpu(self):
        survey = HardwareSurvey()
        with (
            patch.object(survey, "probe_gpus", return_value=[]),
            patch.object(survey, "probe_ram", return_value=64.0),
            patch.object(survey, "probe_disk", return_value=500.0),
            patch.object(survey, "probe_cpu", return_value=32),
        ):
            inv = survey.survey()
        assert inv.gpu_count == 0
        assert inv.total_vram_gb == 0.0
        assert inv.total_ram_gb == 64.0
        assert inv.cpu_cores == 32
        assert isinstance(inv, HardwareInventory)

    def test_metal_fails_nvidia_absent_rocm_absent(self):
        survey = HardwareSurvey()
        with (
            patch.object(survey, "probe_gpu_nvidia", return_value=[]),
            patch.object(survey, "probe_gpu_metal", return_value=[]),
            patch.object(survey, "probe_gpu_rocm", return_value=[]),
        ):
            gpus = survey.probe_gpus()
        assert gpus == []


# ---------------------------------------------------------------------------
# Edge cases and invariants
# ---------------------------------------------------------------------------


class TestGpuSurveyInvariants:
    def test_gpu_info_is_frozen(self):
        gpu = GpuInfo(name="T4", vram_gb=16.0, index=0, backend="nvidia")
        with pytest.raises(AttributeError):
            gpu.name = "changed"

    def test_backend_is_set_on_all_probes(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Tesla T4, 15360\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_nvidia()
        assert all(g.backend for g in gpus)

    def test_nvidia_smi_extra_whitespace(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "  RTX 3090 ,  24576  \n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_nvidia()
        assert len(gpus) == 1
        assert gpus[0].name == "RTX 3090"
        assert gpus[0].vram_gb == 24.0

    def test_nvidia_smi_single_comma_line(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "NVIDIA RTX A6000,\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_nvidia()
        assert gpus == []

    def test_metal_no_chipset_no_vendor_no_vram(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Display Type: LCD\n  Resolution: 2560x1600\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert gpus == []

    def test_hardware_inventory_to_dict_roundtrip(self):
        gpus = [
            GpuInfo(name="H100", vram_gb=80.0, index=0, backend="nvidia"),
            GpuInfo(name="H100", vram_gb=80.0, index=1, backend="nvidia"),
        ]
        inv = HardwareInventory(gpus=gpus, total_ram_gb=512.0, disk_free_gb=2000.0, cpu_cores=128)
        d = inv.to_dict()
        assert len(d["gpus"]) == 2
        assert d["total_ram_gb"] == 512.0
        assert d["cpu_cores"] == 128
        assert d["gpus"][0]["vram_gb"] == 80.0
        assert d["gpus"][1]["vram_gb"] == 80.0

    def test_single_probe_metal_timeout(self):
        survey = HardwareSurvey()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("system_profiler", 15)):
            survey._probe_ram_bytes = MagicMock(return_value=0)
            assert survey.probe_gpu_metal() == []

    def test_single_probe_rocm_invalid_vram(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "VRAM: N/A\n"
        with patch("subprocess.run", return_value=mock):
            assert survey.probe_gpu_rocm() == []

    def test_single_probe_metal_skips_vram_mb_below_threshold(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Chipset Model: Weak iGPU\n  VRAM (Total): 128 MB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert gpus == []

    def test_gpu_count_property_empty(self):
        inv = HardwareInventory()
        assert inv.gpu_count == 0
        assert inv.total_vram_gb == 0.0
