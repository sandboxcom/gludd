"""Tests for shared unified-memory/discrete-VRAM model-fit policy."""

from __future__ import annotations

import pytest

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


def test_classifies_integrated_and_discrete_devices() -> None:
    assert classify_memory_kind("rocm", "AMD Radeon Graphics") == "unified"
    assert classify_memory_kind("rocm", "AMD Instinct MI250") == "discrete"
    assert classify_memory_kind("cuda", "NVIDIA RTX 4090") == "discrete"
    assert classify_memory_kind("unknown", "mystery") == "unknown"
    assert classify_memory_kind("cuda", "NVIDIA", is_integrated=True) == "unified"


def test_memory_budget_reserves_headroom_for_shared_or_discrete_memory() -> None:
    budget = memory_budget(10_000, kind="unified", reserve_fraction=0.20)
    assert budget.reserve_bytes == 2_000
    assert budget.usable_bytes == 8_000
    assert budget.as_dict()["kind"] == "unified"


def test_model_fit_accepts_borderline_model_at_budget() -> None:
    footprint = estimate_model_bytes(3.0, 4)
    result = assess_model_fit(int(footprint / 0.80), 3.0, 4, kind="discrete")
    assert result.status == "fit"
    assert result.budget_bytes is not None
    assert result.footprint_bytes <= result.budget_bytes


def test_model_fit_rejects_model_over_budget() -> None:
    result = assess_model_fit(1_000_000_000, 7.0, 4, kind="unified")
    assert result.status == "reject"
    assert "exceeds" in result.reason


def test_unknown_capacity_does_not_claim_model_fits() -> None:
    result = assess_model_fit(None, 3.0, 4)
    assert result.status == "unknown"
    assert result.budget_bytes is None


def test_recommendations_are_conservative_and_unknown_is_explicit() -> None:
    assert [item["label"] for item in recommend_models(8_000_000_000)] == ["3B Q4", "7B Q4"]
    assert [item["label"] for item in recommend_models(32_000_000_000)] == [
        "3B Q4", "7B Q4", "13B Q4", "34B Q4"
    ]
    assert len(recommend_models(None)) == 4


def test_invalid_memory_policy_arguments_fail_closed() -> None:
    with pytest.raises(ValueError, match="reserve_fraction"):
        memory_budget(10, kind="discrete", reserve_fraction=0.01)
    with pytest.raises(ValueError, match="params_b"):
        estimate_model_bytes(0, 4)
    with pytest.raises(ValueError, match="quant_bits"):
        estimate_model_bytes(1, 3)


def test_shared_memory_info_api_enforces_fit_before_model_load() -> None:
    info = MemoryInfo(
        kind="unified",
        total_bytes=16_000_000_000,
        available_bytes=16_000_000_000,
        backend="mps",
        device="Apple Silicon",
    )
    result = evaluate_model_fit(info, 3_000_000_000, quantization_bits=4, reserve_ratio=0.20)
    assert result.fits is True
    assert result.required_bytes == estimate_model_bytes(3.0, 4)
    assert result.reserved_bytes == 12_800_000_000
    assert "unified" in result.reason


def test_unknown_shared_memory_info_fails_closed() -> None:
    info = MemoryInfo(
        kind="unknown",
        total_bytes=0,
        available_bytes=0,
        backend="auto",
        device="unknown",
    )
    result = evaluate_model_fit(info, 7.0, quantization_bits=4)
    assert result.fits is False
    assert result.status == "unknown"


def test_model_guidance_differs_for_unified_and_discrete_memory() -> None:
    unified = model_guidance("unified")
    discrete = model_guidance("vram")
    unknown = model_guidance("unknown")
    assert "3B Q4" in unified["preferred_models"]
    assert "long-context" in unified["avoid"]
    assert "throughput" in discrete["strategy"]
    assert unknown["preferred_models"] == []
