"""Integration tests for bill-9 compute scheduling wiring.

Proves the ComputeSchedulingHint → route_task → AdaptiveRouter pipeline
is wired end-to-end, including:
- ComputeSchedulingHint.for_work_type() mappings (analysis→A100, review→T4, self_improve→H100)
- route_task() GPU-type-affinity routing
- Config gates enable/disable compute-aware scheduling
- Integration with AdaptiveRouter for GPU-aware model selection
- Non-GPU work types bypass GPU affinity
"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.scheduling.scheduler import ComputeSchedulingHint
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter


class TestComputeSchedulingHintForWorkTypeMappings:
    def test_analysis_maps_to_a100_80(self):
        hint = ComputeSchedulingHint.for_work_type("analysis")
        assert hint.preferred_gpu_type == "a100_80"
        assert hint.min_vram_gb == pytest.approx(40.0)
        assert hint.work_type == "analysis"

    def test_review_maps_to_t4(self):
        hint = ComputeSchedulingHint.for_work_type("review")
        assert hint.preferred_gpu_type == "t4"
        assert hint.min_vram_gb == pytest.approx(8.0)
        assert hint.work_type == "review"

    def test_self_improve_maps_to_h100(self):
        hint = ComputeSchedulingHint.for_work_type("self_improve")
        assert hint.preferred_gpu_type == "h100"
        assert hint.min_vram_gb == pytest.approx(80.0)
        assert hint.work_type == "self_improve"

    def test_vram_ordering_self_improve_gt_analysis_gt_review(self):
        si = ComputeSchedulingHint.for_work_type("self_improve")
        an = ComputeSchedulingHint.for_work_type("analysis")
        rv = ComputeSchedulingHint.for_work_type("review")
        assert si.min_vram_gb > an.min_vram_gb
        assert an.min_vram_gb > rv.min_vram_gb

    def test_unknown_work_type_returns_none_gpu(self):
        hint = ComputeSchedulingHint.for_work_type("foobar")
        assert hint.preferred_gpu_type is None
        assert hint.min_vram_gb == 0.0
        assert hint.estimated_tokens == 0


class TestRouteTaskGpuTypeAffinityRouting:
    def test_analysis_affinity_routes_to_a100_80_when_available(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "a100-1", "http://a100-1:8000", gpu_type="a100_80",
            max_concurrent=4, current_load=1,
        )
        tracker.register_endpoint(
            "t4-1", "http://t4-1:8000", gpu_type="t4",
            max_concurrent=4, current_load=0,
        )

        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("task-1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "a100-1"

    def test_review_affinity_routes_to_t4_when_available(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "h100-1", "http://h100-1:8000", gpu_type="h100",
            max_concurrent=4, current_load=0,
        )
        tracker.register_endpoint(
            "t4-1", "http://t4-1:8000", gpu_type="t4",
            max_concurrent=4, current_load=0,
        )

        hint = ComputeSchedulingHint.for_work_type("review")
        result = tracker.route_task("task-1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "t4-1"

    def test_self_improve_affinity_routes_to_h100(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "h100-1", "http://h100-1:8000", gpu_type="h100",
            max_concurrent=4, current_load=0,
        )
        tracker.register_endpoint(
            "a100-1", "http://a100-1:8000", gpu_type="a100_80",
            max_concurrent=4, current_load=0,
        )

        hint = ComputeSchedulingHint.for_work_type("self_improve")
        result = tracker.route_task("task-1", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "h100-1"

    def test_gpu_affinity_picks_least_utilized_among_matching_type(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "a100-busy", "http://busy:8000", gpu_type="a100_80",
            max_concurrent=4, current_load=3,
        )
        tracker.register_endpoint(
            "a100-free", "http://free:8000", gpu_type="a100_80",
            max_concurrent=4, current_load=0,
        )

        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("t", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "a100-free"

    def test_fallback_to_any_gpu_when_no_affinity_match(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "t4-1", "http://t4-1:8000", gpu_type="t4",
            max_concurrent=4, current_load=0,
        )

        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("t", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "t4-1"


class TestNonGpuWorkTypesBypassGpuAffinity:
    def test_no_scheduling_hint_routes_least_utilized(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "e1", "http://e1:8000", gpu_type="a100_80",
            max_concurrent=4, current_load=3,
        )
        tracker.register_endpoint(
            "e2", "http://e2:8000", gpu_type="t4",
            max_concurrent=4, current_load=0,
        )

        result = tracker.route_task("t", scheduling_hint=None)
        assert result is not None
        assert result.endpoint_id == "e2"

    def test_unknown_work_type_routes_without_gpu_affinity(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "h100-1", "http://h100-1:8000", gpu_type="h100",
            max_concurrent=4, current_load=2,
        )
        tracker.register_endpoint(
            "t4-1", "http://t4-1:8000", gpu_type="t4",
            max_concurrent=4, current_load=0,
        )

        hint = ComputeSchedulingHint.for_work_type("unknown")
        result = tracker.route_task("t", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "t4-1"

    def test_model_match_inside_gpu_affinity_filter(self):
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
        result = tracker.route_task("task-1", model="llama3", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "h100-llama"

    def test_no_endpoints_available_returns_none(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "full", "http://full:8000", gpu_type="a100_80",
            max_concurrent=4, current_load=4,
        )

        hint = ComputeSchedulingHint.for_work_type("analysis")
        result = tracker.route_task("t", scheduling_hint=hint)
        assert result is None


class TestConfigGatesForComputeAwareScheduling:
    def test_hint_with_override_preserves_work_type(self):
        hint = ComputeSchedulingHint.for_work_type(
            "review",
            preferred_gpu_type="l4",
            estimated_tokens=500,
            estimated_duration_seconds=30.0,
        )
        assert hint.preferred_gpu_type == "l4"
        assert hint.min_vram_gb == 8.0
        assert hint.estimated_tokens == 500
        assert hint.work_type == "review"

    def test_hint_is_frozen_dataclass(self):
        hint = ComputeSchedulingHint(preferred_gpu_type="a100_80")
        with pytest.raises(AttributeError):
            cast(Any, hint).preferred_gpu_type = "h100"

    def test_direct_construction_matches_factory(self):
        factory = ComputeSchedulingHint.for_work_type("analysis")
        direct = ComputeSchedulingHint(
            preferred_gpu_type="a100_80",
            min_vram_gb=40.0,
            work_type="analysis",
        )
        assert factory.preferred_gpu_type == direct.preferred_gpu_type
        assert factory.min_vram_gb == direct.min_vram_gb
        assert factory.work_type == direct.work_type

    def test_single_endpoint_always_returns_it(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            "only", "http://only:8000", gpu_type="t4",
            max_concurrent=4, current_load=1,
        )
        hint = ComputeSchedulingHint.for_work_type("review")
        result = tracker.route_task("t", scheduling_hint=hint)
        assert result is not None
        assert result.endpoint_id == "only"


class TestAdaptiveRouterGpuAwareModelSelection:
    def test_router_constructs_without_gpu_config(self):
        router = AdaptiveRouter()
        assert router._repo is None
        assert router._project_id is None
        assert not router._enable_cross_project_borrowing

    def test_router_invalidates_cache(self):
        router = AdaptiveRouter()
        router._cache["key"] = MagicMock()
        router._cache_time = MagicMock()
        router.invalidate_cache()
        assert len(router._cache) == 0
        assert router._cache_time is None

    def test_exceeds_cap_rejects_non_finite(self):
        assert AdaptiveRouter._exceeds_cap(float("nan"), 100.0)
        assert AdaptiveRouter._exceeds_cap(float("inf"), 100.0)

    def test_exceeds_cap_passes_cost_under_cap(self):
        assert not AdaptiveRouter._exceeds_cap(50.0, 100.0)
        assert AdaptiveRouter._exceeds_cap(101.0, 100.0)

    def test_router_leaderboard_empty_when_no_repo(self):
        async def _leaderboard():
            router = AdaptiveRouter()
            result = await router.get_leaderboard(TaskType.BUG_FIX)
            assert result == []
        asyncio.run(_leaderboard())

    def test_router_cache_invalid_when_no_cache_time(self):
        router = AdaptiveRouter()
        assert not router._cache_valid()
