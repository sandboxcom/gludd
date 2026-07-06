"""Integration tests for compute-aware scheduling with GPU affinity.

Proves end-to-end that:
- ComputeSchedulingHint integrates with UtilizationTracker.route_task()
- GPU affinity correctly routes to matching GPU types
- Fallback to least-utilized when no GPU match exists
- Cost-effective profile selection honors budget constraints
- Multi-endpoint scenarios with mixed GPU types route correctly
- Work-type default mappings produce expected routing
- Override behavior works for custom scheduling hints
- Empty/high-load/no-match edge cases handled safely
"""

from __future__ import annotations

import pytest

from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.models.gateway import ModelProfile
from general_ludd.scheduling.scheduler import ComputeSchedulingHint


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


# ---------------------------------------------------------------------------
# Route task with GPU affinity
# ---------------------------------------------------------------------------


class TestRouteTaskGpuAffinity:
    def test_analysis_routes_to_a100_80(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("a100-1", "http://a100-1:8000", gpu_type="a100_80", max_concurrent=4, current_load=1)
        tracker.register_endpoint("t4-1", "http://t4-1:8000", gpu_type="t4", max_concurrent=4, current_load=0)

        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("task-1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "a100-1"

    def test_review_routes_to_t4(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("h100-1", "http://h100-1:8000", gpu_type="h100", max_concurrent=4, current_load=0)
        tracker.register_endpoint("t4-1", "http://t4-1:8000", gpu_type="t4", max_concurrent=4, current_load=0)

        hint = ComputeSchedulingHint.for_work_type("review")
        result = tracker.route_task("task-1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "t4-1"

    def test_self_improve_routes_to_h100(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("h100-1", "http://h100-1:8000", gpu_type="h100", max_concurrent=4, current_load=0)
        tracker.register_endpoint("a100-1", "http://a100-1:8000", gpu_type="a100_80", max_concurrent=4, current_load=0)

        hint = ComputeSchedulingHint.for_work_type("self_improve")
        result = tracker.route_task("task-1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "h100-1"

    def test_gpu_affinity_picks_least_utilized_among_matches(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("a100-busy", "http://busy:8000", gpu_type="a100_80", max_concurrent=4, current_load=3)
        tracker.register_endpoint("a100-free", "http://free:8000", gpu_type="a100_80", max_concurrent=4, current_load=0)

        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("task-1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "a100-free"

    def test_fallback_when_no_matching_gpu(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("t4-1", "http://t4-1:8000", gpu_type="t4", max_concurrent=4, current_load=0)

        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("task-1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "t4-1"

    def test_model_match_overrides_gpu_affinity(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "h100-llama", "http://h100:8000", gpu_type="h100",
            model="llama3", max_concurrent=4, current_load=0,
        )
        tracker.register_endpoint(
            "a100-mistral", "http://a100:8000", gpu_type="a100_80",
            model="mistral", max_concurrent=4, current_load=0,
        )

        hint = ComputeSchedulingHint.for_work_type("self_improve")
        result = tracker.route_task("task-1", model="mistral", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "a100-mistral"


# ---------------------------------------------------------------------------
# Cost-effective profile selection
# ---------------------------------------------------------------------------


class TestCostEffectiveProfileIntegration:
    def test_cheapest_eligible_wins(self):
        a = _make_profile("a", cost_input=1e-6, cost_output=2e-6)
        b = _make_profile("b", cost_input=5e-7, cost_output=1e-6)
        c = _make_profile("c", cost_input=1e-5, cost_output=2e-5)

        from general_ludd.models.gateway import ModelGateway
        result = ModelGateway.select_cost_effective_profile([a, b, c], budget_remaining=500)
        assert result is not None
        assert result.model_profile_id == "b"

    def test_disabled_excluded_from_selection(self):
        a = _make_profile("a", cost_input=1e-6, cost_output=2e-6, enabled=False)
        b = _make_profile("b", cost_input=5e-6, cost_output=1e-5)

        from general_ludd.models.gateway import ModelGateway
        result = ModelGateway.select_cost_effective_profile([a, b], budget_remaining=500)
        assert result is not None
        assert result.model_profile_id == "b"

    def test_budget_cap_blocks_over_budget(self):
        cheap = _make_profile("cheap", run_budget_usd=10.0)
        expensive = _make_profile("expensive", cost_input=2e-6, cost_output=4e-6, run_budget_usd=500.0)

        from general_ludd.models.gateway import ModelGateway
        result = ModelGateway.select_cost_effective_profile([cheap, expensive], budget_remaining=5.0)
        assert result is not None
        assert result.model_profile_id == "cheap"

    def test_unmetered_always_eligible(self):
        unmetered = _make_profile("local", cost_input=5e-6, cost_output=10e-6, api_metered=False, run_budget_usd=500)
        metered = _make_profile("cloud", cost_input=1e-6, cost_output=2e-6, run_budget_usd=100)

        from general_ludd.models.gateway import ModelGateway
        result = ModelGateway.select_cost_effective_profile([unmetered, metered], budget_remaining=1.0)
        assert result is not None
        assert result.model_profile_id == "local"

    def test_zero_run_budget_is_unconstrained(self):
        unconstrained = _make_profile("free", run_budget_usd=0.0)
        constrained = _make_profile("capped", run_budget_usd=50.0)

        from general_ludd.models.gateway import ModelGateway
        result = ModelGateway.select_cost_effective_profile([unconstrained, constrained], budget_remaining=1.0)
        assert result is not None
        assert result.model_profile_id == "free"

    def test_all_over_budget_returns_none(self):
        big = _make_profile("big", run_budget_usd=500)
        medium = _make_profile("medium", run_budget_usd=300)

        from general_ludd.models.gateway import ModelGateway
        result = ModelGateway.select_cost_effective_profile([big, medium], budget_remaining=10.0)
        assert result is None

    def test_empty_list_returns_none(self):
        from general_ludd.models.gateway import ModelGateway
        assert ModelGateway.select_cost_effective_profile([], budget_remaining=100) is None


# ---------------------------------------------------------------------------
# Multi-endpoint scheduling scenarios
# ---------------------------------------------------------------------------


class TestMultiEndpointScheduling:
    def test_heterogeneous_fleet_analysis_routes_best(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("t4-a", "http://t4-a:8000", gpu_type="t4", max_concurrent=4, current_load=0)
        tracker.register_endpoint("t4-b", "http://t4-b:8000", gpu_type="t4", max_concurrent=4, current_load=1)
        tracker.register_endpoint("a100-a", "http://a100-a:8000", gpu_type="a100_80", max_concurrent=4, current_load=2)
        tracker.register_endpoint("a100-b", "http://a100-b:8000", gpu_type="a100_80", max_concurrent=4, current_load=0)
        tracker.register_endpoint("h100-a", "http://h100-a:8000", gpu_type="h100", max_concurrent=4, current_load=0)

        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("t", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "a100-b"

    def test_no_gpu_hint_routes_least_utilized(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("busy", "http://busy:8000", gpu_type="a100_80", max_concurrent=4, current_load=3)
        tracker.register_endpoint("free", "http://free:8000", gpu_type="t4", max_concurrent=4, current_load=0)

        result = tracker.route_task("t", scheduling_hint=None)
        assert result is not None
        assert result.endpoint_id == "free"

    def test_all_endpoints_full_returns_none(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("full1", "http://full1:8000", gpu_type="a100_80", max_concurrent=4, current_load=4)
        tracker.register_endpoint("full2", "http://full2:8000", gpu_type="t4", max_concurrent=4, current_load=4)

        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("t", scheduling_hint=hint)
        assert result is None

    def test_single_endpoint_returns_it(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("only", "http://only:8000", gpu_type="t4", max_concurrent=4, current_load=1)

        hint = ComputeSchedulingHint.for_work_type("review")
        result = tracker.route_task("t", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "only"


# ---------------------------------------------------------------------------
# ComputeSchedulingHint integration edge cases
# ---------------------------------------------------------------------------


class TestComputeSchedulingHintEdgeCases:
    def test_unknown_work_type_has_none_gpu(self):
        hint = ComputeSchedulingHint.for_work_type("unknown_xyz")
        assert hint.preferred_gpu_type is None
        assert hint.min_vram_gb == 0.0
        assert hint.estimated_tokens == 0

    def test_override_work_type_preserves_other_defaults(self):
        hint = ComputeSchedulingHint.for_work_type(
            "review",
            preferred_gpu_type="l4",
            estimated_tokens=500,
            estimated_duration_seconds=30.0,
        )
        assert hint.preferred_gpu_type == "l4"
        assert hint.min_vram_gb == 8.0
        assert hint.estimated_tokens == 500
        assert hint.estimated_duration_seconds == 30.0
        assert hint.work_type == "review"

    def test_hint_is_immutable(self):
        hint = ComputeSchedulingHint(preferred_gpu_type="a100_80")
        with pytest.raises(AttributeError):
            hint.preferred_gpu_type = "h100"  # type: ignore[misc]  # frozen dataclass: tests immutability

    def test_work_type_defaults_are_consistent(self):
        analysis = ComputeSchedulingHint.for_work_type("analysis")
        review = ComputeSchedulingHint.for_work_type("review")
        self_improve = ComputeSchedulingHint.for_work_type("self_improve")

        assert analysis.preferred_gpu_type == "a100_80"
        assert review.preferred_gpu_type == "t4"
        assert self_improve.preferred_gpu_type == "h100"

        assert analysis.min_vram_gb > review.min_vram_gb
        assert self_improve.min_vram_gb > analysis.min_vram_gb

    def test_direct_construction_vs_for_work_type(self):
        factory = ComputeSchedulingHint.for_work_type("analysis")
        direct = ComputeSchedulingHint(
            preferred_gpu_type="a100_80",
            min_vram_gb=40.0,
            work_type="analysis",
        )
        assert factory.preferred_gpu_type == direct.preferred_gpu_type
        assert factory.min_vram_gb == direct.min_vram_gb
        assert factory.work_type == direct.work_type
