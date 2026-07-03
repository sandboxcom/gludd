"""Tests for :mod:`general_ludd.infra.gpu_info_adapter`."""

from __future__ import annotations

from general_ludd.infra.gpu_info_adapter import (
    _CC_BY_ARCH,
    gpu_info_from_gpu_type,
)
from general_ludd.infra.model_deploy_check import MisconfigDetector


def test_known_gpu_type_h100_maps_expected_fields() -> None:
    info = gpu_info_from_gpu_type("h100", 1)
    assert info["vram_gb"] == 80.0
    assert info["gpu_count"] == 1
    assert info["arch"] == "hopper"
    assert info["supports_fp8"] is True
    assert info["compute_capability"] == _CC_BY_ARCH["hopper"] == 9.0


def test_multi_gpu_total_vram_and_nvlink() -> None:
    info = gpu_info_from_gpu_type("h100", 8)
    # total_vram_gb aggregates across the GPUs.
    assert info["vram_gb"] == 640.0
    assert info["gpu_count"] == 8
    # >=2 NVLinked Hopper GPUs -> NVLink on.
    assert info["has_nvlink"] is True


def test_unknown_gpu_type_returns_empty_dict_no_raise() -> None:
    assert gpu_info_from_gpu_type("totally-bogus-gpu") == {}
    # empty gpu_count / instance edge cases also fail-soft.
    assert gpu_info_from_gpu_type("", 4) == {}


def test_fp8_rule_e_does_not_fire_on_fp8_capable_arch() -> None:
    gpu_info = gpu_info_from_gpu_type("h100", 1)
    deployment = {
        "engine": "vllm",
        "quantization": "fp8",
        "gpu_memory_utilization": 0.90,
    }
    findings = MisconfigDetector().check(deployment, gpu_info)
    assert not any(f.rule_id == "e" for f in findings), (
        "rule e (fp8 unsupported) must NOT fire on an FP8-capable arch (hopper)"
    )


def test_fp8_rule_e_fires_on_non_fp8_arch() -> None:
    # a100_80 is Ampere -> no native FP8; rule e SHOULD fire.
    gpu_info = gpu_info_from_gpu_type("a100_80", 1)
    deployment = {
        "engine": "vllm",
        "quantization": "fp8",
        "gpu_memory_utilization": 0.90,
    }
    findings = MisconfigDetector().check(deployment, gpu_info)
    e_findings = [f for f in findings if f.rule_id == "e"]
    assert len(e_findings) == 1
    assert e_findings[0].severity == "critical"
