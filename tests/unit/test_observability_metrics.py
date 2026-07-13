"""Observability metrics unit tests — metric pipeline, ParetoRouter, health endpoint."""

from __future__ import annotations

import math
import time
from typing import Any

import pytest

from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutEvent,
    TimeoutKind,
)
from general_ludd.scoring.metric import MetricConfig, compute_w_dollar
from general_ludd.scoring.pareto import ParetoRouter


class TestMetricPipeline:
    def test_w_dollar_clamped_by_floor(self):
        config = MetricConfig(score_floor=0.5)
        result = compute_w_dollar(composite_score=0.1, median_dollars_per_mtok=1000.0, config=config)
        assert result == 0.5

    def test_w_dollar_clamped_by_ceiling(self):
        config = MetricConfig(score_ceiling=0.5)
        result = compute_w_dollar(composite_score=1.0, median_dollars_per_mtok=0.001, config=config)
        assert result == 0.5

    def test_w_dollar_with_non_default_log_base(self):
        config = MetricConfig(log_base=2.0)
        w_dollar = compute_w_dollar(composite_score=1.0, median_dollars_per_mtok=3.0, config=config)
        expected = 1.0 / (math.log(4.0) / math.log(2.0))
        assert abs(w_dollar - expected) < 0.0001

    def test_w_dollar_offset_prevents_zero_denom(self):
        config = MetricConfig(offset=1.0)
        result = compute_w_dollar(composite_score=0.5, median_dollars_per_mtok=0.0, config=config)
        assert result >= 0.0

    def test_w_dollar_identity_at_offset_one_zero_cost(self):
        result = compute_w_dollar(composite_score=0.75, median_dollars_per_mtok=0.0)
        assert result == pytest.approx(0.75)

    def test_w_dollar_fractional_cost_precision(self):
        result = compute_w_dollar(composite_score=0.8, median_dollars_per_mtok=0.01)
        assert result > 1.0

    def test_w_dollar_very_high_cost_approaches_floor(self):
        config = MetricConfig(score_floor=0.0)
        result = compute_w_dollar(composite_score=1.0, median_dollars_per_mtok=1e9, config=config)
        assert 0.0 < result < 0.15

    def test_w_dollar_is_monotonic_in_composite_score(self):
        score_a = compute_w_dollar(composite_score=0.3, median_dollars_per_mtok=10.0)
        score_b = compute_w_dollar(composite_score=0.7, median_dollars_per_mtok=10.0)
        assert score_b > score_a

    def test_w_dollar_decreases_as_cost_increases(self):
        cheap = compute_w_dollar(composite_score=0.9, median_dollars_per_mtok=0.01)
        expensive = compute_w_dollar(composite_score=0.9, median_dollars_per_mtok=100.0)
        assert cheap > expensive

    def test_metric_config_immutable(self):
        config = MetricConfig(log_base=10, offset=1)
        assert config.__dataclass_params__.frozen


class TestParetoRouterFrontier:
    def test_empty_candidates_returns_empty(self):
        router = ParetoRouter()
        assert router.route_by_pareto_frontier([]) == []

    def test_single_candidate_returns_itself(self):
        router = ParetoRouter()
        cand = [{"id": "a", "cost": 1.0, "quality": 0.9}]
        result = router.route_by_pareto_frontier(cand)
        assert result == cand

    def test_all_equal_candidates_all_returned(self):
        router = ParetoRouter()
        candidates = [
            {"id": "a", "cost": 1.0, "quality": 0.8},
            {"id": "b", "cost": 1.0, "quality": 0.8},
            {"id": "c", "cost": 1.0, "quality": 0.8},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 3

    def test_dominated_candidate_excluded(self):
        router = ParetoRouter()
        candidates = [
            {"id": "a", "cost": 1.0, "quality": 0.9},
            {"id": "b", "cost": 2.0, "quality": 0.5},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "a"

    def test_non_finite_values_excluded(self):
        router = ParetoRouter()
        candidates = [
            {"id": "a", "cost": 1.0, "quality": 0.8},
            {"id": "b", "cost": float("nan"), "quality": 0.9},
            {"id": "c", "cost": 1.5, "quality": float("inf")},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "a"

    def test_frontier_sorted_by_quality_desc(self):
        router = ParetoRouter()
        candidates = [
            {"id": "a", "cost": 8.0, "quality": 0.95},
            {"id": "b", "cost": 1.0, "quality": 0.85},
            {"id": "c", "cost": 2.0, "quality": 0.75},
            {"id": "d", "cost": 3.0, "quality": 0.65},
            {"id": "e", "cost": 5.0, "quality": 0.55},
        ]
        result = router.route_by_pareto_frontier(candidates)
        qualities = [r["quality"] for r in result]
        assert qualities == sorted(qualities, reverse=True)

    def test_all_invalid_values_returns_empty(self):
        router = ParetoRouter()
        candidates = [
            {"id": "a", "cost": "nope", "quality": None},
            {"id": "b", "cost": float("nan"), "quality": float("nan")},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert result == []

    def test_missing_cost_defaults_to_nan(self):
        router = ParetoRouter()
        candidates = [
            {"id": "a", "cost": 1.0, "quality": 0.8},
            {"id": "b", "quality": 0.9},
        ]
        result = router.route_by_pareto_frontier(candidates)
        assert len(result) == 1
        assert result[0]["id"] == "a"


class TestParetoRouterPickWinner:
    def test_empty_frontier_returns_none(self):
        router = ParetoRouter()
        assert router.pick_winner([]) is None

    def test_single_element_frontier_returns_it(self):
        router = ParetoRouter()
        cand = {"id": "a", "cost": 1.0, "quality": 0.9}
        assert router.pick_winner([cand]) == cand

    def test_pick_winner_per_call_overrides(self):
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        frontier: list[dict[str, Any]] = [
            {"id": "a", "cost": 1.0, "quality": 0.9},
            {"id": "b", "cost": 0.1, "quality": 0.9},
        ]
        result = router.pick_winner(frontier, cost_weight=0.0, quality_weight=1.0)
        assert result is not None

    def test_pick_winner_uniform_costs_picks_best_quality(self):
        router = ParetoRouter(cost_weight=0.5, quality_weight=0.5)
        frontier: list[dict[str, Any]] = [
            {"id": "a", "cost": 1.0, "quality": 0.9},
            {"id": "b", "cost": 1.0, "quality": 0.5},
        ]
        result = router.pick_winner(frontier)
        assert result is not None
        assert result["id"] == "a"


class TestModelHealthTracker:
    def test_initial_state_is_healthy(self):
        tracker = ModelHealthTracker()
        assert tracker.is_healthy("model-1")

    def test_healthy_model_get_health_dict(self):
        tracker = ModelHealthTracker()
        health = tracker.get_health("model-1")
        assert health["model_id"] == "model-1"
        assert health["healthy"] is True
        assert health["consecutive_failures"] == 0
        assert health["total_failures"] == 0
        assert health["last_failure_kind"] is None
        assert health["last_failure_at"] is None

    def test_failures_below_threshold_remain_healthy(self):
        tracker = ModelHealthTracker(failure_threshold=3)
        for _i in range(2):
            tracker.record_event(
                TimeoutEvent(
                    model_id="m1",
                    kind=TimeoutKind.CONNECTION_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=5.0,
                )
            )
        assert tracker.is_healthy("m1")

    def test_failures_at_threshold_mark_unhealthy(self):
        tracker = ModelHealthTracker(failure_threshold=2)
        for _i in range(2):
            tracker.record_event(
                TimeoutEvent(
                    model_id="m1",
                    kind=TimeoutKind.CONNECTION_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=5.0,
                )
            )
        assert not tracker.is_healthy("m1")

    def test_record_success_resets_consecutive(self):
        tracker = ModelHealthTracker(failure_threshold=2)
        tracker.record_event(
            TimeoutEvent(
                model_id="m1",
                kind=TimeoutKind.CONNECTION_TIMEOUT,
                timestamp=time.monotonic(),
                duration_s=1.0,
            )
        )
        tracker.record_success("m1")
        assert tracker.is_healthy("m1")
        health = tracker.get_health("m1")
        assert health["consecutive_failures"] == 0

    def test_auth_error_never_marks_unhealthy(self):
        tracker = ModelHealthTracker(failure_threshold=1)
        for _i in range(5):
            tracker.record_event(
                TimeoutEvent(
                    model_id="m1",
                    kind=TimeoutKind.AUTH_ERROR,
                    timestamp=time.monotonic(),
                    duration_s=0.1,
                )
            )
        assert tracker.is_healthy("m1")

    def test_context_length_never_marks_unhealthy(self):
        tracker = ModelHealthTracker(failure_threshold=1)
        for _i in range(5):
            tracker.record_event(
                TimeoutEvent(
                    model_id="m1",
                    kind=TimeoutKind.CONTEXT_LENGTH,
                    timestamp=time.monotonic(),
                    duration_s=0.1,
                )
            )
        assert tracker.is_healthy("m1")

    def test_cooldown_elapsed_admits_probe(self):
        tracker = ModelHealthTracker(failure_threshold=1, cooldown_seconds=0.1)
        now = time.monotonic()
        tracker.record_event(
            TimeoutEvent(
                model_id="m1",
                kind=TimeoutKind.CONNECTION_TIMEOUT,
                timestamp=now,
                duration_s=1.0,
            )
        )
        assert not tracker.is_healthy("m1")
        time.sleep(0.15)
        assert tracker.is_healthy("m1")

    def test_second_probe_blocked_in_same_window(self):
        tracker = ModelHealthTracker(failure_threshold=1, cooldown_seconds=0.1)
        now = time.monotonic()
        tracker.record_event(
            TimeoutEvent(
                model_id="m1",
                kind=TimeoutKind.CONNECTION_TIMEOUT,
                timestamp=now,
                duration_s=1.0,
            )
        )
        time.sleep(0.15)
        assert tracker.is_healthy("m1")
        assert not tracker.is_healthy("m1")

    def test_failure_re_arms_breaker(self):
        tracker = ModelHealthTracker(failure_threshold=1, cooldown_seconds=0.1)
        now = time.monotonic()
        tracker.record_event(
            TimeoutEvent(
                model_id="m1",
                kind=TimeoutKind.CONNECTION_TIMEOUT,
                timestamp=now,
                duration_s=1.0,
            )
        )
        time.sleep(0.15)
        assert tracker.is_healthy("m1")
        tracker.record_event(
            TimeoutEvent(
                model_id="m1",
                kind=TimeoutKind.CONNECTION_TIMEOUT,
                timestamp=time.monotonic(),
                duration_s=1.0,
            )
        )
        assert not tracker.is_healthy("m1")
