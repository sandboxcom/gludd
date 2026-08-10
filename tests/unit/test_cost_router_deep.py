"""Deep edge-case tests for cost_router.py — boundary values, numeric
extremes, missing data, protocol mismatches, and cross-boundary scenarios.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.models.cost_router import (
    _DEFAULT_PEAK,
    CostAwareRouter,
    PeakPricingSchedule,
)

# ── PeakPricingSchedule value-boundary edge cases ────────────────────────────


class TestPeakPricingScheduleBoundaries:
    """Zero, negative, and edge-hour validations beyond basic coverage."""

    def test_peak_start_hour_0_valid(self) -> None:
        s = PeakPricingSchedule(peak_start_hour=0, peak_end_hour=23)
        assert s.peak_start_hour == 0

    def test_peak_start_hour_23_valid(self) -> None:
        s = PeakPricingSchedule(peak_start_hour=23, peak_end_hour=23)
        assert s.peak_start_hour == 23

    def test_peak_start_hour_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="peak_start_hour"):
            PeakPricingSchedule(peak_start_hour=-1, peak_end_hour=20)

    def test_peak_end_hour_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="peak_end_hour"):
            PeakPricingSchedule(peak_start_hour=8, peak_end_hour=-1)

    def test_peak_multiplier_tiny_positive_valid(self) -> None:
        s = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_multiplier=1e-10)
        assert s.peak_multiplier == 1e-10

    def test_peak_multiplier_large_valid(self) -> None:
        s = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_multiplier=1e6)
        assert s.peak_multiplier == 1e6

    def test_off_peak_multiplier_tiny_positive_valid(self) -> None:
        s = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, off_peak_multiplier=1e-10)
        assert s.off_peak_multiplier == 1e-10

    def test_single_day_peak_days(self) -> None:
        s = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_days=frozenset({3}))
        assert s.peak_days == frozenset({3})

    def test_all_seven_days_peak(self) -> None:
        s = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20, peak_days=frozenset(range(7)))
        assert len(s.peak_days) == 7

    def test_frozen_peak_days_immutable(self) -> None:
        s = _DEFAULT_PEAK
        with pytest.raises(AttributeError):
            s.peak_days.add(5)  # type: ignore[union-attr]

    def test_equality_same_values(self) -> None:
        a = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20)
        b = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20)
        assert a == b
        assert hash(a) == hash(b)

    def test_inequality_different_hours(self) -> None:
        a = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20)
        b = PeakPricingSchedule(peak_start_hour=9, peak_end_hour=20)
        assert a != b


# ── shared schedule builders ─────────────────────────────────────────────────

_MON_8AM = datetime.datetime(2026, 8, 3, 8, 0, 0, tzinfo=datetime.UTC)
_MON_8PM = datetime.datetime(2026, 8, 3, 20, 0, 0, tzinfo=datetime.UTC)
_MON_759 = datetime.datetime(2026, 8, 3, 7, 59, 0, tzinfo=datetime.UTC)
_MON_1959 = datetime.datetime(2026, 8, 3, 19, 59, 0, tzinfo=datetime.UTC)
_TUE_MIDNIGHT = datetime.datetime(2026, 8, 4, 0, 0, 0, tzinfo=datetime.UTC)
_TUE_NOON = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)  # peak (weekday 8-20)
_SUN_NOON = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)  # off-peak (weekend)
_FRI_NOON = datetime.datetime(2026, 8, 7, 12, 0, 0, tzinfo=datetime.UTC)
_SAT_NOON = datetime.datetime(2026, 8, 8, 12, 0, 0, tzinfo=datetime.UTC)

_DEFAULT_SCHEDULE = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20)


def _router_with_schedule(
    peak_start_hour: int = 8,
    peak_end_hour: int = 20,
    peak_multiplier: float = 1.5,
    off_peak_multiplier: float = 0.7,
    peak_days: frozenset[int] = frozenset({0, 1, 2, 3, 4}),
) -> CostAwareRouter:
    return CostAwareRouter(
        MagicMock(),
        peak_schedule=PeakPricingSchedule(
            peak_start_hour=peak_start_hour,
            peak_end_hour=peak_end_hour,
            peak_multiplier=peak_multiplier,
            off_peak_multiplier=off_peak_multiplier,
            peak_days=peak_days,
        ),
    )


def _rankings(*pairs: tuple[str, str, float]) -> list[dict[str, object]]:
    return [
        {"service": svc, "model_name": name, "avg_cost_usd": cost, "success_rate": 0.95} for svc, name, cost in pairs
    ]


# ── _is_peak boundary and cross-over edge cases ──────────────────────────────


class TestIsPeakBoundaries:
    def test_exactly_at_peak_start_included(self) -> None:
        router = _router_with_schedule()
        assert router._is_peak(_MON_8AM) is True

    def test_exactly_at_peak_end_excluded(self) -> None:
        router = _router_with_schedule()
        assert router._is_peak(_MON_8PM) is False

    def test_one_minute_before_peak_start(self) -> None:
        router = _router_with_schedule()
        assert router._is_peak(_MON_759) is False

    def test_one_minute_before_peak_end(self) -> None:
        router = _router_with_schedule()
        assert router._is_peak(_MON_1959) is True

    def test_midnight_is_off_peak_with_daytime_schedule(self) -> None:
        router = _router_with_schedule()
        assert router._is_peak(_TUE_MIDNIGHT) is False

    def test_peak_start_midnight_valid(self) -> None:
        router = _router_with_schedule(peak_start_hour=0)
        assert router._is_peak(_TUE_MIDNIGHT) is True

    def test_peak_ending_at_23(self) -> None:
        router = _router_with_schedule(peak_start_hour=20, peak_end_hour=23)
        mon_1030pm = datetime.datetime(2026, 8, 3, 22, 30, 0, tzinfo=datetime.UTC)
        mon_11pm = datetime.datetime(2026, 8, 3, 23, 0, 0, tzinfo=datetime.UTC)
        assert router._is_peak(mon_1030pm) is True
        assert router._is_peak(mon_11pm) is False

    def test_friday_is_peak_saturday_is_not(self) -> None:
        router = _router_with_schedule()
        assert router._is_peak(_FRI_NOON) is True
        assert router._is_peak(_SAT_NOON) is False

    def test_now_none_uses_current_time(self) -> None:
        router = _router_with_schedule()
        result = router._is_peak(None)
        assert isinstance(result, bool)


# ── _multiplier and _adjusted_cost numeric-edge cases ────────────────────────


class TestAdjustedCostBoundaries:
    def test_adjusted_cost_zero(self) -> None:
        router = _router_with_schedule()
        assert router._adjusted_cost(0.0, _TUE_NOON) == 0.0

    def test_adjusted_cost_tiny(self) -> None:
        router = _router_with_schedule()
        result = router._adjusted_cost(1e-10, _TUE_NOON)
        assert result == round(1e-10 * 1.5, 6)

    def test_adjusted_cost_large(self) -> None:
        router = _router_with_schedule()
        result = router._adjusted_cost(1e6, _TUE_NOON)
        assert result == 1.5e6

    def test_adjusted_cost_rounds_to_6_decimal_places(self) -> None:
        router = _router_with_schedule()
        cost = router._adjusted_cost(0.123456789, _TUE_NOON)
        assert cost == round(0.123456789 * 1.5, 6)

    def test_multiplier_via_custom_schedule(self) -> None:
        router = _router_with_schedule(peak_multiplier=3.0, off_peak_multiplier=0.25)
        assert router._multiplier(_TUE_NOON) == 3.0
        assert router._multiplier(_SUN_NOON) == 0.25

    def test_off_peak_multiplier_applied_on_weekend(self) -> None:
        router = _router_with_schedule()
        cost = router._adjusted_cost(1.0, _SUN_NOON)
        assert cost == 0.7


# ── route_by_cost deep edge cases ────────────────────────────────────────────


def _make_route_router(
    rankings: list[dict[str, object]] | None = None,
    budget_guard: object | None = None,
    cost_tracker: object | None = None,
    deferred_queue: object | None = None,
    peak_schedule: PeakPricingSchedule | None = None,
) -> CostAwareRouter:
    perf = MagicMock()
    perf.get_rankings = AsyncMock(return_value=rankings or [])
    perf.select_model = AsyncMock(return_value={"service": "openai", "model_name": "gpt-4o-mini", "fallback": True})
    return CostAwareRouter(
        perf,
        peak_schedule=peak_schedule or _DEFAULT_SCHEDULE,
        budget_guard=budget_guard,
        cost_tracker=cost_tracker,
        deferred_queue=deferred_queue,
    )


class TestRouteByCostDeep:
    def test_all_over_budget_falls_back_to_cheapest_any(self) -> None:
        rankings = _rankings(
            ("openai", "gpt-4o", 0.03),
            ("anthropic", "claude-haiku", 0.002),
        )
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("bug_fix", budget_remaining=0.0001, now=_TUE_NOON))
        assert route.model_id == "anthropic/claude-haiku"

    def test_single_ranking_within_budget(self) -> None:
        rankings = _rankings(("openai", "gpt-4o", 0.01))
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=1.0, now=_TUE_NOON))
        assert route.model_id == "openai/gpt-4o"

    def test_single_ranking_over_budget(self) -> None:
        rankings = _rankings(("openai", "gpt-4o", 0.01))
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=0.001, now=_TUE_NOON))
        assert route.model_id == "openai/gpt-4o"

    def test_identical_costs_picks_first_within_budget(self) -> None:
        rankings: list[dict[str, object]] = [
            {"service": "a", "model_name": "m1", "avg_cost_usd": 0.01, "success_rate": 0.9},
            {"service": "b", "model_name": "m2", "avg_cost_usd": 0.01, "success_rate": 0.95},
        ]
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=1.0, now=_TUE_NOON))
        assert route.model_id in ("a/m1", "b/m2")

    def test_missing_service_key_uses_empty_string(self) -> None:
        rankings: list[dict[str, object]] = [
            {"model_name": "m1", "avg_cost_usd": 0.01, "success_rate": 0.9},
        ]
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=1.0, now=_TUE_NOON))
        assert route.model_id == "/m1"

    def test_missing_model_name_key_uses_empty_string(self) -> None:
        rankings: list[dict[str, object]] = [
            {"service": "openai", "avg_cost_usd": 0.01, "success_rate": 0.9},
        ]
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=1.0, now=_TUE_NOON))
        assert route.model_id == "openai/"

    def test_missing_cost_key_defaults_to_zero(self) -> None:
        rankings: list[dict[str, object]] = [
            {"service": "a", "model_name": "m1", "success_rate": 0.9},
        ]
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=1.0, now=_TUE_NOON))
        assert route.estimated_cost == 0.0

    def test_budget_remaining_zero_still_returns_cheapest(self) -> None:
        rankings = _rankings(
            ("openai", "gpt-4o", 0.03),
            ("openai", "gpt-4o-mini", 0.001),
        )
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=0.0, now=_TUE_NOON))
        assert route.model_id == "openai/gpt-4o-mini"

    def test_cost_tracker_returns_zero(self) -> None:
        rankings = _rankings(("a", "m1", 0.01))
        tracker = MagicMock()
        tracker.remaining_model_budget.return_value = 0.0
        router = _make_route_router(rankings, cost_tracker=tracker)
        route = asyncio.run(router.route_by_cost("task", now=_TUE_NOON))
        assert route is not None

    def test_cost_tracker_returns_negative(self) -> None:
        rankings = _rankings(("a", "m1", 0.01))
        tracker = MagicMock()
        tracker.remaining_model_budget.return_value = -5.0
        router = _make_route_router(rankings, cost_tracker=tracker)
        route = asyncio.run(router.route_by_cost("task", now=_TUE_NOON))
        assert route.model_id == "a/m1"

    def test_zero_base_cost_models(self) -> None:
        rankings = _rankings(("free", "free-model", 0.0))
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=1.0, now=_TUE_NOON))
        assert route.estimated_cost == 0.0

    def test_off_peak_all_models_cheaper(self) -> None:
        rankings = _rankings(
            ("openai", "gpt-4o", 0.03),
            ("openai", "gpt-4o-mini", 0.001),
        )
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=0.01, now=_SUN_NOON))
        assert route.estimated_cost == round(0.001 * 0.7, 6)

    def test_large_ranking_list(self) -> None:
        rankings = _rankings(*[(f"s{i}", f"m{i}", float(i) * 0.001) for i in range(100)])
        router = _make_route_router(rankings)
        route = asyncio.run(router.route_by_cost("task", budget_remaining=999.0, now=_TUE_NOON))
        assert route.model_id == "s0/m0"


# ── is_better_to_wait deep edge cases ────────────────────────────────────────


class TestIsBetterToWaitDeep:
    def test_saving_exactly_at_20_percent_threshold(self) -> None:
        router = _router_with_schedule(peak_multiplier=1.25, off_peak_multiplier=1.0)
        now = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 100.0}
        peak_cost = 100.0 * 1.25
        off_cost = 100.0 * 1.0
        saving = peak_cost - off_cost
        assert saving == peak_cost * 0.20
        assert router.is_better_to_wait(task, deadline_hours=24, now=now) is True

    def test_saving_just_below_20_percent_returns_false(self) -> None:
        router = _router_with_schedule(peak_multiplier=1.24, off_peak_multiplier=1.0)
        now = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 100.0}
        assert router.is_better_to_wait(task, deadline_hours=24, now=now) is False

    def test_saving_just_above_20_percent_returns_true(self) -> None:
        router = _router_with_schedule(peak_multiplier=2.0, off_peak_multiplier=1.0)
        now = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 100.0}
        assert router.is_better_to_wait(task, deadline_hours=24, now=now) is True

    def test_negative_estimated_cost_returns_false(self) -> None:
        router = _router_with_schedule()
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": -1.0}
        assert router.is_better_to_wait(task, deadline_hours=8, now=now) is False

    def test_missing_estimated_cost_key_returns_false(self) -> None:
        router = _router_with_schedule()
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        task: dict[str, object] = {}
        assert router.is_better_to_wait(task, deadline_hours=8, now=now) is False

    def test_deadline_zero_hours_returns_false_in_peak(self) -> None:
        router = _router_with_schedule()
        now = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 100.0}
        assert router.is_better_to_wait(task, deadline_hours=0.0, now=now) is False

    def test_very_large_deadline_returns_true(self) -> None:
        router = _router_with_schedule()
        now = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 100.0}
        assert router.is_better_to_wait(task, deadline_hours=999999.0, now=now) is True

    def test_at_exact_peak_end_hour_stay_peak_is_false(self) -> None:
        router = _router_with_schedule()
        now = datetime.datetime(2026, 8, 4, 20, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 100.0}
        assert router.is_better_to_wait(task, deadline_hours=24, now=now) is False

    def test_one_hour_before_peak_end_stay_peak_true(self) -> None:
        router = _router_with_schedule()
        now = datetime.datetime(2026, 8, 4, 19, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 10.0}
        assert router.is_better_to_wait(task, deadline_hours=2, now=now) is True

    def test_off_peak_before_peak_start_peak_day(self) -> None:
        router = _router_with_schedule()
        now = datetime.datetime(2026, 8, 4, 3, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 100.0}
        assert router.is_better_to_wait(task, deadline_hours=24, now=now) is False

    def test_large_saving_exact_deadline_match(self) -> None:
        router = _router_with_schedule()
        now = datetime.datetime(2026, 8, 4, 19, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 10.0}
        hours_until = 20 - 19  # dt.hour is int, so hours_until == 1
        assert router.is_better_to_wait(task, deadline_hours=hours_until, now=now) is True

    def test_large_saving_deadline_just_too_tight(self) -> None:
        router = _router_with_schedule()
        now = datetime.datetime(2026, 8, 4, 19, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 10.0}
        hours_until = 20 - 19
        assert router.is_better_to_wait(task, deadline_hours=hours_until - 0.001, now=now) is False

    def test_saving_zero_when_off_peak_equals_peak(self) -> None:
        router = _router_with_schedule(peak_multiplier=1.0, off_peak_multiplier=1.0)
        now = datetime.datetime(2026, 8, 4, 10, 0, 0, tzinfo=datetime.UTC)
        task = {"estimated_cost": 100.0}
        assert router.is_better_to_wait(task, deadline_hours=24, now=now) is False


# ── defer_to_off_peak deep edge cases ────────────────────────────────────────


def _defer_router(
    deferred_queue: object | None = None,
    peak_schedule: PeakPricingSchedule | None = None,
) -> CostAwareRouter:
    return CostAwareRouter(
        MagicMock(),
        peak_schedule=peak_schedule or _DEFAULT_SCHEDULE,
        deferred_queue=deferred_queue,
    )


class TestDeferToOffPeakDeep:
    def test_defer_during_off_peak_schedules_one_hour_ahead(self) -> None:
        router = _defer_router()
        now = datetime.datetime(2026, 8, 9, 3, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=24)
        result = router.defer_to_off_peak("task", deadline, now=now)
        scheduled = datetime.datetime.fromisoformat(cast(str, result["scheduled_for"]))
        expected = now + datetime.timedelta(hours=1)
        assert scheduled == expected

    def test_defer_deadline_exactly_at_peak_end(self) -> None:
        router = _defer_router()
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        end_of_peak = now.replace(hour=20, minute=0, second=0, microsecond=0)
        result = router.defer_to_off_peak("task", end_of_peak, now=now)
        scheduled = datetime.datetime.fromisoformat(cast(str, result["scheduled_for"]))
        assert scheduled <= end_of_peak

    def test_defer_peak_end_wraps_past_midnight(self) -> None:
        router = _defer_router()
        now = datetime.datetime(2026, 8, 4, 21, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=48)
        result = router.defer_to_off_peak("task", deadline, now=now)
        scheduled = datetime.datetime.fromisoformat(cast(str, result["scheduled_for"]))
        assert scheduled > now
        assert scheduled <= deadline

    def test_defer_peak_at_exact_end_hour(self) -> None:
        router = _defer_router()
        now = datetime.datetime(2026, 8, 4, 20, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=24)
        result = router.defer_to_off_peak("task", deadline, now=now)
        scheduled = datetime.datetime.fromisoformat(cast(str, result["scheduled_for"]))
        assert scheduled > now
        assert scheduled <= deadline

    def test_defer_deadline_in_past_relative_to_peak_end(self) -> None:
        router = _defer_router()
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(minutes=30)
        result = router.defer_to_off_peak("task", deadline, now=now)
        scheduled = datetime.datetime.fromisoformat(cast(str, result["scheduled_for"]))
        assert scheduled <= deadline

    def test_queue_enqueue_raises_value_error(self) -> None:
        q = MagicMock()
        q.enqueue.side_effect = ValueError("bad data")
        router = _defer_router(deferred_queue=q)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=24)
        result = router.defer_to_off_peak("task", deadline, now=now)
        assert result["enqueued"] is False
        assert result["enqueue_id"] is None
        assert result["task_id"] == "task"

    def test_queue_enqueue_raises_keyboard_interrupt_propagates(self) -> None:
        q = MagicMock()
        q.enqueue.side_effect = KeyboardInterrupt("interrupted")
        router = _defer_router(deferred_queue=q)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=24)
        with pytest.raises(KeyboardInterrupt):
            router.defer_to_off_peak("task", deadline, now=now)
        assert True

    def test_defer_no_queue_no_now_defaults_to_utc(self) -> None:
        router = _defer_router()
        deadline = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=24)
        result = router.defer_to_off_peak("task", deadline)
        assert "scheduled_for" in result
        assert result["enqueued"] is False


# ── estimate_cost deep edge cases ────────────────────────────────────────────


class TestEstimateCostDeep:
    def test_estimate_zero_cost(self) -> None:
        router = _router_with_schedule()
        assert router.estimate_cost(0.0, now=_TUE_NOON) == 0.0

    def test_estimate_large_cost(self) -> None:
        router = _router_with_schedule()
        assert router.estimate_cost(1e9, now=_TUE_NOON) == 1.5e9

    def test_estimate_tiny_cost_not_rounded_to_zero(self) -> None:
        router = _router_with_schedule()
        cost = router.estimate_cost(1e-6, now=_TUE_NOON)  # 1e-6 * 1.5 = 0.000001 (6 decimal places)
        assert cost > 0.0
        assert cost == pytest.approx(0.000002, rel=1e-9)  # round(0.0000015, 6)

    def test_estimate_now_none_returns_positive(self) -> None:
        router = _router_with_schedule()
        cost = router.estimate_cost(1.0)
        assert cost > 0.0


# ── check_budget deep edge cases ─────────────────────────────────────────────


class TestCheckBudgetDeep:
    def test_guard_returns_empty_dict(self) -> None:
        guard = MagicMock()
        guard.check_all_limits.return_value = {}
        router = CostAwareRouter(MagicMock(), budget_guard=guard)
        result = router.check_budget(5.0)
        assert result == {}

    def test_guard_zero_cost(self) -> None:
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": True, "reason": "ok"}
        router = CostAwareRouter(MagicMock(), budget_guard=guard)
        result = router.check_budget(0.0)
        assert result["allowed"] is True
        guard.check_all_limits.assert_called_once_with(estimated_cost=0.0)

    def test_guard_very_large_cost(self) -> None:
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "exceeds cap"}
        router = CostAwareRouter(MagicMock(), budget_guard=guard)
        result = router.check_budget(1e12)
        assert result["allowed"] is False


# ── peak_schedule property edge cases ────────────────────────────────────────


class TestPeakSchedulePropertyDeep:
    def test_readonly_property_returns_same_object(self) -> None:
        schedule = PeakPricingSchedule(peak_start_hour=6, peak_end_hour=18)
        router = CostAwareRouter(MagicMock(), peak_schedule=schedule)
        assert router.peak_schedule is schedule

    def test_property_with_default_schedule(self) -> None:
        router = CostAwareRouter(MagicMock())
        assert router.peak_schedule == _DEFAULT_PEAK
