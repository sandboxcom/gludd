"""Tests for shared unified-memory/discrete-VRAM model-fit policy."""

from __future__ import annotations

import pytest

from general_ludd.hardware_memory_policy import (
    assess_model_fit,
    classify_memory_kind,
    estimate_model_bytes,
    memory_budget,
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
