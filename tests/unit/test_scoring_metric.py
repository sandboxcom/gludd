"""Tests for the extracted W$ scoring metric (C-05) and per-task weight
selection in AdaptiveRouter.route (D-10)."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest

from general_ludd.routing_roles.weights import RoleWeights, weights_for
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.metric import normalize_cost, score
from general_ludd.scoring.router import AdaptiveRouter

# ---------------------------------------------------------------------------
# C-05: pure metric
# ---------------------------------------------------------------------------


class TestScoreMetric:
    def test_pure_formula_matches_definition(self):
        weights = RoleWeights(cost=0.25, quality=0.75)
        # cost_norm = 0.4 / 0.8 = 0.5; 0.75*0.9 - 0.25*0.5 = 0.675 - 0.125
        assert score(0.4, 0.9, weights, max_cost=0.8) == pytest.approx(0.55)

    def test_zero_max_cost_drops_penalty(self):
        weights = RoleWeights(cost=0.3, quality=0.7)
        # max_cost <= 0 => cost_norm 0 => only quality term
        assert score(5.0, 1.0, weights, max_cost=0.0) == pytest.approx(0.7)

    def test_non_finite_cost_drops_penalty(self):
        weights = RoleWeights(cost=0.3, quality=0.7)
        assert score(math.inf, 1.0, weights, max_cost=2.0) == pytest.approx(0.7)
        assert score(math.nan, 1.0, weights, max_cost=2.0) == pytest.approx(0.7)

    def test_normalize_cost(self):
        assert normalize_cost(1.0, 4.0) == pytest.approx(0.25)
        assert normalize_cost(1.0, 0.0) == 0.0
        assert normalize_cost(math.inf, 4.0) == 0.0

    def test_higher_quality_ranks_higher(self):
        w = RoleWeights(cost=0.2, quality=0.8)
        assert score(0.5, 0.9, w, 1.0) > score(0.5, 0.4, w, 1.0)

    def test_higher_cost_ranks_lower(self):
        w = RoleWeights(cost=0.2, quality=0.8)
        assert score(0.9, 0.7, w, 1.0) < score(0.1, 0.7, w, 1.0)


# ---------------------------------------------------------------------------
# D-10: per-task weight selection in route()
# ---------------------------------------------------------------------------


def _agg(model_id: str, composite: float, cost: float, samples: int = 5) -> dict:
    return {
        "prompt_profile_id": "p1",
        "model_profile_id": model_id,
        "composite_score": composite,
        "avg_cost": cost,
        "sample_count": samples,
        "task_type": TaskType.REFACTOR.value,
    }


def _router_with(aggregates: list[dict]) -> AdaptiveRouter:
    repo = AsyncMock()
    repo.get_aggregate_scores = AsyncMock(return_value=aggregates)
    return AdaptiveRouter(benchmark_repo=repo, min_samples=1)


class TestPerTaskWeightSelection:
    @pytest.mark.asyncio
    async def test_no_task_role_uses_task_type_weights(self):
        # Two candidates: A high quality + high cost, B lower quality + free.
        aggs = [_agg("A", 0.90, 1.0), _agg("B", 0.80, 0.0)]
        router = _router_with(aggs)
        # No task_role -> behavior identical to before (weights_for(task_type)).
        decision = await router.route(TaskType.REFACTOR)
        # REFACTOR weights (0.25 cost, 0.75 quality):
        #   A: 0.75*0.90 - 0.25*1.0 = 0.425
        #   B: 0.75*0.80 - 0.25*0.0 = 0.600  -> B wins
        assert decision.selected_model_profile_id == "B"

    @pytest.mark.asyncio
    async def test_task_role_override_changes_winner(self):
        aggs = [_agg("A", 0.90, 1.0), _agg("B", 0.80, 0.0)]
        router = _router_with(aggs)
        # Override with SECURITY_FIX weights (0.05 cost, 0.95 quality):
        #   A: 0.95*0.90 - 0.05*1.0 = 0.805
        #   B: 0.95*0.80 - 0.05*0.0 = 0.760  -> A wins now
        decision = await router.route(
            TaskType.REFACTOR, task_role=TaskType.SECURITY_FIX
        )
        assert decision.selected_model_profile_id == "A"

    @pytest.mark.asyncio
    async def test_role_matching_task_type_is_identical_to_none(self):
        aggs = [_agg("A", 0.90, 1.0), _agg("B", 0.80, 0.0)]
        d_none = await _router_with(aggs).route(TaskType.REFACTOR)
        d_same = await _router_with(aggs).route(
            TaskType.REFACTOR, task_role=TaskType.REFACTOR
        )
        assert (
            d_none.selected_model_profile_id == d_same.selected_model_profile_id
        )

    def test_weights_for_is_the_selector(self):
        # Guard: the override path selects weights via weights_for.
        assert weights_for(TaskType.SECURITY_FIX) == RoleWeights(0.05, 0.95)
        assert weights_for(TaskType.DOCUMENTATION) == RoleWeights(0.40, 0.60)
