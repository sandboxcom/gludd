"""Structural tests for infra/gpu_info_adapter.py — GPU info adapter."""

from __future__ import annotations

from unittest import mock

from general_ludd.infra.gpu_info_adapter import (
    _CC_BY_ARCH,
    gpu_info_from_gpu_type,
)


class TestGpuInfoFromGpuType:
    def test_unknown_gpu_returns_empty(self):
        result = gpu_info_from_gpu_type("unknown-gpu-type")
        assert result == {}

    def test_valid_gpu_with_mocked_profile(self):
        mock_hp = mock.MagicMock()
        mock_hp.total_vram_gb = 80.0
        mock_hp.gpu_count = 8
        mock_hp.arch = "hopper"
        mock_hp.supports_fp8 = True
        mock_hp.has_nvlink = True

        with mock.patch(
            "general_ludd.infra.gpu_info_adapter.deployment_optimizer.hardware_profile_for",
            return_value=mock_hp,
        ):
            result = gpu_info_from_gpu_type("H100", gpu_count=8)
            assert result["vram_gb"] == 80.0
            assert result["gpu_count"] == 8
            assert result["arch"] == "hopper"
            assert result["supports_fp8"] is True
            assert result["has_nvlink"] is True
            assert result["compute_capability"] == 9.0

    def test_hardware_profile_error_returns_empty(self):
        with mock.patch(
            "general_ludd.infra.gpu_info_adapter.deployment_optimizer.hardware_profile_for",
            side_effect=ValueError("unknown"),
        ):
            result = gpu_info_from_gpu_type("bogus")
            assert result == {}

    def test_compute_capability_ampere(self):
        mock_hp = mock.MagicMock()
        mock_hp.total_vram_gb = 40.0
        mock_hp.gpu_count = 1
        mock_hp.arch = "ampere"
        mock_hp.supports_fp8 = False
        mock_hp.has_nvlink = False

        with mock.patch(
            "general_ludd.infra.gpu_info_adapter.deployment_optimizer.hardware_profile_for",
            return_value=mock_hp,
        ):
            result = gpu_info_from_gpu_type("A100")
            assert result["compute_capability"] == 8.0

    def test_compute_capability_turing(self):
        mock_hp = mock.MagicMock()
        mock_hp.total_vram_gb = 16.0
        mock_hp.gpu_count = 1
        mock_hp.arch = "turing"
        mock_hp.supports_fp8 = False
        mock_hp.has_nvlink = False

        with mock.patch(
            "general_ludd.infra.gpu_info_adapter.deployment_optimizer.hardware_profile_for",
            return_value=mock_hp,
        ):
            result = gpu_info_from_gpu_type("T4")
            assert result["compute_capability"] == 7.5

    def test_compute_capability_ada(self):
        mock_hp = mock.MagicMock()
        mock_hp.total_vram_gb = 48.0
        mock_hp.gpu_count = 1
        mock_hp.arch = "ada"
        mock_hp.supports_fp8 = False
        mock_hp.has_nvlink = False

        with mock.patch(
            "general_ludd.infra.gpu_info_adapter.deployment_optimizer.hardware_profile_for",
            return_value=mock_hp,
        ):
            result = gpu_info_from_gpu_type("L40S")
            assert result["compute_capability"] == 8.9

    def test_compute_capability_blackwell(self):
        mock_hp = mock.MagicMock()
        mock_hp.total_vram_gb = 192.0
        mock_hp.gpu_count = 1
        mock_hp.arch = "blackwell"
        mock_hp.supports_fp8 = True
        mock_hp.has_nvlink = True

        with mock.patch(
            "general_ludd.infra.gpu_info_adapter.deployment_optimizer.hardware_profile_for",
            return_value=mock_hp,
        ):
            result = gpu_info_from_gpu_type("B200")
            assert result["compute_capability"] == 10.0

    def test_unknown_arch_no_compute_capability(self):
        mock_hp = mock.MagicMock()
        mock_hp.total_vram_gb = 24.0
        mock_hp.gpu_count = 1
        mock_hp.arch = "pascal"
        mock_hp.supports_fp8 = False
        mock_hp.has_nvlink = False

        with mock.patch(
            "general_ludd.infra.gpu_info_adapter.deployment_optimizer.hardware_profile_for",
            return_value=mock_hp,
        ):
            result = gpu_info_from_gpu_type("P100")
            assert result["compute_capability"] is None

    def test_with_instance_param(self):
        mock_hp = mock.MagicMock()
        mock_hp.total_vram_gb = 80.0
        mock_hp.gpu_count = 4
        mock_hp.arch = "hopper"
        mock_hp.supports_fp8 = True
        mock_hp.has_nvlink = True

        with mock.patch(
            "general_ludd.infra.gpu_info_adapter.deployment_optimizer.hardware_profile_for",
            return_value=mock_hp,
        ) as mock_hpf:
            result = gpu_info_from_gpu_type("H100", gpu_count=4, instance="g5.48xlarge")
            mock_hpf.assert_called_once_with("H100", 4, instance="g5.48xlarge")
            assert result["gpu_count"] == 4

    def test_gpu_count_defaults_to_one(self):
        mock_hp = mock.MagicMock()
        mock_hp.total_vram_gb = 80.0
        mock_hp.gpu_count = 1
        mock_hp.arch = "hopper"
        mock_hp.supports_fp8 = True
        mock_hp.has_nvlink = True

        with mock.patch(
            "general_ludd.infra.gpu_info_adapter.deployment_optimizer.hardware_profile_for",
            return_value=mock_hp,
        ) as mock_hpf:
            gpu_info_from_gpu_type("H100")
            args, _kwargs = mock_hpf.call_args
            assert args[1] == 1


class TestComputeCapabilityTable:
    def test_all_arches_have_cc(self):
        expected = {"turing", "ampere", "ada", "hopper", "blackwell"}
        assert set(_CC_BY_ARCH.keys()) == expected

    def test_all_values_positive(self):
        for cc in _CC_BY_ARCH.values():
            assert cc > 0
            assert isinstance(cc, float)
