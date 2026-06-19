"""Tests for usefulness_score on RoutingCandidate and usefulness_weight on AdaptiveRouter."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from general_ludd.schemas.benchmark import RoutingCandidate, TaskType
from general_ludd.scoring.router import AdaptiveRouter


class TestRoutingCandidateUsefulnessScore:
    def test_default_usefulness_score(self) -> None:
        candidate = RoutingCandidate(
            prompt_profile_id=None,
            model_profile_id="model-a",
            composite_score=0.8,
            avg_cost_usd=0.001,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        assert candidate.usefulness_score == 0.5

    def test_explicit_usefulness_score(self) -> None:
        candidate = RoutingCandidate(
            prompt_profile_id=None,
            model_profile_id="model-a",
            composite_score=0.8,
            avg_cost_usd=0.001,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
            usefulness_score=0.9,
        )
        assert candidate.usefulness_score == 0.9

    def test_usefulness_score_zero(self) -> None:
        candidate = RoutingCandidate(
            prompt_profile_id=None,
            model_profile_id="model-a",
            composite_score=0.5,
            avg_cost_usd=0.0,
            sample_count=1,
            task_type=TaskType.FEATURE,
            usefulness_score=0.0,
        )
        assert candidate.usefulness_score == 0.0

    def test_usefulness_score_one(self) -> None:
        candidate = RoutingCandidate(
            prompt_profile_id=None,
            model_profile_id="model-a",
            composite_score=0.5,
            avg_cost_usd=0.0,
            sample_count=1,
            task_type=TaskType.FEATURE,
            usefulness_score=1.0,
        )
        assert candidate.usefulness_score == 1.0

    def test_usefulness_score_out_of_range_high(self) -> None:
        with pytest.raises(ValueError):
            RoutingCandidate(
                prompt_profile_id=None,
                model_profile_id="model-a",
                composite_score=0.5,
                avg_cost_usd=0.0,
                sample_count=1,
                task_type=TaskType.FEATURE,
                usefulness_score=1.1,
            )

    def test_usefulness_score_out_of_range_low(self) -> None:
        with pytest.raises(ValueError):
            RoutingCandidate(
                prompt_profile_id=None,
                model_profile_id="model-a",
                composite_score=0.5,
                avg_cost_usd=0.0,
                sample_count=1,
                task_type=TaskType.FEATURE,
                usefulness_score=-0.1,
            )


def _make_agg(
    model_id: str,
    composite: float,
    cost: float,
    count: int = 5,
    usefulness: float = 0.5,
) -> dict[str, Any]:
    return {
        "model_profile_id": model_id,
        "composite_score": composite,
        "avg_cost": cost,
        "sample_count": count,
        "prompt_profile_id": None,
        "task_type": TaskType.BUG_FIX.value,
        "usefulness_score": usefulness,
    }


class TestAdaptiveRouterUsefulnessWeight:
    def test_usefulness_weight_stored(self) -> None:
        router = AdaptiveRouter(usefulness_weight=0.15)
        assert router._usefulness_weight == 0.15

    def test_default_usefulness_weight(self) -> None:
        router = AdaptiveRouter()
        assert router._usefulness_weight == 0.1

    def test_high_usefulness_lifts_candidate(self) -> None:
        """A candidate with higher usefulness_score should rank above equal-quality peer."""
        low_useful = RoutingCandidate(
            prompt_profile_id=None,
            model_profile_id="low-useful",
            composite_score=0.7,
            avg_cost_usd=0.001,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
            usefulness_score=0.1,
        )
        high_useful = RoutingCandidate(
            prompt_profile_id=None,
            model_profile_id="high-useful",
            composite_score=0.7,
            avg_cost_usd=0.001,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
            usefulness_score=0.9,
        )
        router = AdaptiveRouter(usefulness_weight=0.3)
        max_cost = max(low_useful.avg_cost_usd, high_useful.avg_cost_usd)
        rank_low = router._cost_adjusted_rank(low_useful, low_useful.composite_score, max_cost)
        rank_high = router._cost_adjusted_rank(high_useful, high_useful.composite_score, max_cost)
        assert rank_high > rank_low

    def test_zero_usefulness_weight_ignores_usefulness(self) -> None:
        """With usefulness_weight=0, two candidates differing only in usefulness rank the same."""
        c1 = RoutingCandidate(
            prompt_profile_id=None,
            model_profile_id="m1",
            composite_score=0.6,
            avg_cost_usd=0.001,
            sample_count=5,
            task_type=TaskType.REFACTOR,
            usefulness_score=0.0,
        )
        c2 = RoutingCandidate(
            prompt_profile_id=None,
            model_profile_id="m2",
            composite_score=0.6,
            avg_cost_usd=0.001,
            sample_count=5,
            task_type=TaskType.REFACTOR,
            usefulness_score=1.0,
        )
        router = AdaptiveRouter(usefulness_weight=0.0)
        max_cost = 0.001
        assert router._cost_adjusted_rank(c1, c1.composite_score, max_cost) == pytest.approx(
            router._cost_adjusted_rank(c2, c2.composite_score, max_cost)
        )

    def test_router_selects_higher_usefulness_from_repo(self) -> None:
        """End-to-end: router picks the candidate with higher usefulness from benchmark repo."""
        repo = AsyncMock()
        repo.get_aggregate_scores = AsyncMock(
            return_value=[
                _make_agg("model-low-useful", 0.7, 0.001, usefulness=0.1),
                _make_agg("model-high-useful", 0.7, 0.001, usefulness=0.9),
            ]
        )
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3, usefulness_weight=0.5)

        async def run() -> str:
            decision = await router.route(TaskType.BUG_FIX)
            return decision.selected_model_profile_id

        result = asyncio.run(run())
        assert result == "model-high-useful"

    def test_usefulness_weight_constructor_kwarg(self) -> None:
        router = AdaptiveRouter(usefulness_weight=0.25)
        assert router._usefulness_weight == 0.25
