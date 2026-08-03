from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.hardware.survey import (
    GpuInfo,
    HardwareInventory,
    HardwareSurvey,
)


class TestGpuInfo:
    def test_constructor_defaults(self):
        gpu = GpuInfo(name="RTX 4090", vram_gb=24.0)
        assert gpu.name == "RTX 4090"
        assert gpu.vram_gb == 24.0
        assert gpu.index == 0
        assert gpu.backend == ""

    def test_frozen(self):
        gpu = GpuInfo(name="T4", vram_gb=16.0, index=0, backend="nvidia")
        with pytest.raises(TypeError):
            object.__setattr__(gpu, "name", "changed")
        assert gpu.name == "T4"

    def test_equality(self):
        a = GpuInfo(name="A100", vram_gb=80.0, index=0, backend="nvidia")
        b = GpuInfo(name="A100", vram_gb=80.0, index=0, backend="nvidia")
        assert a == b

    def test_distinct(self):
        a = GpuInfo(name="A100", vram_gb=80.0, index=0, backend="nvidia")
        b = GpuInfo(name="A100", vram_gb=80.0, index=1, backend="nvidia")
        assert a != b


class TestHardwareInventory:
    def test_empty(self):
        inv = HardwareInventory()
        assert inv.gpu_count == 0
        assert inv.total_vram_gb == 0.0
        assert inv.total_ram_gb == 0.0
        assert inv.disk_free_gb == 0.0
        assert inv.cpu_cores == 0

    def test_with_gpus(self):
        gpus = [
            GpuInfo(name="H100", vram_gb=80.0, index=0, backend="nvidia"),
            GpuInfo(name="H100", vram_gb=80.0, index=1, backend="nvidia"),
        ]
        inv = HardwareInventory(gpus=gpus, total_ram_gb=256.0, disk_free_gb=500.0, cpu_cores=64)
        assert inv.gpu_count == 2
        assert inv.total_vram_gb == 160.0

    def test_total_vram_single_gpu(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="L4", vram_gb=24.0, backend="nvidia")],
        )
        assert inv.total_vram_gb == 24.0

    def test_total_vram_fractional(self):
        inv = HardwareInventory(
            gpus=[GpuInfo(name="M3", vram_gb=17.83, backend="metal")],
        )
        assert inv.total_vram_gb == 17.83

    def test_to_dict(self):
        gpus = [GpuInfo(name="T4", vram_gb=16.0, index=0, backend="nvidia")]
        inv = HardwareInventory(gpus=gpus, total_ram_gb=128.0, disk_free_gb=200.0, cpu_cores=16)
        d = inv.to_dict()
        assert d["total_ram_gb"] == 128.0
        assert d["disk_free_gb"] == 200.0
        assert d["cpu_cores"] == 16
        assert len(d["gpus"]) == 1
        assert d["gpus"][0]["name"] == "T4"

    def test_to_dict_no_gpus(self):
        inv = HardwareInventory(cpu_cores=8, total_ram_gb=32.0, disk_free_gb=100.0)
        d = inv.to_dict()
        assert d["gpus"] == []


class TestHardwareSurveyNvidia:
    def test_nvidia_smi_not_found(self):
        survey = HardwareSurvey()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert survey.probe_gpu_nvidia() == []

    def test_nvidia_smi_timeout(self):
        survey = HardwareSurvey()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 15)):
            assert survey.probe_gpu_nvidia() == []

    def test_nvidia_smi_error_return(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 1
        with patch("subprocess.run", return_value=mock):
            assert survey.probe_gpu_nvidia() == []

    def test_nvidia_smi_single_gpu(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Tesla T4, 15360 MiB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_nvidia()
        assert len(gpus) == 1
        assert gpus[0].name == "Tesla T4"
        assert gpus[0].vram_gb == 15.0
        assert gpus[0].backend == "nvidia"

    def test_nvidia_smi_multi_gpu(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "NVIDIA H100, 81559 MiB\nNVIDIA H100, 81559 MiB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_nvidia()
        assert len(gpus) == 2
        assert gpus[0].index == 0
        assert gpus[1].index == 1
        assert gpus[0].vram_gb == pytest.approx(79.65, rel=0.01)

    def test_nvidia_smi_filters_zero_vram(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Broken GPU, 0 MiB\n"
        with patch("subprocess.run", return_value=mock):
            assert survey.probe_gpu_nvidia() == []

    def test_nvidia_smi_empty_lines(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "A100, 40960 MiB\n\n\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_nvidia()
        assert len(gpus) == 1
        assert gpus[0].vram_gb == 40.0

    def test_nvidia_smi_invalid_vram(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Some GPU, UNKNOWN\n"
        with patch("subprocess.run", return_value=mock):
            assert survey.probe_gpu_nvidia() == []


class TestHardwareSurveyMetal:
    def test_system_profiler_not_found(self):
        survey = HardwareSurvey()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            survey._probe_ram_bytes = MagicMock(return_value=0)
            assert survey.probe_gpu_metal() == []

    def test_system_profiler_apple_silicon(self):
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

    def test_system_profiler_amd_egpu(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Vendor: AMD\n  VRAM (Total): 16 GB\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 1
        assert gpus[0].name == "AMD"
        assert gpus[0].vram_gb == 16.0

    def test_system_profiler_unified_memory_fallback(self):
        survey = HardwareSurvey()
        survey._probe_ram_bytes = MagicMock(return_value=36 * 1024**3)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Chipset Model: Apple M2\n  Metal Support: Metal 3\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 1
        assert gpus[0].name == "Apple M2"
        assert pytest.approx(gpus[0].vram_gb, rel=0.05) == 24.12

    def test_system_profiler_no_vram_line(self):
        survey = HardwareSurvey()
        survey._probe_ram_bytes = MagicMock(return_value=8 * 1024**3)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Vendor: NVIDIA\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_metal()
        assert len(gpus) == 0


class TestHardwareSurveyROCm:
    def test_rocm_smi_not_found(self):
        survey = HardwareSurvey()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert survey.probe_gpu_rocm() == []

    def test_rocm_smi_single_gpu(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "VRAM: 24576\n"
        with patch("subprocess.run", return_value=mock):
            gpus = survey.probe_gpu_rocm()
        assert len(gpus) == 1
        assert gpus[0].name == "AMD GPU 0"
        assert gpus[0].vram_gb == 24.0
        assert gpus[0].backend == "rocm"

    def test_rocm_smi_error_return(self):
        survey = HardwareSurvey()
        mock = MagicMock()
        mock.returncode = 1
        with patch("subprocess.run", return_value=mock):
            assert survey.probe_gpu_rocm() == []


class TestHardwareSurveyProbeGpus:
    def test_nvidia_wins_over_others(self):
        survey = HardwareSurvey()
        nvidia_mock = MagicMock()
        nvidia_mock.returncode = 0
        nvidia_mock.stdout = "Tesla V100, 16384 MiB\n"

        def run_side_effect(args, **kwargs):
            cmd = args[0] if args else ""
            if "nvidia-smi" in str(cmd):
                return nvidia_mock
            raise FileNotFoundError

        with patch("subprocess.run", side_effect=run_side_effect):
            gpus = survey.probe_gpus()
        assert len(gpus) == 1
        assert gpus[0].backend == "nvidia"

    def test_metal_wins_when_no_nvidia(self):
        survey = HardwareSurvey()
        metal_mock = MagicMock()
        metal_mock.returncode = 0
        metal_mock.stdout = "Chipset Model: Apple M1 Pro\n  VRAM (Total): 16 GB\n"

        def run_side_effect(args, **kwargs):
            cmd = args[0] if args else ""
            if "nvidia-smi" in str(cmd):
                raise FileNotFoundError
            if "system_profiler" in str(cmd):
                return metal_mock
            raise FileNotFoundError

        with patch("subprocess.run", side_effect=run_side_effect):
            gpus = survey.probe_gpus()
        assert len(gpus) == 1
        assert gpus[0].backend == "metal"

    def test_empty_when_no_gpu(self):
        survey = HardwareSurvey()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert survey.probe_gpus() == []


class TestHardwareSurveySystem:
    def test_probe_ram_psutil(self):
        survey = HardwareSurvey()
        with patch("general_ludd.hardware.survey.psutil", create=True) as mock_psutil:
            mock_psutil.virtual_memory.return_value.total = 64 * 1024**3
            assert survey.probe_ram() == 64.0

    def test_probe_ram_sysctl_fallback(self):
        survey = HardwareSurvey()
        with patch("general_ludd.hardware.survey.psutil", create=True) as mock_psutil:
            mock_psutil.virtual_memory.side_effect = ImportError
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = "34359738368\n"
            with patch("subprocess.run", return_value=mock):
                assert survey.probe_ram() == 32.0

    def test_probe_ram_zero_on_failure(self):
        survey = HardwareSurvey()
        with patch("general_ludd.hardware.survey.psutil", create=True) as mock_psutil:
            mock_psutil.virtual_memory.side_effect = ImportError
            with patch("subprocess.run", side_effect=FileNotFoundError):
                assert survey.probe_ram() == 0.0

    def test_probe_disk(self):
        survey = HardwareSurvey()
        with patch("general_ludd.hardware.survey.shutil.disk_usage") as mock_disk:
            mock_disk.return_value = MagicMock(free=500 * 1024**3)
            assert survey.probe_disk() == 500.0

    def test_probe_disk_failure(self):
        survey = HardwareSurvey()
        with patch("general_ludd.hardware.survey.shutil.disk_usage", side_effect=OSError):
            assert survey.probe_disk() == 0.0

    def test_probe_cpu(self, monkeypatch):
        survey = HardwareSurvey()
        monkeypatch.setattr("general_ludd.hardware.survey.os.cpu_count", lambda: 16)
        assert survey.probe_cpu() == 16

    def test_probe_cpu_none_fallback(self, monkeypatch):
        survey = HardwareSurvey()
        monkeypatch.setattr("general_ludd.hardware.survey.os.cpu_count", lambda: None)
        assert survey.probe_cpu() == 1


class TestHardwareSurveyFull:
    def test_survey_no_gpu_healthy_system(self):
        survey = HardwareSurvey()
        with (
            patch.object(survey, "probe_gpus", return_value=[]),
            patch.object(survey, "probe_ram", return_value=32.0),
            patch.object(survey, "probe_disk", return_value=500.0),
            patch.object(survey, "probe_cpu", return_value=8),
        ):
            inv = survey.survey()
        assert isinstance(inv, HardwareInventory)
        assert inv.gpu_count == 0
        assert inv.total_vram_gb == 0.0
        assert inv.total_ram_gb == 32.0
        assert inv.disk_free_gb == 500.0
        assert inv.cpu_cores == 8

    def test_survey_with_gpus(self):
        survey = HardwareSurvey()
        gpus = [
            GpuInfo(name="RTX 4090", vram_gb=24.0, index=0, backend="nvidia"),
            GpuInfo(name="RTX 4090", vram_gb=24.0, index=1, backend="nvidia"),
        ]
        with (
            patch.object(survey, "probe_gpus", return_value=gpus),
            patch.object(survey, "probe_ram", return_value=128.0),
            patch.object(survey, "probe_disk", return_value=1000.0),
            patch.object(survey, "probe_cpu", return_value=32),
        ):
            inv = survey.survey()
        assert inv.gpu_count == 2
        assert inv.total_vram_gb == 48.0
        assert len(inv.gpus) == 2

    def test_survey_to_dict_includes_everything(self):
        survey = HardwareSurvey()
        gpus = [GpuInfo(name="L40S", vram_gb=48.0, backend="nvidia")]
        with (
            patch.object(survey, "probe_gpus", return_value=gpus),
            patch.object(survey, "probe_ram", return_value=256.0),
            patch.object(survey, "probe_disk", return_value=2000.0),
            patch.object(survey, "probe_cpu", return_value=64),
        ):
            inv = survey.survey()
        d = inv.to_dict()
        assert d["gpu_count"] is None or "gpus" in d
        assert d["total_ram_gb"] == 256.0
        assert d["disk_free_gb"] == 2000.0
        assert d["cpu_cores"] == 64
