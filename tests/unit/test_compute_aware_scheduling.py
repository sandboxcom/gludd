"""Unit tests for compute-aware scheduling — GPU affinity, cost-aware model selection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.scheduling.scheduler import ComputeSchedulingHint

# ---------------------------------------------------------------------------
# ComputeSchedulingHint — work-type defaults
# ---------------------------------------------------------------------------


class TestComputeSchedulingHintDefaults:
    def test_analysis_maps_to_a100_80(self):
        hint = ComputeSchedulingHint.for_work_type("analysis")
        assert hint.preferred_gpu_type == "a100_80"
        assert hint.min_vram_gb == 40.0

    def test_review_maps_to_t4(self):
        hint = ComputeSchedulingHint.for_work_type("review")
        assert hint.preferred_gpu_type == "t4"
        assert hint.min_vram_gb == 8.0

    def test_self_improve_maps_to_h100(self):
        hint = ComputeSchedulingHint.for_work_type("self_improve")
        assert hint.preferred_gpu_type == "h100"
        assert hint.min_vram_gb == 80.0

    def test_unknown_work_type_has_no_default_gpu(self):
        hint = ComputeSchedulingHint.for_work_type("unknown_type")
        assert hint.preferred_gpu_type is None
        assert hint.min_vram_gb == 0.0

    def test_overrides_are_honored(self):
        hint = ComputeSchedulingHint.for_work_type(
            "analysis",
            preferred_gpu_type="h200",
            estimated_tokens=5000,
        )
        assert hint.preferred_gpu_type == "h200"
        assert hint.estimated_tokens == 5000
        assert hint.min_vram_gb == 40.0

    def test_direct_construction(self):
        hint = ComputeSchedulingHint(
            preferred_gpu_type="a100_80",
            min_vram_gb=40.0,
            estimated_tokens=10000,
            estimated_duration_seconds=120.0,
            work_type="analysis",
        )
        assert hint.preferred_gpu_type == "a100_80"
        assert hint.min_vram_gb == 40.0
        assert hint.estimated_tokens == 10000
        assert hint.estimated_duration_seconds == 120.0
        assert hint.work_type == "analysis"

    def test_is_frozen(self):
        hint = ComputeSchedulingHint(preferred_gpu_type="t4")
        with pytest.raises(FrozenInstanceError):
            hint.preferred_gpu_type = "h100"  # type: ignore[misc]  # frozen dataclass: tests immutability


# ---------------------------------------------------------------------------
# GPU type affinity routing
# ---------------------------------------------------------------------------


class TestGpuAffinityRouting:
    def test_routes_to_matching_gpu_type(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e-a100", "http://a100:8000", gpu_type="a100_80", max_concurrent=4)
        tracker.register_endpoint("e-t4", "http://t4:8000", gpu_type="t4", max_concurrent=4)
        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("t1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "e-a100"
        assert result.reason == "gpu_affinity"

    def test_routes_to_matching_gpu_hint_h100(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e-h100", "http://h100:8000", gpu_type="h100", max_concurrent=4)
        tracker.register_endpoint("e-a100", "http://a100:8000", gpu_type="a100_80", max_concurrent=4)
        hint = ComputeSchedulingHint.for_work_type("self_improve")
        result = tracker.route_task("t1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "e-h100"
        assert result.reason == "gpu_affinity"

    def test_fallback_to_least_utilized_when_no_gpu_match(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e-t4", "http://t4:8000", gpu_type="t4", max_concurrent=4, current_load=3)
        tracker.register_endpoint("e-l4", "http://l4:8000", gpu_type="l4", max_concurrent=4, current_load=0)
        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("t1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "e-l4"
        assert result.reason == "least_utilized"

    def test_gpu_affinity_subset_when_multiple_match(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", gpu_type="a100_80", max_concurrent=4, current_load=3)
        tracker.register_endpoint("e2", "http://e2", gpu_type="a100_80", max_concurrent=4, current_load=0)
        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("t1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "e2"
        assert result.reason == "gpu_affinity"

    def test_model_match_still_works_with_gpu_hint(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", gpu_type="a100_80", model="llama3", max_concurrent=4)
        tracker.register_endpoint("e2", "http://e2", gpu_type="a100_80", model="mistral", max_concurrent=4)
        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("t1", model="llama3", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "e1"

    def test_gpu_affinity_ignored_when_hint_is_none(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e-a100", "http://a100:8000", gpu_type="a100_80", max_concurrent=4, current_load=3)
        tracker.register_endpoint("e-t4", "http://t4:8000", gpu_type="t4", max_concurrent=4, current_load=0)
        result = tracker.route_task("t1", scheduling_hint=None)
        assert result is not None
        assert result.endpoint_id == "e-t4"
        assert result.reason == "least_utilized"


# ---------------------------------------------------------------------------
# Cost-effective profile selection
# ---------------------------------------------------------------------------


def _make_profile(
    profile_id: str,
    *,
    cost_input: float = 0.0,
    cost_output: float = 0.0,
    run_budget_usd: float = 200.0,
    enabled: bool = True,
    api_metered: bool = True,
) -> ModelProfile:
    return ModelProfile(
        model_profile_id=profile_id,
        model_name=profile_id,
        context_window=128000,
        max_input_tokens=120000,
        max_output_tokens=8000,
        cost_per_input_token=cost_input,
        cost_per_output_token=cost_output,
        run_budget_usd=run_budget_usd,
        enabled=enabled,
        api_metered=api_metered,
    )


class TestCostEffectiveProfileSelection:
    def test_prefers_cheapest_eligible_profile(self):
        cheap = _make_profile("cheap", cost_input=1e-6, cost_output=2e-6)
        medium = _make_profile("medium", cost_input=5e-6, cost_output=10e-6)
        expensive = _make_profile("expensive", cost_input=1e-5, cost_output=2e-5)
        result = ModelGateway.select_cost_effective_profile(
            [cheap, medium, expensive], budget_remaining=500.0
        )
        assert result is not None
        assert result.model_profile_id == "cheap"

    def test_excludes_disabled_profiles(self):
        disabled = _make_profile("disabled", cost_input=1e-7, cost_output=2e-7, enabled=False)
        enabled = _make_profile("enabled", cost_input=5e-6, cost_output=10e-6)
        result = ModelGateway.select_cost_effective_profile(
            [disabled, enabled], budget_remaining=500.0
        )
        assert result is not None
        assert result.model_profile_id == "enabled"

    def test_budget_cap_blocks_expensive_model(self):
        """A profile whose run_budget exceeds remaining budget is excluded."""
        cheap = _make_profile("cheap", cost_input=1e-6, cost_output=2e-6, run_budget_usd=50.0)
        expensive = _make_profile("expensive", cost_input=5e-6, cost_output=10e-6, run_budget_usd=500.0)
        result = ModelGateway.select_cost_effective_profile(
            [cheap, expensive], budget_remaining=60.0
        )
        assert result is not None
        assert result.model_profile_id == "cheap"

    def test_budget_cap_gates_all_when_none_eligible(self):
        expensive = _make_profile("expensive", run_budget_usd=500.0)
        medium = _make_profile("medium", run_budget_usd=300.0)
        result = ModelGateway.select_cost_effective_profile(
            [expensive, medium], budget_remaining=10.0
        )
        assert result is None

    def test_unmetered_profiles_always_eligible(self):
        cheap = _make_profile("cheap", cost_input=5e-6, cost_output=10e-6, api_metered=False, run_budget_usd=500.0)
        expensive = _make_profile("expensive", cost_input=1e-5, cost_output=2e-5, run_budget_usd=10.0)
        result = ModelGateway.select_cost_effective_profile(
            [cheap, expensive], budget_remaining=5.0
        )
        assert result is not None
        assert result.model_profile_id == "cheap"

    def test_zero_run_budget_usd_treated_as_unconstrained(self):
        unconstrained = _make_profile("unconstrained", cost_input=1e-6, cost_output=2e-6, run_budget_usd=0.0)
        constrained = _make_profile("constrained", cost_input=5e-6, cost_output=10e-6, run_budget_usd=500.0)
        result = ModelGateway.select_cost_effective_profile(
            [unconstrained, constrained], budget_remaining=1.0
        )
        assert result is not None
        assert result.model_profile_id == "unconstrained"

    def test_empty_profile_list_returns_none(self):
        result = ModelGateway.select_cost_effective_profile([], budget_remaining=100.0)
        assert result is None

    def test_sort_order_uses_combined_cost(self):
        a = _make_profile("a", cost_input=1e-5, cost_output=1e-5)   # sum: 2e-5
        b = _make_profile("b", cost_input=1e-6, cost_output=1e-6)   # sum: 2e-6
        c = _make_profile("c", cost_input=0.0, cost_output=1e-5)    # sum: 1e-5
        result = ModelGateway.select_cost_effective_profile(
            [a, b, c], budget_remaining=500.0
        )
        assert result is not None
        assert result.model_profile_id == "b"


# ---------------------------------------------------------------------------
# ComputeSchedulingHint — edge cases
# ---------------------------------------------------------------------------


class TestComputeSchedulingHintEdgeCases:
    def test_for_work_type_with_no_gpu_affinity(self):
        hint = ComputeSchedulingHint.for_work_type("review")
        result = hint.preferred_gpu_type
        assert result in ("t4", "l4")

    def test_for_work_type_allows_l4_for_review(self):
        hint = ComputeSchedulingHint.for_work_type("review", preferred_gpu_type="l4")
        assert hint.preferred_gpu_type == "l4"
        assert hint.min_vram_gb == 8.0
