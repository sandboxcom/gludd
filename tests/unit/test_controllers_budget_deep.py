"""Deep edge-case tests for RunBudgetGuard in controllers/budget.py.

Covers: float precision boundaries, thread-safety edges (concurrent reads
during writes), composability/priority-order edges, constructor extremes,
result-dict immutability, and get_elapsed_seconds monotonicity.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from general_ludd.controllers.budget import RunBudgetGuard

# ---------------------------------------------------------------------------
# record_spend — deep edges
# ---------------------------------------------------------------------------


class TestRecordSpendDeep:
    def test_record_spend_negative_zero_accepted(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(-0.0)
        assert guard.get_total_spend() == pytest.approx(0.0)

    def test_record_spend_float_epsilon(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=1.0)
        eps = sys.float_info.min
        guard.record_spend(eps)
        assert guard.get_total_spend() == pytest.approx(eps)

    def test_record_spend_very_large_finite(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=float("inf"))
        huge = 1e308
        guard.record_spend(huge)
        assert guard.get_total_spend() == pytest.approx(huge)

    def test_record_spend_denormal(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(1e-310)
        assert guard.get_total_spend() == pytest.approx(1e-310)

    def test_record_spend_negative_infinity_raises(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        with pytest.raises(ValueError, match="finite"):
            guard.record_spend(float("-inf"))

    def test_record_spend_many_tiny_amounts_accumulate(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        for _ in range(10000):
            guard.record_spend(0.0001)
        assert guard.get_total_spend() == pytest.approx(1.0, rel=1e-9)

    def test_record_spend_sequence_then_negative_after_positive_raises(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(1.0)
        with pytest.raises(ValueError, match="finite"):
            guard.record_spend(-0.01)
        assert guard.get_total_spend() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# check_run_budget — deep edges beyond existing coverage
# ---------------------------------------------------------------------------


class TestCheckRunBudgetDeep:
    def test_fail_closed_on_positive_infinity_total(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=100.0)
        guard._total_spend = float("inf")
        result = guard.check_run_budget()
        assert result["allowed"] is False
        assert "non-finite" in str(str(result["reason"]))

    def test_fail_closed_on_negative_infinity_total(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=100.0)
        guard._total_spend = float("-inf")
        result = guard.check_run_budget()
        assert result["allowed"] is False
        assert "non-finite" in str(str(result["reason"]))

    def test_run_budget_zero_with_zero_spend_allowed(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=0.0)
        result = guard.check_run_budget()
        assert result["allowed"] is True
        assert result["remaining_budget"] == pytest.approx(0.0)
        assert result["total_spend"] == pytest.approx(0.0)

    def test_run_budget_zero_with_any_spend_denies(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=0.0)
        guard.record_spend(0.0001)
        result = guard.check_run_budget()
        assert result["allowed"] is False

    def test_spend_exceeds_by_tiny_margin(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(10.0 + 1e-12)
        result = guard.check_run_budget()
        assert result["allowed"] is False

    def test_non_finite_reason_includes_value_repr(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard._total_spend = float("nan")
        result = guard.check_run_budget()
        assert "nan" in str(str(result["reason"])).lower()

    def test_exceeds_by_overflow_magnitude_denies_cleanly(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=1.0)
        guard.record_spend(1e300)
        result = guard.check_run_budget()
        assert result["allowed"] is False
        assert result["remaining_budget"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# check_wall_clock — deep edges
# ---------------------------------------------------------------------------


class TestCheckWallClockDeep:
    def test_negative_large_timeout_denies(self) -> None:
        guard = RunBudgetGuard(run_timeout_seconds=-1000.0)
        result = guard.check_wall_clock()
        assert result["allowed"] is False

    def test_elapsed_seconds_monotonically_increases(self) -> None:
        guard = RunBudgetGuard(run_timeout_seconds=1e9)
        r1 = guard.check_wall_clock()
        r2 = guard.check_wall_clock()
        e1: float = r1["elapsed_seconds"]  # type: ignore[assignment]
        e2: float = r2["elapsed_seconds"]  # type: ignore[assignment]
        assert e2 >= e1

    def test_wall_clock_result_exact_keys(self) -> None:
        guard = RunBudgetGuard(run_timeout_seconds=1e9)
        result = guard.check_wall_clock()
        assert set(result.keys()) == {"allowed", "reason", "elapsed_seconds"}

    def test_wall_clock_elapsed_close_to_get_elapsed(self) -> None:
        guard = RunBudgetGuard(run_timeout_seconds=1e9)
        before = guard.get_elapsed_seconds()
        result = guard.check_wall_clock()
        after = guard.get_elapsed_seconds()
        elapsed: float = result["elapsed_seconds"]  # type: ignore[assignment]
        assert before <= elapsed <= after + 1e-6

    def test_timeout_is_nan_should_not_crash(self) -> None:
        """NaN timeout: elapsed > NaN is always False, so allowed=True."""
        guard = RunBudgetGuard(run_timeout_seconds=float("nan"))
        result = guard.check_wall_clock()
        assert isinstance(result["allowed"], bool)


# ---------------------------------------------------------------------------
# check_per_call — deep edges
# ---------------------------------------------------------------------------


class TestCheckPerCallDeep:
    def test_negative_estimated_cost_allowed(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(-0.01)
        assert result["allowed"] is True

    def test_negative_infinity_cost_denied(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(float("-inf"))
        assert result["allowed"] is False
        assert "non-finite" in str(result["reason"])

    def test_epsilon_above_cap_denies(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(1.0 + 1e-12)
        assert result["allowed"] is False

    def test_per_call_zero_budget_denies_positive_cost(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=0.0)
        result = guard.check_per_call(0.0001)
        assert result["allowed"] is False

    def test_per_call_zero_budget_allows_zero_cost(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=0.0)
        result = guard.check_per_call(0.0)
        assert result["allowed"] is True

    def test_per_call_denied_reason_includes_values(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(5.0)
        assert "5.0000" in str(result["reason"])
        assert "1.0000" in str(result["reason"])

    def test_per_call_result_exact_keys(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(0.5)
        assert set(result.keys()) == {"allowed", "reason", "estimated_cost"}


# ---------------------------------------------------------------------------
# check_all_limits — deep edges (priority short-circuit + composability)
# ---------------------------------------------------------------------------


class TestCheckAllLimitsDeep:
    def test_wall_clock_fails_short_circuits_before_per_call(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=float("inf"),
            run_timeout_seconds=-1.0,
            per_call_budget_usd=1.0,
        )
        result = guard.check_all_limits(estimated_cost=10.0)
        assert result["allowed"] is False
        assert "elapsed_seconds" in result
        assert "estimated_cost" not in result

    def test_all_allowed_has_five_keys(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=float("inf"),
            run_timeout_seconds=1e9,
            per_call_budget_usd=float("inf"),
        )
        result = guard.check_all_limits()
        assert set(result.keys()) == {
            "allowed",
            "reason",
            "total_spend",
            "elapsed_seconds",
            "remaining_budget",
        }

    def test_default_estimated_cost_zero(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=float("inf"),
            run_timeout_seconds=1e9,
            per_call_budget_usd=0.001,
        )
        result = guard.check_all_limits()
        assert result["allowed"] is True

    def test_all_limits_with_inf_cost_denied(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=float("inf"),
            run_timeout_seconds=1e9,
            per_call_budget_usd=float("inf"),
        )
        result = guard.check_all_limits(float("inf"))
        assert result["allowed"] is False
        assert "non-finite" in str(result["reason"])

    def test_run_budget_non_finite_supersedes_other_failures(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=100.0,
            run_timeout_seconds=-1.0,
            per_call_budget_usd=0.0,
        )
        guard._total_spend = float("nan")
        result = guard.check_all_limits(estimated_cost=1.0)
        assert result["allowed"] is False
        assert "non-finite" in str(result["reason"])

    def test_priority_order_budget_over_wall(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=1.0, run_timeout_seconds=-1.0)
        guard.record_spend(2.0)
        r = guard.check_all_limits(estimated_cost=0.0)
        assert "remaining_budget" in r
        assert "elapsed_seconds" not in r

    def test_priority_order_wall_over_per_call(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=float("inf"),
            run_timeout_seconds=-1.0,
            per_call_budget_usd=0.0,
        )
        r = guard.check_all_limits(estimated_cost=1.0)
        assert "elapsed_seconds" in r
        assert "estimated_cost" not in r

    def test_priority_order_per_call_last(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=float("inf"),
            run_timeout_seconds=1e9,
            per_call_budget_usd=0.0,
        )
        r = guard.check_all_limits(estimated_cost=1.0)
        assert "estimated_cost" in r
        assert "remaining_budget" not in r


# ---------------------------------------------------------------------------
# Thread safety — concurrent reads during writes
# ---------------------------------------------------------------------------


class TestThreadSafetyDeep:
    def test_get_total_spend_during_record_spend_no_crash(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=float("inf"))
        errors: list[Exception] = []
        done = threading.Event()

        def writer() -> None:
            for _ in range(10000):
                guard.record_spend(0.001)
            done.set()

        def reader() -> None:
            while not done.is_set():
                try:
                    _ = guard.get_total_spend()
                except Exception as exc:
                    errors.append(exc)

        t_w = threading.Thread(target=writer)
        t_r = threading.Thread(target=reader)
        t_w.start()
        t_r.start()
        t_w.join()
        t_r.join()
        assert len(errors) == 0

    def test_many_threads_record_spend_exact_accumulation(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=float("inf"))
        n_threads = 32
        per_thread = 1000
        amount = 0.01

        def worker() -> None:
            for _ in range(per_thread):
                guard.record_spend(amount)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        expected = n_threads * per_thread * amount
        assert guard.get_total_spend() == pytest.approx(expected)

    def test_check_all_limits_concurrent_with_record_spend_no_crash(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=float("inf"),
            run_timeout_seconds=1e9,
            per_call_budget_usd=float("inf"),
        )
        done = threading.Event()
        errors: list[Exception] = []

        def writer() -> None:
            for _ in range(5000):
                guard.record_spend(0.001)
            done.set()

        def checker() -> None:
            while not done.is_set():
                try:
                    guard.check_all_limits()
                    guard.check_run_budget()
                    guard.check_wall_clock()
                    guard.check_per_call(0.001)
                except Exception as exc:
                    errors.append(exc)

        t_w = threading.Thread(target=writer)
        t_c = threading.Thread(target=checker)
        t_w.start()
        t_c.start()
        t_w.join()
        t_c.join()
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Result dict immutability
# ---------------------------------------------------------------------------


class TestResultImmutability:
    def test_modifying_result_dict_does_not_affect_internal_state(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(3.0)
        result = guard.check_run_budget()
        result["total_spend"] = 999.0
        assert guard.get_total_spend() == pytest.approx(3.0)

    def test_repeated_check_run_budget_consistent(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(5.0)
        r1 = guard.check_run_budget()
        r2 = guard.check_run_budget()
        assert r1["allowed"] == r2["allowed"]
        assert r1["total_spend"] == pytest.approx(r2["total_spend"])


# ---------------------------------------------------------------------------
# Constructor extremes
# ---------------------------------------------------------------------------


class TestConstructorDeep:
    def test_explicit_inf_limits_all_pass(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=float("inf"),
            run_timeout_seconds=float("inf"),
            per_call_budget_usd=float("inf"),
        )
        assert guard.check_run_budget()["allowed"] is True
        assert guard.check_wall_clock()["allowed"] is True
        assert guard.check_per_call(1e100)["allowed"] is True

    def test_all_zero_limits_initial_spend_ok(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=0.0,
            run_timeout_seconds=0.0,
            per_call_budget_usd=0.0,
        )
        assert guard.check_run_budget()["allowed"] is True
        assert guard.check_per_call(0.0)["allowed"] is True

    def test_negative_run_budget_denies_immediately(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=-0.01)
        result = guard.check_run_budget()
        assert result["allowed"] is False

    def test_negative_per_call_budget_denies_zero_cost(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=-0.01)
        result = guard.check_per_call(0.0)
        assert result["allowed"] is False


# ---------------------------------------------------------------------------
# get_elapsed_seconds — monotonicity
# ---------------------------------------------------------------------------


class TestGetElapsedSecondsDeep:
    def test_elapsed_increases_over_sleep(self) -> None:
        guard = RunBudgetGuard()
        e1 = guard.get_elapsed_seconds()
        time.sleep(0.01)
        e2 = guard.get_elapsed_seconds()
        assert e2 >= e1

    def test_elapsed_never_negative(self) -> None:
        guard = RunBudgetGuard()
        for _ in range(100):
            assert guard.get_elapsed_seconds() >= 0.0


# ---------------------------------------------------------------------------
# Composite integration — spend exhausts budget across records
# ---------------------------------------------------------------------------


class TestCompositeIntegrationDeep:
    def test_spend_exhausts_budget_across_many_small_records(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=1.0)
        for _ in range(8):
            guard.record_spend(0.125)
        result = guard.check_run_budget()
        assert result["allowed"] is True
        assert result["remaining_budget"] == pytest.approx(0.0)
        guard.record_spend(0.125)
        result2 = guard.check_run_budget()
        assert result2["allowed"] is False

    def test_per_call_independent_of_run_budget(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=0.0,
            per_call_budget_usd=10.0,
        )
        assert guard.check_per_call(5.0)["allowed"] is True

    def test_per_call_and_run_budget_both_can_fail_independently(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=1.0,
            per_call_budget_usd=0.50,
        )
        guard.record_spend(2.0)
        assert guard.check_run_budget()["allowed"] is False
        assert guard.check_per_call(1.0)["allowed"] is False
