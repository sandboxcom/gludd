"""Integration tests for AdaptiveRouter cost-constrained routing end-to-end.

Proves that the AdaptiveRouter with max_cost_usd constraint:
  - selects a cheaper candidate when best exceeds the cost cap,
  - fails closed (fallback) when no candidate fits under budget,
  - caches decisions within the TTL window,
  - returns the best historical candidate when under budget.
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.schemas.benchmark import (
    RoutingCandidate,
    RoutingDecision,
    TaskType,
)
from general_ludd.scoring.router import AdaptiveRouter


def _dummy_candidate(
    prompt_id: str = "p1",
    model_id: str = "m1",
    score: float = 0.85,
    cost: float = 0.01,
    samples: int = 10,
    task_type: TaskType = TaskType.FEATURE,
) -> RoutingCandidate:
    return RoutingCandidate(
        prompt_profile_id=prompt_id,
        model_profile_id=model_id,
        composite_score=score,
        avg_cost_usd=cost,
        sample_count=samples,
        task_type=task_type,
    )


def _dummy_aggregate(prompt_id: str, model_id: str, score: float, cost: float, samples: int) -> dict:
    return {
        "prompt_profile_id": prompt_id,
        "model_profile_id": model_id,
        "composite_score": score,
        "avg_cost": cost,
        "sample_count": samples,
    }


class TestAdaptiveRouterCostConstrained:
    """Cost-constrained routing: max_cost_usd forces cheaper candidate selection."""

    @pytest.mark.asyncio
    async def test_best_under_cap_returns_best(self):
        """When best candidate fits under the cost cap, it is selected."""
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _dummy_aggregate("p1", "m1", 0.90, 0.005, 10),
            _dummy_aggregate("p2", "m2", 0.70, 0.003, 10),
        ]
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=1)

        decision = await router.route(TaskType.FEATURE, max_cost_usd=0.01)

        assert decision.fallback is False
        assert decision.selected_model_profile_id == "m1"
        assert decision.selected_prompt_profile_id == "p1"
        assert decision.reason == "best_historical_score"

    @pytest.mark.asyncio
    async def test_best_over_cap_selects_cheaper(self):
        """When best candidate exceeds cost cap, cheaper candidate selected."""
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _dummy_aggregate("p1", "expensive-best", 0.99, 0.05, 10),
            _dummy_aggregate("p2", "cheaper-fit", 0.70, 0.002, 10),
        ]
        router = AdaptiveRouter(
            benchmark_repo=repo, min_samples=1,
            cost_weight=0.0, quality_weight=1.0,  # pure quality ranking
        )

        decision = await router.route(TaskType.FEATURE, max_cost_usd=0.01)

        # expensive-best has higher quality but exceeds cap → cheaper-fit selected
        assert decision.fallback is False
        assert decision.selected_model_profile_id == "cheaper-fit"
        assert decision.reason == "cost_constrained"

    @pytest.mark.asyncio
    async def test_no_candidate_fits_cap_falls_back(self):
        """When NO candidate fits under the cost cap, fails closed to defaults."""
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _dummy_aggregate("p1", "m1", 0.90, 0.05, 10),
            _dummy_aggregate("p2", "m2", 0.70, 0.04, 10),
        ]
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=1)

        decision = await router.route(
            TaskType.FEATURE,
            max_cost_usd=0.01,
            default_model_profile="safe-default",
            default_prompt_profile="safe-prompt",
        )

        assert decision.fallback is True
        assert decision.reason == "cost_cap_no_fit"
        assert decision.selected_model_profile_id == "safe-default"
        assert decision.selected_prompt_profile_id == "safe-prompt"

    @pytest.mark.asyncio
    async def test_insufficient_data_falls_back(self):
        """When repo has no data, fallback to defaults regardless of cap."""
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = []
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)

        decision = await router.route(
            TaskType.FEATURE,
            max_cost_usd=0.01,
            default_model_profile="safe-default",
            default_prompt_profile="safe-prompt",
        )

        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"


class TestAdaptiveRouterCacheBehavior:
    """Cache: decisions are cached within TTL to avoid recomputation."""

    @pytest.mark.asyncio
    async def test_cache_returns_same_decision_within_ttl(self):
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _dummy_aggregate("p1", "m1", 0.88, 0.005, 10),
        ]
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=1)

        d1 = await router.route(TaskType.FEATURE, max_cost_usd=0.01)
        d2 = await router.route(TaskType.FEATURE, max_cost_usd=0.01)

        # Second call should hit cache — repo only called once
        assert repo.get_aggregate_scores.call_count == 1
        assert d1.selected_model_profile_id == d2.selected_model_profile_id
        assert d1.composite_score == d2.composite_score

    @pytest.mark.asyncio
    async def test_different_cost_caps_use_different_cache_keys(self):
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _dummy_aggregate("p1", "m1", 0.90, 0.005, 10),
            _dummy_aggregate("p2", "m2", 0.70, 0.002, 10),
        ]
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=1)

        d_tight = await router.route(TaskType.FEATURE, max_cost_usd=0.001)
        d_loose = await router.route(TaskType.FEATURE, max_cost_usd=0.01)

        # Tight cap has no candidate → fallback (2 repo calls: best + cheapest)
        assert d_tight.fallback is True
        # Loose cap fits best → non-fallback (1 more call, different cache key)
        assert d_loose.fallback is False
        # Total: 3 calls (1 for tight-best, 1 for tight-cheapest, 1 for loose-best)
        assert repo.get_aggregate_scores.call_count == 3

    @pytest.mark.asyncio
    async def test_unhealthy_model_bypasses_cache(self):
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            _dummy_aggregate("p1", "m1", 0.90, 0.005, 10),
        ]
        tracker = MagicMock()
        tracker.is_healthy.return_value = True  # first call passes health check

        router = AdaptiveRouter(benchmark_repo=repo, min_samples=1, health_tracker=tracker)

        d1 = await router.route(TaskType.FEATURE, max_cost_usd=0.01)
        assert d1.selected_model_profile_id == "m1"

        # Now make the cached model unhealthy — cache should be skipped
        tracker.is_healthy.return_value = False
        await router.route(TaskType.FEATURE, max_cost_usd=0.01)

        # Repo called again (cache was bypassed due to unhealthy model)
        assert repo.get_aggregate_scores.call_count == 2


class TestAdaptiveRouterEdgeCases:
    """Edge cases: nil repo, non-finite costs, quantization."""

    @pytest.mark.asyncio
    async def test_nil_repo_falls_back(self):
        router = AdaptiveRouter(benchmark_repo=None, min_samples=1)
        decision = await router.route(
            TaskType.FEATURE,
            max_cost_usd=0.01,
            default_model_profile="safe-default",
            default_prompt_profile="safe-prompt",
        )
        assert decision.fallback is True
        assert decision.reason == "insufficient_historical_data"

    @pytest.mark.asyncio
    async def test_exceeds_cap_treats_nan_cost_as_over(self):
        """Non-finite cost is treated as exceeding the cap (fail closed)."""
        is_over = AdaptiveRouter._exceeds_cap(math.nan, 0.01)
        assert is_over is True
        is_over = AdaptiveRouter._exceeds_cap(math.inf, 0.01)
        assert is_over is True

    @pytest.mark.asyncio
    async def test_exceeds_cap_treats_finite_cost_correctly(self):
        assert AdaptiveRouter._exceeds_cap(0.005, 0.01) is False
        assert AdaptiveRouter._exceeds_cap(0.01, 0.01) is False
        assert AdaptiveRouter._exceeds_cap(0.011, 0.01) is True


class TestRoutingDecisionProperties:
    """RoutingDecision carries all relevant fields for the dispatch path."""

    def test_routing_decision_fallback_carries_reason(self) -> None:
        d = RoutingDecision(
            selected_prompt_profile_id="p-safe",
            selected_model_profile_id="m-safe",
            composite_score=0.0,
            estimated_cost_usd=0.0,
            sample_count=0,
            fallback=True,
            reason="cost_cap_no_fit",
        )
        assert d.fallback is True
        assert d.reason == "cost_cap_no_fit"
        assert d.composite_score == 0.0

    def test_routing_decision_best_hist_carries_full_data(self) -> None:
        d = RoutingDecision(
            selected_prompt_profile_id="p1",
            selected_model_profile_id="m1",
            composite_score=0.88,
            estimated_cost_usd=0.005,
            sample_count=50,
            fallback=False,
            reason="best_historical_score",
        )
        assert d.fallback is False
        assert d.composite_score == pytest.approx(0.88)
        assert d.estimated_cost_usd == pytest.approx(0.005)
        assert d.sample_count == 50
