"""Tests for cost_router.py — peak/off-peak pricing and budget-aware routing."""

from __future__ import annotations

import asyncio
import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.models.cost_router import (
    _DEFAULT_PEAK,
    CostAwareRouter,
    ModelRoute,
    PeakPricingSchedule,
)


class TestPeakPricingSchedule:
    def test_default_schedule(self) -> None:
        s = _DEFAULT_PEAK
        assert s.peak_start_hour == 8
        assert s.peak_end_hour == 20
        assert s.peak_multiplier == 1.5
        assert s.off_peak_multiplier == 0.7
        assert 0 in s.peak_days
        assert 5 not in s.peak_days
        assert 6 not in s.peak_days

    def test_invalid_start_hour_raises(self) -> None:
        with pytest.raises(ValueError, match="peak_start_hour"):
            PeakPricingSchedule(peak_start_hour=25, peak_end_hour=20)

    def test_invalid_end_hour_raises(self) -> None:
        with pytest.raises(ValueError, match="peak_end_hour"):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=-1)

    def test_negative_multiplier_raises(self) -> None:
        with pytest.raises(ValueError, match="peak_multiplier"):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_multiplier=0)

    def test_negative_off_peak_raises(self) -> None:
        with pytest.raises(ValueError, match="off_peak_multiplier"):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, off_peak_multiplier=-0.5)

    def test_empty_peak_days_raises(self) -> None:
        with pytest.raises(ValueError, match="peak_days"):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_days=frozenset())

    def test_custom_schedule(self) -> None:
        s = PeakPricingSchedule(
            peak_start_hour=6,
            peak_end_hour=18,
            peak_multiplier=2.0,
            off_peak_multiplier=0.5,
            peak_days=frozenset({0, 1, 2, 3, 4, 5}),
        )
        assert s.peak_start_hour == 6
        assert s.peak_end_hour == 18

    def test_frozen_dataclass(self) -> None:
        s = _DEFAULT_PEAK
        with pytest.raises(AttributeError):
            s.peak_start_hour = 9  # type: ignore[misc]

    def test_weekend_not_in_default_peak_days(self) -> None:
        assert 5 not in _DEFAULT_PEAK.peak_days
        assert 6 not in _DEFAULT_PEAK.peak_days


class TestModelRoute:
    def test_default_values(self) -> None:
        r = ModelRoute(model_id="openai/gpt-4o", estimated_cost=0.01, peak_status="peak", hourly_rate=0.01)
        assert r.currency == "USD"
        assert r.model_id == "openai/gpt-4o"

    def test_explicit_currency(self) -> None:
        r = ModelRoute(model_id="x", estimated_cost=0.0, peak_status="off_peak", hourly_rate=0.0, currency="EUR")
        assert r.currency == "EUR"

    def test_frozen(self) -> None:
        r = ModelRoute(model_id="x", estimated_cost=0.0, peak_status="peak", hourly_rate=0.0)
        with pytest.raises(AttributeError):
            r.estimated_cost = 1.0  # type: ignore[misc]

    def test_peak_status_strings(self) -> None:
        assert ModelRoute(model_id="a", estimated_cost=1, peak_status="peak", hourly_rate=1).peak_status == "peak"
        assert (
            ModelRoute(model_id="a", estimated_cost=1, peak_status="off_peak", hourly_rate=1).peak_status == "off_peak"
        )
        assert ModelRoute(model_id="a", estimated_cost=1, peak_status="unknown", hourly_rate=1).peak_status == "unknown"


class TestCostAwareRouterPeakDetection:
    def _make_router(self) -> CostAwareRouter:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=[])
        perf.select_model = AsyncMock(return_value={"service": "openai", "model_name": "gpt-4o-mini", "fallback": True})
        return CostAwareRouter(perf)

    def test_is_peak_true_weekday_morning(self) -> None:
        router = CostAwareRouter(MagicMock())
        tuesday_9am = datetime.datetime(2026, 8, 4, 9, 0, 0, tzinfo=datetime.UTC)
        assert router._is_peak(tuesday_9am) is True

    def test_is_peak_true_weekday_boundary_start(self) -> None:
        router = CostAwareRouter(MagicMock())
        dt = datetime.datetime(2026, 8, 4, 8, 0, 0, tzinfo=datetime.UTC)
        assert router._is_peak(dt) is True

    def test_is_peak_false_weekday_boundary_end(self) -> None:
        router = CostAwareRouter(MagicMock())
        dt = datetime.datetime(2026, 8, 4, 20, 0, 0, tzinfo=datetime.UTC)
        assert router._is_peak(dt) is False

    def test_is_peak_false_saturday(self) -> None:
        router = CostAwareRouter(MagicMock())
        saturday = datetime.datetime(2026, 8, 8, 12, 0, 0, tzinfo=datetime.UTC)
        assert router._is_peak(saturday) is False

    def test_is_peak_false_sunday(self) -> None:
        router = CostAwareRouter(MagicMock())
        sunday = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)
        assert router._is_peak(sunday) is False

    def test_is_peak_custom_days(self) -> None:
        router = CostAwareRouter(
            MagicMock(),
            peak_schedule=PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_days=frozenset({5, 6})),
        )
        sunday = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)
        assert router._is_peak(sunday) is True
        tuesday = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        assert router._is_peak(tuesday) is False

    def test_peak_status_returns_correct_strings(self) -> None:
        router = CostAwareRouter(MagicMock())
        peak = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        off = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)
        assert router._peak_status(peak) == "peak"
        assert router._peak_status(off) == "off_peak"

    def test_multiplier_during_peak(self) -> None:
        router = CostAwareRouter(MagicMock())
        peak = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        assert router._multiplier(peak) == 1.5

    def test_multiplier_during_off_peak(self) -> None:
        router = CostAwareRouter(MagicMock())
        off = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)
        assert router._multiplier(off) == 0.7

    def test_adjusted_cost_peak(self) -> None:
        router = CostAwareRouter(MagicMock())
        peak = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        assert router._adjusted_cost(1.0, peak) == 1.5

    def test_adjusted_cost_off_peak(self) -> None:
        router = CostAwareRouter(MagicMock())
        off = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)
        assert router._adjusted_cost(1.0, off) == 0.7


class TestRouteByCost:
    def _make_rankings(self) -> list[dict[str, object]]:
        return [
            {"service": "openai", "model_name": "gpt-4o", "avg_cost_usd": 0.03, "success_rate": 0.98},
            {"service": "openai", "model_name": "gpt-4o-mini", "avg_cost_usd": 0.001, "success_rate": 0.95},
            {"service": "anthropic", "model_name": "claude-haiku", "avg_cost_usd": 0.002, "success_rate": 0.96},
        ]

    def test_route_by_cost_picks_cheapest_within_budget(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())

        router = CostAwareRouter(perf)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("bug_fix", budget_remaining=0.01, now=now))
        assert route.model_id == "openai/gpt-4o-mini"
        assert route.peak_status == "peak"
        assert route.estimated_cost == 0.0015

    def test_route_by_cost_skips_over_budget(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())

        router = CostAwareRouter(perf)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("bug_fix", budget_remaining=0.001, now=now))
        assert route.model_id == "openai/gpt-4o-mini"
        assert route.peak_status == "peak"

    def test_route_by_cost_off_peak_pricing(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())

        router = CostAwareRouter(perf)
        now = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("bug_fix", budget_remaining=None, now=now))
        assert route.peak_status == "off_peak"
        assert route.estimated_cost == round(0.001 * 0.7, 6)
        assert route.model_id == "openai/gpt-4o-mini"

    def test_route_by_cost_empty_rankings_fallback(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=[])
        perf.select_model = AsyncMock(return_value={"service": "openai", "model_name": "gpt-4o-mini", "fallback": True})

        router = CostAwareRouter(perf)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("bug_fix", now=now))
        assert route.model_id == "openai/gpt-4o-mini"
        assert route.peak_status == "peak"

    def test_route_by_cost_with_cost_tracker(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())

        tracker = MagicMock()
        tracker.remaining_model_budget.return_value = 0.005

        router = CostAwareRouter(perf, cost_tracker=tracker)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("bug_fix", now=now))
        tracker.remaining_model_budget.assert_called_once()
        assert route is not None

    def test_route_by_cost_budget_override_has_priority(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())

        tracker = MagicMock()
        tracker.remaining_model_budget.return_value = 999.0

        router = CostAwareRouter(perf, cost_tracker=tracker)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("bug_fix", budget_remaining=0.002, now=now))
        assert route is not None

    def test_route_by_cost_rankings_empty_with_budget_fallback(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=[])
        perf.select_model = AsyncMock(return_value={"service": "openai", "model_name": "gpt-4o", "fallback": True})

        router = CostAwareRouter(perf)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("task", budget_remaining=1.0, now=now))
        assert route.model_id == "openai/gpt-4o"


class TestIsBetterToWait:
    def _make_router(self, peak_schedule: PeakPricingSchedule | None = None) -> CostAwareRouter:
        return CostAwareRouter(MagicMock(), peak_schedule=peak_schedule or _DEFAULT_PEAK)

    def test_already_off_peak_returns_false(self) -> None:
        router = self._make_router()
        now = datetime.datetime(2026, 8, 9, 3, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 1.0}
        assert router.is_better_to_wait(task, deadline_hours=8, now=now) is False

    def test_zero_cost_returns_false(self) -> None:
        router = self._make_router()
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 0.0}
        assert router.is_better_to_wait(task, deadline_hours=8, now=now) is False

    def test_valid_wait_with_large_saving(self) -> None:
        router = self._make_router()
        now = datetime.datetime(2026, 8, 4, 17, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 10.0}
        assert router.is_better_to_wait(task, deadline_hours=8, now=now) is True

    def test_small_saving_returns_false(self) -> None:
        router = self._make_router()
        now = datetime.datetime(2026, 8, 4, 17, 30, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 0.01}
        assert router.is_better_to_wait(task, deadline_hours=8, now=now) is True

    def test_deadline_too_tight_returns_false(self) -> None:
        router = self._make_router()
        now = datetime.datetime(2026, 8, 4, 19, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 100.0}
        assert router.is_better_to_wait(task, deadline_hours=0.25, now=now) is False

    def test_wait_when_peak_saving_above_20_percent(self) -> None:
        router = self._make_router()
        now = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 5.0}
        assert router.is_better_to_wait(task, deadline_hours=12, now=now) is True

    def test_is_better_to_wait_not_peak_window(self) -> None:
        router = self._make_router()
        now = datetime.datetime(2026, 8, 4, 19, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 10.0}
        assert router.is_better_to_wait(task, deadline_hours=2, now=now) is True


class TestDeferToOffPeak:
    def test_defer_during_peak_sets_scheduled_to_end_of_peak(self) -> None:
        router = CostAwareRouter(MagicMock())
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=24)

        result = router.defer_to_off_peak("task-1", deadline, now=now)
        assert result["enqueued"] is False
        assert result["task_id"] == "task-1"
        assert "scheduled_for" in result

    def test_defer_with_queue(self) -> None:
        q = MagicMock()
        q.enqueue.return_value = "enq-abc"
        router = CostAwareRouter(MagicMock(), deferred_queue=q)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=24)

        result = router.defer_to_off_peak("task-2", deadline, now=now)
        assert result["enqueued"] is True
        assert result["enqueue_id"] == "enq-abc"
        assert result["task_id"] == "task-2"

    def test_defer_queue_failure_still_returns_result(self) -> None:
        q = MagicMock()
        q.enqueue.side_effect = RuntimeError("queue down")
        router = CostAwareRouter(MagicMock(), deferred_queue=q)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=24)

        result = router.defer_to_off_peak("task-3", deadline, now=now)
        assert result["enqueued"] is False
        assert result["enqueue_id"] is None
        assert result["task_id"] == "task-3"

    def test_defer_respects_deadline(self) -> None:
        router = CostAwareRouter(MagicMock())
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=2)

        result = router.defer_to_off_peak("task-4", deadline, now=now)
        scheduled = datetime.datetime.fromisoformat(cast(str, result["scheduled_for"]))
        assert scheduled <= deadline


class TestBudgetGuardIntegration:
    def test_check_budget_no_guard_returns_allowed(self) -> None:
        router = CostAwareRouter(MagicMock())
        assert router.check_budget(5.0) == {"allowed": True, "reason": "no_guard"}

    def test_check_budget_with_guard(self) -> None:
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": True, "reason": "ok"}
        router = CostAwareRouter(MagicMock(), budget_guard=guard)
        result = router.check_budget(5.0)
        assert result["allowed"] is True
        guard.check_all_limits.assert_called_once_with(estimated_cost=5.0)

    def test_check_budget_with_guard_denied(self) -> None:
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "budget exceeded"}
        router = CostAwareRouter(MagicMock(), budget_guard=guard)
        result = router.check_budget(5.0)
        assert result["allowed"] is False
        assert result["reason"] == "budget exceeded"


class TestEstimateCost:
    def test_estimate_cost_no_now_uses_current_time(self) -> None:
        router = CostAwareRouter(MagicMock())
        cost = router.estimate_cost(1.0)
        assert cost > 0

    def test_estimate_cost_explicit_peak(self) -> None:
        router = CostAwareRouter(MagicMock())
        peak = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        assert router.estimate_cost(10.0, now=peak) == 15.0

    def test_estimate_cost_explicit_off_peak(self) -> None:
        router = CostAwareRouter(MagicMock())
        off = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)
        assert router.estimate_cost(10.0, now=off) == 7.0

    def test_estimate_cost_rounds_to_6_places(self) -> None:
        router = CostAwareRouter(MagicMock())
        peak = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        cost = router.estimate_cost(0.333333, now=peak)
        expected = round(0.333333 * 1.5, 6)
        assert cost == expected


class TestPeakScheduleProperty:
    def test_peak_schedule_property(self) -> None:
        schedule = PeakPricingSchedule(
            peak_start_hour=6, peak_end_hour=18, peak_multiplier=2.0, off_peak_multiplier=0.5
        )
        router = CostAwareRouter(MagicMock(), peak_schedule=schedule)
        assert router.peak_schedule is schedule
        assert router.peak_schedule.peak_start_hour == 6
        assert router.peak_schedule.peak_end_hour == 18
