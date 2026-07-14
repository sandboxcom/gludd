"""Unit tests for scoring/pareto.py — G8 Pareto frontier router."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.scoring.pareto import ParetoRouter


def _make_candidate(
    idx: int | str, cost: float, quality: float
) -> dict[str, Any]:
    return {"id": str(idx), "name": f"model-{idx}", "cost": cost, "quality": quality}


class TestParetoRouterConstruction:
    def test_default_weights(self) -> None:
        router = ParetoRouter()
        assert router._cost_weight == 0.5
        assert router._quality_weight == 0.5

    def test_custom_weights(self) -> None:
        router = ParetoRouter(cost_weight=0.8, quality_weight=0.2)
        assert router._cost_weight == 0.8
        assert router._quality_weight == 0.2


class TestRouteByParetoFrontier:
    def test_empty_candidates(self) -> None:
        router = ParetoRouter()
        assert router.route_by_pareto_frontier([]) == []

    def test_single_candidate(self) -> None:
        router = ParetoRouter()
        candidates = [_make_candidate("a", 1.0, 0.9)]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "a"

    def test_two_candidates_one_dominates(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate("cheap_better", 0.1, 0.9),
            _make_candidate("expensive_worse", 0.5, 0.5),
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "cheap_better"

    def test_two_on_frontier(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate("cheap_ok", 0.1, 0.5),
            _make_candidate("expensive_good", 0.5, 0.9),
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 2

    def test_all_equal_candidates(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate("a", 0.5, 0.5),
            _make_candidate("b", 0.5, 0.5),
            _make_candidate("c", 0.5, 0.5),
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 3

    def test_nan_cost_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate("good", 0.5, 0.5),
            {"id": "bad", "name": "bad", "cost": float("nan"), "quality": 0.9},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "good"

    def test_inf_quality_excluded(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate("good", 0.5, 0.5),
            {"id": "bad", "name": "bad", "cost": 0.1, "quality": float("inf")},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "good"

    def test_all_invalid_candidates(self) -> None:
        router = ParetoRouter()
        candidates = [
            {"id": "a", "cost": float("nan"), "quality": 0.5},
            {"id": "b", "cost": 0.5, "quality": float("inf")},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert result == []

    def test_result_sorted_by_quality_desc(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate("low", 0.2, 0.3),
            _make_candidate("high", 0.2, 0.9),
            _make_candidate("mid", 0.2, 0.6),
        ]
        result = router.route_by_pareto_frontier(candidates)
        qualities = [r["quality"] for r in result]
        assert qualities == sorted(qualities, reverse=True)


class TestPickWinner:
    def test_empty_frontier_returns_none(self) -> None:
        router = ParetoRouter()
        assert router.pick_winner([]) is None

    def test_single_frontier_candidate(self) -> None:
        router = ParetoRouter()
        frontier = [_make_candidate("a", 0.5, 0.5)]
        result = router.pick_winner(frontier)
        assert result is not None
        assert result["id"] == "a"

    def test_prefers_higher_quality(self) -> None:
        router = ParetoRouter(cost_weight=0.0, quality_weight=1.0)
        frontier = [
            _make_candidate("low_q", 0.1, 0.3),
            _make_candidate("high_q", 0.1, 0.9),
        ]
        result = router.pick_winner(frontier)
        assert result is not None
        assert result["id"] == "high_q"

    def test_prefers_lower_cost(self) -> None:
        router = ParetoRouter(cost_weight=1.0, quality_weight=0.0)
        frontier = [
            _make_candidate("expensive", 0.8, 0.5),
            _make_candidate("cheap", 0.2, 0.5),
        ]
        result = router.pick_winner(frontier)
        assert result is not None
        assert result["id"] == "cheap"

    def test_override_weights_per_call(self) -> None:
        router = ParetoRouter(cost_weight=0.0, quality_weight=1.0)
        frontier = [
            _make_candidate("expensive", 0.8, 0.5),
            _make_candidate("cheap", 0.2, 0.5),
        ]
        result = router.pick_winner(frontier, cost_weight=1.0, quality_weight=0.0)
        assert result is not None
        assert result["id"] == "cheap"

    def test_all_equal_scores_picks_one(self) -> None:
        router = ParetoRouter()
        frontier = [
            _make_candidate("a", 0.5, 0.5),
            _make_candidate("b", 0.5, 0.5),
        ]
        result = router.pick_winner(frontier)
        assert result is not None
        assert result["id"] in ("a", "b")


class TestPickWinnerForTask:
    def test_delegates_to_pick_winner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from collections import namedtuple

        RoleWeights = namedtuple("RoleWeights", ["cost", "quality"])

        def mock_weights_for(task_type: Any) -> RoleWeights:
            return RoleWeights(cost=0.7, quality=0.3)

        monkeypatch.setattr(
            "general_ludd.routing_roles.weights.weights_for", mock_weights_for,
        )

        router = ParetoRouter()
        frontier = [
            _make_candidate("cheap", 0.1, 0.5),
            _make_candidate("expensive", 0.8, 0.5),
        ]
        result = router.pick_winner_for_task(frontier, "bug_fix")  # type: ignore[arg-type]
        assert result is not None
        assert result["id"] == "cheap"


class TestParetoFrontierEdgeCases:
    def test_three_candidates_middle_dominated(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate("best", 0.1, 0.6),
            _make_candidate("tradeoff", 0.5, 0.9),
            _make_candidate("dominated", 0.5, 0.5),
        ]
        result = router.route_by_pareto_frontier(candidates)
        ids = {r["id"] for r in result}
        assert "best" in ids
        assert "tradeoff" in ids
        assert "dominated" not in ids

    def test_cost_equal_quality_better_breaks_tie(self) -> None:
        router = ParetoRouter()
        candidates = [
            _make_candidate("good", 0.5, 0.8),
            _make_candidate("bad", 0.5, 0.4),
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "good"

    def test_missing_cost_key(self) -> None:
        router = ParetoRouter()
        candidates = [
            {"id": "no_cost", "quality": 0.5},
            _make_candidate("good", 0.5, 0.5),
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "good"

    def test_missing_quality_key(self) -> None:
        router = ParetoRouter()
        candidates = [
            {"id": "no_quality", "cost": 0.5},
            _make_candidate("good", 0.5, 0.5),
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "good"
