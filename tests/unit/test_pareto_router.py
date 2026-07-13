"""Unit tests for G8: ParetoRouter — cost/quality Pareto frontier routing."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.pareto import ParetoRouter
from general_ludd.scoring.router import AdaptiveRouter


class TestParetoRouterConstruction:
    def test_constructor_defaults(self) -> None:
        router = ParetoRouter()
        assert router._cost_weight == 0.5
        assert router._quality_weight == 0.5

    def test_constructor_custom_weights(self) -> None:
        router = ParetoRouter(cost_weight=0.3, quality_weight=0.7)
        assert router._cost_weight == 0.3
        assert router._quality_weight == 0.7


class TestParetoFrontierDomination:
    def test_clear_domination_one_dominates_others(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.01, "quality": 0.95},
            {"model": "b", "cost": 0.02, "quality": 0.80},
            {"model": "c", "cost": 0.03, "quality": 0.70},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"

    def test_no_domination_all_frontier(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.01, "quality": 0.80},
            {"model": "b", "cost": 0.05, "quality": 0.95},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 2
        models = {r["model"] for r in result}
        assert models == {"a", "b"}

    def test_dominated_model_excluded(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "cheap_good", "cost": 0.10, "quality": 0.90},
            {"model": "expensive_bad", "cost": 0.50, "quality": 0.50},
            {"model": "cheap_best", "cost": 0.05, "quality": 0.95},
        ]
        result = router.route_by_pareto_frontier(candidates)
        models = {r["model"] for r in result}
        assert "expensive_bad" not in models
        assert "cheap_good" not in models
        assert models == {"cheap_best"}

    def test_partial_domination_three_candidates(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "b", "cost": 0.20, "quality": 0.85},
            {"model": "c", "cost": 0.15, "quality": 0.95},
        ]
        result = router.route_by_pareto_frontier(candidates)
        models = {r["model"] for r in result}
        assert "b" not in models
        assert models == {"a", "c"}

    def test_tie_on_one_axis_same_cost(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "b", "cost": 0.10, "quality": 0.70},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"

    def test_tie_on_quality_different_cost(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "b", "cost": 0.05, "quality": 0.90},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "b"


class TestParetoFrontierEdgeCases:
    def test_empty_list(self) -> None:
        router = ParetoRouter()
        assert router.route_by_pareto_frontier([]) == []

    def test_single_candidate(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "only", "cost": 0.01, "quality": 0.90},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "only"

    def test_all_equal_candidates_all_frontier(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.05, "quality": 0.90},
            {"model": "b", "cost": 0.05, "quality": 0.90},
            {"model": "c", "cost": 0.05, "quality": 0.90},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 3

    def test_nan_cost_excluded(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "nan_cost", "cost": float("nan"), "quality": 0.95},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"

    def test_nan_quality_excluded(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "nan_qual", "cost": 0.05, "quality": float("nan")},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"

    def test_inf_cost_excluded(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "inf_cost", "cost": float("inf"), "quality": 0.95},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"

    def test_neg_inf_cost_excluded(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "neginf_cost", "cost": float("-inf"), "quality": 0.95},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"

    def test_inf_quality_excluded(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "inf_qual", "cost": 0.05, "quality": float("inf")},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"

    def test_all_nan_or_inf_returns_empty(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "nan", "cost": float("nan"), "quality": 0.90},
            {"model": "inf", "cost": 0.10, "quality": float("inf")},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert result == []

    def test_missing_cost_excluded(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "no_cost", "quality": 0.95},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"


class TestParetoFrontierOrdering:
    def test_quality_descending_order(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "low_cost", "cost": 0.01, "quality": 0.70},
            {"model": "high_cost", "cost": 0.50, "quality": 0.99},
            {"model": "mid", "cost": 0.10, "quality": 0.85},
        ]
        result = router.route_by_pareto_frontier(candidates)
        qualities = [float(r["quality"]) for r in result]
        assert qualities == sorted(qualities, reverse=True)

    def test_cheapest_and_best_quality_always_frontier(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "cheapest", "cost": 0.01, "quality": 0.60},
            {"model": "best_qual", "cost": 0.10, "quality": 0.99},
            {"model": "mid", "cost": 0.05, "quality": 0.80},
        ]
        result = router.route_by_pareto_frontier(candidates)
        models = {r["model"] for r in result}
        assert "cheapest" in models
        assert "best_qual" in models


class TestPickWinner:
    def test_single_candidate_wins(self) -> None:
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        frontier: list[dict[str, Any]] = [
            {"model": "only", "cost": 0.10, "quality": 0.90},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["model"] == "only"

    def test_empty_frontier_returns_none(self) -> None:
        router = ParetoRouter()
        assert router.pick_winner([]) is None

    def test_higher_composite_score_wins(self) -> None:
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        frontier: list[dict[str, Any]] = [
            {"model": "cheap", "cost": 0.01, "quality": 0.70},
            {"model": "expensive", "cost": 0.10, "quality": 0.95},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["model"] in ("cheap", "expensive")

    def test_quality_weight_dominant(self) -> None:
        router = ParetoRouter(cost_weight=0.01, quality_weight=0.99)
        frontier: list[dict[str, Any]] = [
            {"model": "cheap_lowq", "cost": 0.01, "quality": 0.50},
            {"model": "expensive_highq", "cost": 0.10, "quality": 0.99},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["model"] == "expensive_highq"

    def test_cost_weight_dominant(self) -> None:
        router = ParetoRouter(cost_weight=0.99, quality_weight=0.01)
        frontier: list[dict[str, Any]] = [
            {"model": "cheap_lowq", "cost": 0.01, "quality": 0.50},
            {"model": "expensive_highq", "cost": 0.10, "quality": 0.99},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["model"] == "cheap_lowq"

    def test_all_equal_composite_picks_first(self) -> None:
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        frontier: list[dict[str, Any]] = [
            {"model": "first", "cost": 0.05, "quality": 0.80},
            {"model": "second", "cost": 0.05, "quality": 0.80},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["model"] == "first"


class TestAdaptiveRouterParetoIntegration:
    @staticmethod
    def _make_agg(
        model: str,
        sample_count: int = 10,
        composite: float = 0.85,
        avg_cost: float = 0.01,
    ) -> dict[str, Any]:
        return {
            "model_profile_id": model,
            "prompt_profile_id": "default",
            "sample_count": sample_count,
            "composite_score": composite,
            "avg_cost": avg_cost,
            "task_type": "bug_fix",
        }

    @pytest.mark.asyncio
    async def test_pareto_excludes_dominated_before_ranking(self) -> None:
        pareto = ParetoRouter()
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            self._make_agg("model_a", avg_cost=0.01, composite=0.95),
            self._make_agg("model_b", avg_cost=0.05, composite=0.70),
        ]
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "model_a"

    @pytest.mark.asyncio
    async def test_no_pareto_router_no_change(self) -> None:
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            self._make_agg("model_a", avg_cost=0.01, composite=0.95),
            self._make_agg("model_b", avg_cost=0.05, composite=0.70),
        ]
        router = AdaptiveRouter(benchmark_repo=repo, min_samples=1)
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "model_a"

    @pytest.mark.asyncio
    async def test_pareto_keeps_all_on_frontier(self) -> None:
        pareto = ParetoRouter()
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            self._make_agg("model_a", avg_cost=0.01, composite=0.80),
            self._make_agg("model_b", avg_cost=0.10, composite=0.99),
        ]
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "model_b"

    @pytest.mark.asyncio
    async def test_pareto_skips_when_single_candidate(self) -> None:
        pareto = ParetoRouter()
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            self._make_agg("only", avg_cost=0.01, composite=0.85),
        ]
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "only"

    @pytest.mark.asyncio
    async def test_pareto_with_embeddings_path(self) -> None:
        pareto = ParetoRouter()
        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            self._make_agg("model_a", avg_cost=0.01, composite=0.95),
            self._make_agg("model_b", avg_cost=0.05, composite=0.70),
        ]
        emb_store = MagicMock()
        emb_store.similarity_to = AsyncMock(
            return_value={"code_generation": 0.8, "code_review": 0.5}
        )
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
            embedding_store=emb_store,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "model_a"

    @pytest.mark.asyncio
    async def test_pareto_with_unhealthy_model(self) -> None:
        pareto = ParetoRouter()
        repo = AsyncMock()
        health = MagicMock()
        health.is_healthy = lambda model_id, admit_probe=False: (
            model_id != "sick_model"
        )
        repo.get_aggregate_scores.return_value = [
            self._make_agg("healthy_best", avg_cost=0.01, composite=0.95),
            self._make_agg("sick_model", avg_cost=0.02, composite=0.70),
        ]
        router = AdaptiveRouter(
            benchmark_repo=repo,
            min_samples=1,
            pareto_router=pareto,
            health_tracker=health,
        )
        result = await router.route(TaskType("bug_fix"))
        assert not result.fallback
        assert result.selected_model_profile_id == "healthy_best"


class TestParetoRouterLargeCandidateSet:
    def test_many_candidates_still_finds_frontier(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = []
        for i in range(50):
            cost = 0.01 * (i + 1)
            quality = 0.95 - 0.01 * i
            candidates.append({"model": f"m{i}", "cost": cost, "quality": quality})
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) >= 1
        assert len(result) <= 50

    def test_frontier_never_empty_for_valid_input(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.01, "quality": 0.99},
            {"model": "b", "cost": 0.02, "quality": 0.80},
            {"model": "c", "cost": 0.03, "quality": 0.70},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) >= 1

    def test_quality_descending_on_frontier_always(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.01, "quality": 0.50},
            {"model": "b", "cost": 0.50, "quality": 0.99},
            {"model": "c", "cost": 0.10, "quality": 0.80},
        ]
        result = router.route_by_pareto_frontier(candidates)
        qualities = [float(r["quality"]) for r in result]
        for i in range(len(qualities) - 1):
            assert qualities[i] >= qualities[i + 1]


class TestParetoRouterPickWinnerRobustness:
    def test_pick_winner_handles_same_cost_range(self) -> None:
        router = ParetoRouter()
        frontier: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.05, "quality": 0.80},
            {"model": "b", "cost": 0.05, "quality": 0.90},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        # when costs are equal, quality should decide
        assert winner["model"] == "b"

    def test_pick_winner_handles_same_quality_range(self) -> None:
        router = ParetoRouter()
        frontier: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.85},
            {"model": "b", "cost": 0.05, "quality": 0.85},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        # when quality is equal, lower cost should win
        assert winner["model"] == "b"

    def test_pick_winner_normalizes_correctly(self) -> None:
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        frontier: list[dict[str, Any]] = [
            {"model": "low_cost", "cost": 0.01, "quality": 0.60},
            {"model": "high_qual", "cost": 0.10, "quality": 0.99},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None

    def test_pick_winner_returns_first_on_tie(self) -> None:
        router = ParetoRouter()
        frontier: list[dict[str, Any]] = [
            {"model": "first", "cost": 0.05, "quality": 0.80},
            {"model": "second", "cost": 0.05, "quality": 0.80},
            {"model": "third", "cost": 0.05, "quality": 0.80},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["model"] == "first"


class TestParetoRouterCostQualityWeights:
    def test_cost_weight_zero_quality_weight_one(self) -> None:
        router = ParetoRouter(cost_weight=0.0, quality_weight=1.0)
        frontier: list[dict[str, Any]] = [
            {"model": "cheap", "cost": 0.01, "quality": 0.50},
            {"model": "expensive", "cost": 0.10, "quality": 0.99},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["model"] == "expensive"

    def test_cost_weight_one_quality_weight_zero(self) -> None:
        router = ParetoRouter(cost_weight=1.0, quality_weight=0.0)
        frontier: list[dict[str, Any]] = [
            {"model": "cheap", "cost": 0.01, "quality": 0.50},
            {"model": "expensive", "cost": 0.10, "quality": 0.99},
        ]
        winner = router.pick_winner(frontier)
        assert winner is not None
        assert winner["model"] == "cheap"


class TestPickWinnerPerCallWeights:
    """Per-call weight overrides (bonus gap: hardcoded 0.5/0.5 → task-aware)."""

    def test_per_call_override_quality_dominant(self) -> None:
        router = ParetoRouter()  # defaults: 0.5, 0.5
        frontier: list[dict[str, Any]] = [
            {"model": "cheap", "cost": 0.01, "quality": 0.50},
            {"model": "expensive", "cost": 0.10, "quality": 0.99},
        ]
        winner = router.pick_winner(
            frontier, cost_weight=0.01, quality_weight=0.99
        )
        assert winner is not None
        assert winner["model"] == "expensive"

    def test_per_call_override_cost_dominant(self) -> None:
        router = ParetoRouter()  # defaults: 0.5, 0.5
        frontier: list[dict[str, Any]] = [
            {"model": "cheap", "cost": 0.01, "quality": 0.50},
            {"model": "expensive", "cost": 0.10, "quality": 0.99},
        ]
        winner = router.pick_winner(
            frontier, cost_weight=0.99, quality_weight=0.01
        )
        assert winner is not None
        assert winner["model"] == "cheap"

    def test_per_call_override_does_not_mutate_instance(self) -> None:
        router = ParetoRouter(cost_weight=0.2, quality_weight=0.8)
        frontier: list[dict[str, Any]] = [
            {"model": "cheap", "cost": 0.01, "quality": 0.50},
            {"model": "expensive", "cost": 0.10, "quality": 0.99},
        ]
        router.pick_winner(frontier, cost_weight=0.99, quality_weight=0.01)
        assert router._cost_weight == 0.2
        assert router._quality_weight == 0.8

    def test_per_call_override_one_param_only(self) -> None:
        router = ParetoRouter(cost_weight=0.2, quality_weight=0.8)
        frontier: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.01, "quality": 0.50},
            {"model": "b", "cost": 0.10, "quality": 0.99},
        ]
        winner = router.pick_winner(frontier, quality_weight=0.99)
        assert winner is not None
        assert winner["model"] == "b"


class TestPickWinnerForTask:
    """Per-task weight lookup via pick_winner_for_task."""

    def test_security_fix_prefers_quality_over_cost(self) -> None:
        router = ParetoRouter()  # defaults: 0.5, 0.5
        frontier: list[dict[str, Any]] = [
            {"model": "cheap", "cost": 0.01, "quality": 0.60},
            {"model": "secure", "cost": 0.15, "quality": 0.99},
        ]
        winner = router.pick_winner_for_task(
            frontier, TaskType.SECURITY_FIX
        )
        assert winner is not None
        # SECURITY_FIX: cost=0.05, quality=0.95 → quality-heavy
        assert winner["model"] == "secure"

    def test_different_tasks_pick_different_winners(self) -> None:
        router = ParetoRouter()
        frontier: list[dict[str, Any]] = [
            {"model": "cheap", "cost": 0.01, "quality": 0.65},
            {"model": "lowq_floor", "cost": 0.03, "quality": 0.50},
            {"model": "expensive", "cost": 0.14, "quality": 0.85},
        ]
        sec_winner = router.pick_winner_for_task(
            frontier, TaskType.SECURITY_FIX
        )
        doc_winner = router.pick_winner_for_task(
            frontier, TaskType.DOCUMENTATION
        )
        assert sec_winner is not None
        assert doc_winner is not None
        assert sec_winner != doc_winner

    def test_bug_fix_uses_correct_weights(self) -> None:
        router = ParetoRouter()
        frontier: list[dict[str, Any]] = [
            {"model": "cheap", "cost": 0.02, "quality": 0.75},
            {"model": "good", "cost": 0.08, "quality": 0.95},
        ]
        winner = router.pick_winner_for_task(frontier, TaskType.BUG_FIX)
        assert winner is not None
        # BUG_FIX: cost=0.15, quality=0.85 → mildly quality-leaning
        assert winner["model"] == "good"

    def test_single_candidate_always_wins_regardless_of_weights(self) -> None:
        router = ParetoRouter()
        frontier: list[dict[str, Any]] = [
            {"model": "only", "cost": 0.50, "quality": 0.30},
        ]
        winner = router.pick_winner_for_task(frontier, TaskType.DOCUMENTATION)
        assert winner is not None
        assert winner["model"] == "only"

    def test_empty_frontier_returns_none(self) -> None:
        router = ParetoRouter()
        assert (
            router.pick_winner_for_task([], TaskType.FEATURE) is None
        )


class TestParetoFrontierMixedInvalid:
    def test_some_valid_some_nan_still_works(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "b", "cost": float("nan"), "quality": 0.95},
            {"model": "c", "cost": 0.15, "quality": 0.85},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"

    def test_string_cost_treated_as_nan(self) -> None:
        router = ParetoRouter()
        candidates: list[dict[str, Any]] = [
            {"model": "a", "cost": 0.10, "quality": 0.90},
            {"model": "bad", "cost": "not_a_number", "quality": 0.95},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["model"] == "a"
