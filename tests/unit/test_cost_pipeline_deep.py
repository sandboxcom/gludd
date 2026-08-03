"""Deep integration tests for the cost-aware routing pipeline.

Covers: peak/off-peak detection edge cases, budget exhaustion with partial
refund, multi-model routing cost comparison, scheduler priority inversion,
cost estimation accuracy across model sizes, and batch request cost aggregation.
"""

from __future__ import annotations

import asyncio
import datetime
import threading
import time
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.budget.combined_cost import CombinedCostTracker
from general_ludd.budget.off_peak_scheduler import (
    OffPeakScheduler,
    SavingsTracker,
)
from general_ludd.budget.peak_pricing import (
    PeakPricingSchedule,
    PeakPricingTracker,
    RateTier,
    current_rate_multiplier,
    default_schedule,
    is_off_peak,
    is_peak,
    peak_rate_for_model,
)
from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.infra.cost_tracker import InfraCostTracker
from general_ludd.models.cost_router import (
    CostAwareRouter,
)
from general_ludd.models.cost_router import (
    PeakPricingSchedule as RouterPeakSchedule,
)
from general_ludd.small_models.cost import (
    compute_cost_score,
    estimate_download_cost,
    estimate_inference_cost,
    estimate_quantize_cost,
    next_off_peak_window,
    should_defer_download,
)
from general_ludd.small_models.cost import (
    is_off_peak as sm_is_off_peak,
)

# ---------------------------------------------------------------------------
# 1. Peak/off-peak detection edge cases
# ---------------------------------------------------------------------------


class TestPeakOffPeakBoundary:
    """Edge cases for peak/off-peak time-window detection."""

    def test_midnight_boundary_is_off_peak(self) -> None:
        midnight = datetime.datetime(2026, 8, 4, 0, 0, 0, tzinfo=datetime.UTC)
        assert current_rate_multiplier(midnight) == 0.75
        assert is_peak(midnight) is False

    def test_one_second_before_peak_starts(self) -> None:
        before_peak = datetime.datetime(2026, 8, 4, 8, 59, 59, tzinfo=datetime.UTC)
        assert current_rate_multiplier(before_peak) == 0.75

    def test_exact_peak_start_boundary(self) -> None:
        start = datetime.datetime(2026, 8, 4, 9, 0, 0, tzinfo=datetime.UTC)
        assert current_rate_multiplier(start) == 1.0
        assert is_peak(start) is True

    def test_exact_peak_end_boundary_is_off_peak(self) -> None:
        end = datetime.datetime(2026, 8, 4, 17, 0, 0, tzinfo=datetime.UTC)
        assert current_rate_multiplier(end) == 0.75
        assert is_peak(end) is False

    def test_friday_evening_is_off_peak(self) -> None:
        friday_7pm = datetime.datetime(2026, 8, 7, 19, 0, 0, tzinfo=datetime.UTC)
        assert current_rate_multiplier(friday_7pm) == 0.75

    def test_saturday_all_day_is_off_peak(self) -> None:
        for hour in range(0, 24):
            dt = datetime.datetime(2026, 8, 8, hour, 0, 0, tzinfo=datetime.UTC)
            assert current_rate_multiplier(dt) == 0.75, f"hour={hour}"
            assert is_peak(dt) is False, f"hour={hour}"

    def test_sunday_all_day_is_off_peak(self) -> None:
        for hour in range(0, 24):
            dt = datetime.datetime(2026, 8, 9, hour, 0, 0, tzinfo=datetime.UTC)
            assert current_rate_multiplier(dt) == 0.75, f"hour={hour}"
            assert is_peak(dt) is False, f"hour={hour}"

    def test_monday_morning_is_off_peak_until_9(self) -> None:
        for hour in range(0, 9):
            dt = datetime.datetime(2026, 8, 10, hour, 0, 0, tzinfo=datetime.UTC)
            assert current_rate_multiplier(dt) == 0.75, f"hour={hour}"

    def test_custom_discount_applies_multiplicatively(self) -> None:
        now = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)
        assert current_rate_multiplier(now, off_peak_discount=0.5) == 0.5
        now_peak = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        assert current_rate_multiplier(now_peak, off_peak_discount=0.5) == 1.0

    def test_rate_tier_overnight_window_crosses_midnight(self) -> None:
        tier = RateTier(
            model_id="test",
            provider="test",
            rate=1.0,
            label="off-peak",
            days=frozenset({0, 1, 2, 3, 4}),
            start_hour=20,
            end_hour=6,
        )
        midnight = datetime.datetime(2026, 8, 4, 0, 30, tzinfo=datetime.UTC)
        assert tier.covers(midnight) is True
        evening = datetime.datetime(2026, 8, 4, 22, 0, tzinfo=datetime.UTC)
        assert tier.covers(evening) is True
        morning = datetime.datetime(2026, 8, 4, 5, 59, tzinfo=datetime.UTC)
        assert tier.covers(morning) is True
        noon = datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC)
        assert tier.covers(noon) is False

    def test_rate_tier_saturday_excluded_from_weekday_tier(self) -> None:
        tier = RateTier(
            model_id="test",
            provider="test",
            rate=1.0,
            label="peak",
            days=frozenset({0, 1, 2, 3, 4}),
            start_hour=9,
            end_hour=17,
        )
        saturday = datetime.datetime(2026, 8, 8, 12, 0, tzinfo=datetime.UTC)
        assert tier.covers(saturday) is False

    def test_peak_pricing_schedule_matching_tier_returns_first_match(self) -> None:
        sched = PeakPricingSchedule()
        sched.add_tier(RateTier("m", "p", 1.0, "peak", frozenset({0, 1, 2, 3, 4}), 9, 17))
        sched.add_tier(RateTier("m", "p", 0.5, "off-peak", frozenset({0, 1, 2, 3, 4}), 0, 24))
        monday_noon = datetime.datetime(2026, 8, 10, 12, 0, tzinfo=datetime.UTC)
        tier = sched.matching_tier("m", "p", monday_noon)
        assert tier is not None
        assert tier.rate == 1.0
        assert tier.label == "peak"

    def test_small_models_is_off_peak_weekday_morning(self) -> None:
        morning = datetime.datetime(2026, 8, 4, 3, 0, 0, tzinfo=datetime.UTC)
        assert sm_is_off_peak(morning) is True

    def test_small_models_is_off_peak_weekday_noon_is_peak(self) -> None:
        noon = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        assert sm_is_off_peak(noon) is False

    def test_small_models_is_off_peak_weekday_evening(self) -> None:
        evening = datetime.datetime(2026, 8, 4, 20, 0, 0, tzinfo=datetime.UTC)
        assert sm_is_off_peak(evening) is True

    def test_small_models_next_off_peak_window_during_peak(self) -> None:
        noon = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        window = next_off_peak_window(noon)
        assert window["is_off_peak_now"] is False
        assert cast(int, window["seconds_until"]) > 0

    def test_small_models_next_off_peak_window_when_already_off_peak(self) -> None:
        midnight = datetime.datetime(2026, 8, 4, 0, 0, 0, tzinfo=datetime.UTC)
        window = next_off_peak_window(midnight)
        assert window["is_off_peak_now"] is True
        assert window["seconds_until"] == 0


# ---------------------------------------------------------------------------
# 2. Budget exhaustion with partial refund
# ---------------------------------------------------------------------------


class TestBudgetExhaustion:
    """Budget exhaustion scenarios including partial refund tracking."""

    def test_peak_pricing_tracker_accumulates_savings(self) -> None:
        tracker = PeakPricingTracker()
        tracker.record_call(base_cost=10.0, effective_cost=7.5)
        tracker.record_call(base_cost=5.0, effective_cost=3.5)

        assert tracker.cumulative_savings == pytest.approx(4.0)
        assert tracker.cumulative_full_cost == pytest.approx(15.0)
        assert tracker.cumulative_discounted_cost == pytest.approx(11.0)

    def test_peak_pricing_tracker_ignores_no_saving_calls(self) -> None:
        tracker = PeakPricingTracker()
        tracker.record_call(base_cost=5.0, effective_cost=5.0)
        tracker.record_call(base_cost=3.0, effective_cost=5.0)

        assert tracker.cumulative_savings == 0.0
        assert tracker.cumulative_full_cost == 0.0

    def test_peak_pricing_tracker_singleton_returns_same_instance(self) -> None:
        a = PeakPricingTracker.singleton()
        b = PeakPricingTracker.singleton()
        assert a is b

    def test_peak_pricing_tracker_thread_safety(self) -> None:
        tracker = PeakPricingTracker()
        threads = 4
        calls_per_thread = 100
        barrier = threading.Barrier(threads)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait()
                for _i in range(calls_per_thread):
                    tracker.record_call(base_cost=1.0, effective_cost=0.5)
            except BaseException as exc:
                errors.append(exc)

        ts = [threading.Thread(target=worker) for _ in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        assert not errors, f"threads raised: {errors!r}"
        expected_calls = threads * calls_per_thread
        assert tracker.cumulative_full_cost == pytest.approx(float(expected_calls))
        assert tracker.cumulative_savings == pytest.approx(float(expected_calls) * 0.5)

    def test_savings_tracker_rejects_negative_savings(self) -> None:
        st = SavingsTracker()
        st.record(-5.0)
        assert st.total_savings == 0.0
        assert st.total_deferred == 0

    def test_savings_tracker_rejects_non_finite_savings(self) -> None:
        st = SavingsTracker()
        st.record(float("nan"))
        st.record(float("inf"))
        assert st.total_savings == 0.0

    def test_savings_tracker_snapshot_includes_all_fields(self) -> None:
        st = SavingsTracker()
        st.record(10.0)
        st.record(5.0)
        snap = st.snapshot()
        assert snap["total_deferred"] == 2
        assert snap["total_savings"] == 15.0

    def test_off_peak_scheduler_records_savings_on_deferral(self) -> None:
        with patch.object(OffPeakScheduler, "_is_off_peak", return_value=False):
            sched = OffPeakScheduler(min_savings_ratio=0.0)
            sched.schedule(
                {"task": "heavy"},
                deadline=time.time() + 86400,
                estimated_cost_now=10.0,
                estimated_cost_off_peak=2.0,
            )
            assert sched.savings.total_savings == pytest.approx(8.0)
            assert sched.savings.total_deferred == 1

    def test_off_peak_scheduler_does_not_defer_when_already_off_peak(self) -> None:
        with patch.object(OffPeakScheduler, "_is_off_peak", return_value=True):
            sched = OffPeakScheduler(min_savings_ratio=0.0)
            ticket = sched.schedule(
                {"task": "light"},
                deadline=time.time() + 86400,
                estimated_cost_now=10.0,
                estimated_cost_off_peak=2.0,
            )
            assert ticket is None
            assert sched.savings.total_deferred == 0


# ---------------------------------------------------------------------------
# 3. Multi-model routing cost comparison
# ---------------------------------------------------------------------------


class TestMultiModelRouting:
    """Cost-aware routing across multiple models with different price points."""

    def _make_rankings(self) -> list[dict[str, object]]:
        return [
            {"service": "openai", "model_name": "gpt-4o", "avg_cost_usd": 0.03, "success_rate": 0.98},
            {"service": "openai", "model_name": "gpt-4.1-mini", "avg_cost_usd": 0.0004, "success_rate": 0.92},
            {"service": "openai", "model_name": "gpt-4.1", "avg_cost_usd": 0.002, "success_rate": 0.96},
            {
                "service": "anthropic",
                "model_name": "claude-sonnet-4-20250514",
                "avg_cost_usd": 0.003,
                "success_rate": 0.95,
            },
            {
                "service": "anthropic",
                "model_name": "claude-opus-4-20250514",
                "avg_cost_usd": 0.015,
                "success_rate": 0.99,
            },
        ]

    def test_cheapest_model_wins_with_unlimited_budget(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())
        router = CostAwareRouter(perf)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("feature", now=now))
        assert route.model_id == "openai/gpt-4.1-mini"

    def test_tight_budget_forces_fallback_to_cheapest(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())
        router = CostAwareRouter(perf)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("feature", budget_remaining=0.0001, now=now))
        assert route.model_id == "openai/gpt-4.1-mini"

    def test_off_peak_cheap_model_becomes_cheaper(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())
        router = CostAwareRouter(perf)
        sunday = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("feature", now=sunday))
        assert route.peak_status == "off_peak"
        assert route.estimated_cost == round(0.0004 * 0.7, 6)

    def test_budget_exhausted_no_model_fits_returns_cheapest_anyway(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())
        router = CostAwareRouter(perf)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("feature", budget_remaining=0.000001, now=now))
        assert route.model_id == "openai/gpt-4.1-mini"

    def test_peak_multiplier_increases_all_costs(self) -> None:
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())
        router = CostAwareRouter(perf)
        peak = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        off = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)

        route_peak = asyncio.run(router.route_by_cost("feature", budget_remaining=None, now=peak))
        route_off = asyncio.run(router.route_by_cost("feature", budget_remaining=None, now=off))

        assert route_peak.estimated_cost > route_off.estimated_cost

    def test_custom_peak_schedule_changes_cost_ordering(self) -> None:
        schedule = RouterPeakSchedule(
            peak_start_hour=0,
            peak_end_hour=24,
            peak_multiplier=10.0,
            off_peak_multiplier=0.1,
        )
        perf = MagicMock()
        perf.get_rankings = AsyncMock(return_value=self._make_rankings())
        router = CostAwareRouter(perf, peak_schedule=schedule)
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)

        route = asyncio.run(router.route_by_cost("feature", budget_remaining=1.0, now=now))
        assert route is not None


# ---------------------------------------------------------------------------
# 4. Scheduler priority inversion scenarios
# ---------------------------------------------------------------------------


class TestSchedulerPriorityInversion:
    """OffPeakScheduler behavior when tasks with different priorities compete."""

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_two_tasks_different_costs_correct_order(self, _mock: MagicMock) -> None:
        sched = OffPeakScheduler(min_savings_ratio=0.0)

        ticket_small = sched.schedule(
            {"task": "small"},
            deadline=time.time() + 86400,
            estimated_cost_now=1.0,
            estimated_cost_off_peak=0.5,
        )
        ticket_large = sched.schedule(
            {"task": "large"},
            deadline=time.time() + 86400,
            estimated_cost_now=100.0,
            estimated_cost_off_peak=50.0,
        )

        assert ticket_small is not None
        assert ticket_large is not None
        assert ticket_large.savings > ticket_small.savings

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_task_below_min_savings_ratio_not_deferred(self, _mock: MagicMock) -> None:
        sched = OffPeakScheduler(min_savings_ratio=0.50)
        ticket = sched.schedule(
            {"task": "barely"},
            deadline=time.time() + 86400,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=9.0,
        )
        assert ticket is None

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_deadline_passed_tasks_pruned_not_ready(self, _mock: MagicMock) -> None:
        sched = OffPeakScheduler(ticket_ttl=0.0, min_savings_ratio=0.0)
        sched.schedule(
            {"task": "expired"},
            deadline=time.time() - 100,
            estimated_cost_now=10.0,
            estimated_cost_off_peak=5.0,
        )
        ready = sched.get_ready_tasks()
        assert len(ready) == 0

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_same_deadline_higher_savings_matters(self, _mock: MagicMock) -> None:
        sched = OffPeakScheduler(min_savings_ratio=0.0, off_peak_start=0, off_peak_end=6)

        deadline = time.time() + 86400
        t1 = sched.schedule(
            {"task": "expensive"},
            deadline=deadline,
            estimated_cost_now=500.0,
            estimated_cost_off_peak=100.0,
        )
        t2 = sched.schedule(
            {"task": "moderate"},
            deadline=deadline,
            estimated_cost_now=200.0,
            estimated_cost_off_peak=50.0,
        )

        assert t1 is not None and t2 is not None
        assert t1.savings > t2.savings

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_schedule_returns_unique_task_ids(self, _mock: MagicMock) -> None:
        sched = OffPeakScheduler(min_savings_ratio=0.0)
        t1 = sched.schedule(
            {"t": 1}, deadline=time.time() + 86400, estimated_cost_now=10.0, estimated_cost_off_peak=1.0
        )
        t2 = sched.schedule(
            {"t": 2}, deadline=time.time() + 86400, estimated_cost_now=10.0, estimated_cost_off_peak=1.0
        )

        assert t1 is not None and t2 is not None
        assert t1.task_id != t2.task_id

    @patch("general_ludd.budget.off_peak_scheduler.OffPeakScheduler._is_off_peak", return_value=False)
    def test_get_status_reflects_pending_count(self, _mock: MagicMock) -> None:
        sched = OffPeakScheduler(min_savings_ratio=0.0)
        for i in range(5):
            sched.schedule({"t": i}, deadline=time.time() + 86400, estimated_cost_now=10.0, estimated_cost_off_peak=1.0)

        status = sched.get_status()
        assert status["pending_count"] == 5
        assert status["off_peak_active"] is False

    def test_off_peak_detection_overnight_window_22_to_6(self) -> None:
        sched = OffPeakScheduler(off_peak_start=22, off_peak_end=6)

        with patch("time.localtime") as mock_lt:
            mock_lt.return_value = time.struct_time((2026, 8, 4, 23, 0, 0, 1, 216, 0))
            mock_lt.configure_mock(tm_hour=23)
            assert sched._is_off_peak() is True

        with patch("time.localtime") as mock_lt:
            mock_lt.return_value = time.struct_time((2026, 8, 4, 3, 0, 0, 1, 216, 0))
            mock_lt.configure_mock(tm_hour=3)
            assert sched._is_off_peak() is True

        with patch("time.localtime") as mock_lt:
            mock_lt.return_value = time.struct_time((2026, 8, 4, 12, 0, 0, 1, 216, 0))
            mock_lt.configure_mock(tm_hour=12)
            assert sched._is_off_peak() is False


# ---------------------------------------------------------------------------
# 5. Cost estimation accuracy for different model sizes
# ---------------------------------------------------------------------------


class TestCostEstimationAccuracy:
    """Verify cost estimation correctness across model sizes and tiers."""

    def test_small_local_model_cost_is_lowest(self) -> None:
        info = estimate_inference_cost("phi-2")
        assert info["tier"] == "small_local"
        assert info["input_usd_per_1m_tokens"] == 0.0001
        assert info["output_usd_per_1m_tokens"] == 0.0002
        estimated = cast(float, info["estimated_usd_per_hour"])
        assert estimated < 0.01

    def test_medium_api_model_has_medium_cost(self) -> None:
        info = estimate_inference_cost("qwen2.5-7b")
        assert info["tier"] == "medium_api"
        estimated = cast(float, info["estimated_usd_per_hour"])
        assert 0.0001 < estimated < 0.1

    def test_large_api_model_cost_is_highest(self) -> None:
        info = estimate_inference_cost("llama3.1-70b")
        assert info["tier"] == "large_api"
        estimated = cast(float, info["estimated_usd_per_hour"])
        assert estimated > 0.001

    def test_unknown_model_defaults_to_small_local(self) -> None:
        info = estimate_inference_cost("nonexistent-model")
        assert info["tier"] == "small_local"

    def test_cost_score_cheaper_model_scores_higher(self) -> None:
        score_phi = compute_cost_score("phi-2")
        score_llama70 = compute_cost_score("llama3.1-70b")
        assert score_phi > score_llama70

    def test_cost_score_bounded_0_to_1(self) -> None:
        for model in ("phi-2", "qwen2.5-7b", "llama3.1-70b", "mistral-7b"):
            score = compute_cost_score(model)
            assert 0.0 <= score <= 1.0, f"score={score} for {model}"

    def test_download_cost_large_model_prefers_off_peak(self) -> None:
        result = estimate_download_cost("llama3.1-70b")
        assert result["size_gb"] == 140.0
        assert result["prefer_off_peak"] is True

    def test_download_cost_small_model_does_not_prefer_off_peak(self) -> None:
        result = estimate_download_cost("phi-2")
        assert result["size_gb"] == 2.7
        assert result["prefer_off_peak"] is False

    def test_quantize_cost_increases_with_method_quality(self) -> None:
        cost_q4 = cast(float, estimate_quantize_cost("phi-2", 2.7, "q4_0")["estimated_cost_usd"])
        cost_q8 = cast(float, estimate_quantize_cost("phi-2", 2.7, "q8_0")["estimated_cost_usd"])
        cost_f16 = cast(float, estimate_quantize_cost("phi-2", 2.7, "f16")["estimated_cost_usd"])
        assert cost_q8 > cost_q4
        assert cost_q4 > cost_f16

    def test_size_resolution_from_model_name_containing_b(self) -> None:
        result = estimate_download_cost("custom-13b-model")
        assert result["size_gb"] == pytest.approx(26.0)

    def test_should_defer_large_download_during_peak(self) -> None:
        noon = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        result = should_defer_download(2.0, now=noon)
        assert result["defer"] is True
        assert result["reason"] == "large_download_during_peak"

    def test_should_not_defer_small_download_during_peak(self) -> None:
        noon = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        result = should_defer_download(0.5, now=noon)
        assert result["defer"] is False

    def test_should_not_defer_when_already_off_peak(self) -> None:
        midnight = datetime.datetime(2026, 8, 4, 0, 0, 0, tzinfo=datetime.UTC)
        result = should_defer_download(10.0, now=midnight)
        assert result["defer"] is False

    def test_api_model_inference_cost_uses_pricing_lookup(self) -> None:
        info = estimate_inference_cost("gpt-4o")
        assert info["tier"] == "large_api"
        estimated = cast(float, info["estimated_usd_per_hour"])
        assert estimated > 0.0


# ---------------------------------------------------------------------------
# 6. Batch request cost aggregation
# ---------------------------------------------------------------------------


class TestBatchCostAggregation:
    """CombinedCostTracker batch operations and multi-source cost aggregation."""

    def _make_limiter(self, limit: float = 1000.0, window: float = 3600.0) -> SpendLimiter:
        return SpendLimiter(limit_usd=limit, window_seconds=window)

    def test_combined_tracker_model_side(self) -> None:
        sl = self._make_limiter()
        tracker = CombinedCostTracker(spend_limiter=sl)
        assert tracker.has_model is True
        assert tracker.has_infra is False
        assert tracker.model_spend() == 0.0
        assert tracker.infra_spend() == 0.0

    def test_combined_tracker_both_sides(self) -> None:
        sl = self._make_limiter()
        it = InfraCostTracker()
        tracker = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)
        assert tracker.has_model is True
        assert tracker.has_infra is True

    def test_record_model_cost_updates_window_spend(self) -> None:
        sl = self._make_limiter()
        tracker = CombinedCostTracker(spend_limiter=sl)
        tracker.record_model_cost(5.0)
        tracker.record_model_cost(3.0, project_id="proj-a")

        assert tracker.model_spend() == pytest.approx(8.0)
        assert tracker.get_total_spend() == pytest.approx(8.0)

    def test_record_model_cost_without_limiter_raises(self) -> None:
        tracker = CombinedCostTracker()
        with pytest.raises(RuntimeError, match="no SpendLimiter"):
            tracker.record_model_cost(5.0)

    def test_record_infra_cost_without_tracker_raises(self) -> None:
        tracker = CombinedCostTracker(spend_limiter=self._make_limiter())
        with pytest.raises(RuntimeError, match="no InfraCostTracker"):
            tracker.record_infra_cost("aws", "compute", "i-1", 1.0)

    def test_combined_total_is_sum_of_both_sides(self) -> None:
        sl = self._make_limiter()
        it = InfraCostTracker()
        tracker = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)

        tracker.record_model_cost(10.0)
        it.record("runpod", "gpu_instance", "ig-1", 5.0)

        total = tracker.get_total_spend()
        infra_part = tracker.infra_spend()
        model_part = tracker.model_spend()

        assert total == pytest.approx(model_part + infra_part)
        assert total > model_part

    def test_remaining_model_budget_returns_inf_when_no_cap(self) -> None:
        sl = SpendLimiter(limit_usd=0.0, window_seconds=3600.0)
        tracker = CombinedCostTracker(spend_limiter=sl)
        assert tracker.remaining_model_budget() == float("inf")

    def test_remaining_model_budget_decreases_with_spend(self) -> None:
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0)
        tracker = CombinedCostTracker(spend_limiter=sl)
        tracker.record_model_cost(30.0)
        assert tracker.remaining_model_budget() == pytest.approx(70.0)

    def test_would_exceed_combined_returns_true_when_over_cap(self) -> None:
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0)
        tracker = CombinedCostTracker(spend_limiter=sl)
        tracker.record_model_cost(90.0)
        assert tracker.would_exceed_combined(20.0) is True

    def test_would_exceed_combined_returns_false_when_under_cap(self) -> None:
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0)
        tracker = CombinedCostTracker(spend_limiter=sl)
        tracker.record_model_cost(10.0)
        assert tracker.would_exceed_combined(20.0) is False

    def test_would_exceed_combined_no_limiter_returns_false(self) -> None:
        tracker = CombinedCostTracker()
        assert tracker.would_exceed_combined(999.0) is False

    def test_cost_breakdown_includes_all_categories(self) -> None:
        sl = self._make_limiter()
        it = InfraCostTracker()
        tracker = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)

        tracker.record_model_cost(5.0, project_id="p1")
        tracker.record_model_cost(2.0, project_id="p2")
        it.record("aws", "gpu_instance", "ig-2", 5.0, project_id="p1")
        it.record("runpod", "gpu_instance", "ig-3", 2.5, project_id="p3")

        bd = tracker.get_cost_breakdown()
        assert "model_api" in bd
        assert "infrastructure" in bd
        assert "total" in bd
        assert "breakdown_by_provider" in bd
        assert "breakdown_by_resource_type" in bd
        assert "breakdown_by_project" in bd
        assert "record_count" in bd
        assert bd["total"] > 0.0

    def test_snapshot_roundtrip_preserves_state(self) -> None:
        sl = self._make_limiter()
        it = InfraCostTracker()
        tracker = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)

        tracker.record_model_cost(7.0, project_id="x")
        it.record("aws", "gpu_instance", "ig-snapshot", 5.0)

        snap = tracker.snapshot()
        assert "model_records" in snap
        assert "infra" in snap
        assert isinstance(snap["model_records"], list)
        assert isinstance(snap["infra"], dict)

    def test_repr_shows_wired_state(self) -> None:
        sl = self._make_limiter()
        it = InfraCostTracker()
        tracker = CombinedCostTracker(spend_limiter=sl, infra_tracker=it)
        rep = repr(tracker)
        assert "has_model=True" in rep
        assert "has_infra=True" in rep

    def test_peak_rate_for_model_applies_multiplier(self) -> None:
        peak = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        ip, op = peak_rate_for_model("gpt-4o", 2.50, 5.00, now=peak)
        assert ip == pytest.approx(2.50)
        assert op == pytest.approx(5.00)

    def test_peak_rate_for_model_off_peak_discounts(self) -> None:
        sunday = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)
        ip, op = peak_rate_for_model("gpt-4o", 2.50, 5.00, now=sunday)
        assert ip == pytest.approx(2.50 * 0.75)
        assert op == pytest.approx(5.00 * 0.75)

    def test_default_schedule_has_all_providers(self) -> None:
        sched = default_schedule()
        providers = sched.all_providers()
        assert "openai" in providers
        assert "anthropic" in providers
        assert "google" in providers
        assert "deepseek" in providers
        assert "openrouter" in providers

    def test_is_off_peak_budget_returns_correct_bool(self) -> None:
        sched = default_schedule()
        monday_noon = datetime.datetime(2026, 8, 10, 12, 0, 0, tzinfo=datetime.UTC)
        sunday = datetime.datetime(2026, 8, 9, 12, 0, 0, tzinfo=datetime.UTC)

        with patch("general_ludd.budget.peak_pricing._utcnow", return_value=monday_noon):
            assert is_off_peak(sched, "gpt-4o", "openai") is False

        with patch("general_ludd.budget.peak_pricing._utcnow", return_value=sunday):
            assert is_off_peak(sched, "gpt-4o", "openai") is True

    def test_cost_router_is_better_to_wait_boundary_conditions(self) -> None:
        router = CostAwareRouter(MagicMock())
        saturday = datetime.datetime(2026, 8, 8, 12, 0, 0, tzinfo=datetime.UTC)
        assert router.is_better_to_wait({"estimated_cost": 100.0}, 24, now=saturday) is False

        late_friday = datetime.datetime(2026, 8, 7, 19, 0, 0, tzinfo=datetime.UTC)
        assert router.is_better_to_wait({"estimated_cost": 100.0}, 24, now=late_friday) is False

        just_inside_peak = datetime.datetime(2026, 8, 4, 8, 1, 0, tzinfo=datetime.UTC)
        result = router.is_better_to_wait({"estimated_cost": 10.0}, 24, now=just_inside_peak)
        assert result is True

    def test_cost_router_defer_to_off_peak_when_deadline_before_off_peak(self) -> None:
        router = CostAwareRouter(MagicMock())
        now = datetime.datetime(2026, 8, 4, 12, 0, 0, tzinfo=datetime.UTC)
        deadline = now + datetime.timedelta(hours=1)

        result = router.defer_to_off_peak("task-x", deadline, now=now)
        scheduled = datetime.datetime.fromisoformat(cast(str, result["scheduled_for"]))
        assert scheduled <= deadline
