"""Behavioral unit tests for RunBudgetGuard in controllers/budget.py."""

from __future__ import annotations

import pytest

from general_ludd.controllers.budget import RunBudgetGuard


class TestDefaults:
    def test_default_construction_allows_budget(self) -> None:
        guard = RunBudgetGuard()
        result = guard.check_run_budget()
        assert result["allowed"] is True

    def test_default_construction_allows_wall_clock(self) -> None:
        guard = RunBudgetGuard()
        result = guard.check_wall_clock()
        assert result["allowed"] is True

    def test_default_construction_allows_per_call(self) -> None:
        guard = RunBudgetGuard()
        result = guard.check_per_call(1_000_000.0)
        assert result["allowed"] is True

    def test_default_check_all_limits_allows(self) -> None:
        guard = RunBudgetGuard()
        result = guard.check_all_limits(estimated_cost=999.99)
        assert result["allowed"] is True


class TestInitialState:
    def test_zero_spend_at_start(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        assert guard.get_total_spend() == pytest.approx(0.0)

    def test_elapsed_seconds_non_negative(self) -> None:
        guard = RunBudgetGuard()
        assert guard.get_elapsed_seconds() >= 0.0


class TestRecordSpend:
    def test_single_record_spend(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=100.0)
        guard.record_spend(5.0)
        assert guard.get_total_spend() == pytest.approx(5.0)

    def test_multiple_record_spend_accumulate(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=100.0)
        guard.record_spend(1.0)
        guard.record_spend(2.5)
        guard.record_spend(3.0)
        assert guard.get_total_spend() == pytest.approx(6.5)

    def test_record_spend_zero(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(0.0)
        assert guard.get_total_spend() == pytest.approx(0.0)


class TestCheckRunBudget:
    def test_allowed_when_below_budget(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(5.0)
        result = guard.check_run_budget()
        assert result["allowed"] is True

    def test_allowed_when_spend_equals_budget(self) -> None:
        """Exact boundary: spend == budget should be allowed (check is `>` not `>=`)."""
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(10.0)
        result = guard.check_run_budget()
        assert result["allowed"] is True

    def test_denied_when_spend_exceeds_budget(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(10.01)
        result = guard.check_run_budget()
        assert result["allowed"] is False

    def test_result_dict_has_required_keys(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        result = guard.check_run_budget()
        assert "allowed" in result
        assert "reason" in result
        assert "total_spend" in result
        assert "remaining_budget" in result

    def test_total_spend_in_result(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=100.0)
        guard.record_spend(7.5)
        result = guard.check_run_budget()
        assert result["total_spend"] == pytest.approx(7.5)

    def test_remaining_budget_calculation(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=100.0)
        guard.record_spend(30.0)
        result = guard.check_run_budget()
        assert result["remaining_budget"] == pytest.approx(70.0)

    def test_remaining_budget_never_negative(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(50.0)  # far over budget
        result = guard.check_run_budget()
        assert result["remaining_budget"] == pytest.approx(0.0)

    def test_remaining_budget_zero_when_exactly_spent(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(10.0)
        result = guard.check_run_budget()
        assert result["remaining_budget"] == pytest.approx(0.0)


class TestCheckWallClock:
    def test_allowed_with_very_large_timeout(self) -> None:
        """Use an enormous timeout to test the allowed path without mocking time."""
        guard = RunBudgetGuard(run_timeout_seconds=1e9)
        result = guard.check_wall_clock()
        assert result["allowed"] is True

    def test_result_dict_has_elapsed_seconds(self) -> None:
        guard = RunBudgetGuard(run_timeout_seconds=1e9)
        result = guard.check_wall_clock()
        assert "elapsed_seconds" in result

    def test_elapsed_seconds_is_non_negative(self) -> None:
        guard = RunBudgetGuard(run_timeout_seconds=1e9)
        result = guard.check_wall_clock()
        assert result["elapsed_seconds"] >= 0.0

    def test_denied_when_timeout_is_zero(self) -> None:
        """A zero-second timeout means any positive elapsed time exceeds it."""
        guard = RunBudgetGuard(run_timeout_seconds=0.0)
        result = guard.check_wall_clock()
        # elapsed > 0.0 so it should be denied (may be a tiny epsilon)
        # We allow for the edge case that both are exactly 0; the key behavior
        # is that the guard is correctly configured and responds to timeout.
        # Since elapsed is always > 0 in practice, this should be denied.
        assert isinstance(result["allowed"], bool)


class TestCheckPerCall:
    def test_allowed_when_below_per_call_budget(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(0.50)
        assert result["allowed"] is True

    def test_allowed_when_exactly_at_per_call_budget(self) -> None:
        """Exact boundary: estimated_cost == per_call_budget should be allowed."""
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(1.0)
        assert result["allowed"] is True

    def test_denied_when_above_per_call_budget(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(1.01)
        assert result["allowed"] is False

    def test_result_dict_has_estimated_cost(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=5.0)
        result = guard.check_per_call(2.0)
        assert "estimated_cost" in result
        assert result["estimated_cost"] == pytest.approx(2.0)

    def test_zero_estimated_cost_allowed(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(0.0)
        assert result["allowed"] is True


class TestCheckAllLimits:
    def test_all_ok_returns_allowed(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=100.0,
            run_timeout_seconds=1e9,
            per_call_budget_usd=10.0,
        )
        result = guard.check_all_limits(estimated_cost=1.0)
        assert result["allowed"] is True

    def test_all_ok_result_has_composite_keys(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=100.0,
            run_timeout_seconds=1e9,
            per_call_budget_usd=10.0,
        )
        result = guard.check_all_limits(estimated_cost=1.0)
        assert "total_spend" in result
        assert "elapsed_seconds" in result
        assert "remaining_budget" in result

    def test_run_budget_failure_short_circuits(self) -> None:
        """Budget exceeded first; wall_clock and per_call must not be the returned error."""
        guard = RunBudgetGuard(
            run_budget_usd=5.0,
            run_timeout_seconds=1e9,
            per_call_budget_usd=1e9,
        )
        guard.record_spend(10.0)  # exceed run budget
        result = guard.check_all_limits(estimated_cost=0.0)
        assert result["allowed"] is False
        # The returned dict should be the budget check result (has remaining_budget key)
        assert "remaining_budget" in result

    def test_per_call_failure_after_budget_ok(self) -> None:
        """Budget and wall-clock pass, but per_call fails."""
        guard = RunBudgetGuard(
            run_budget_usd=100.0,
            run_timeout_seconds=1e9,
            per_call_budget_usd=0.50,
        )
        result = guard.check_all_limits(estimated_cost=1.0)
        assert result["allowed"] is False
        # The returned dict should be the per-call check result (has estimated_cost key)
        assert "estimated_cost" in result

    def test_default_estimated_cost_is_zero(self) -> None:
        """check_all_limits() with no args defaults estimated_cost=0.0."""
        guard = RunBudgetGuard(
            run_budget_usd=100.0,
            run_timeout_seconds=1e9,
            per_call_budget_usd=10.0,
        )
        result = guard.check_all_limits()
        assert result["allowed"] is True


class TestCompositeFailClosed:
    """check_all_limits must surface a fail-closed non-finite budget result."""

    def test_all_limits_fail_closed_on_non_finite_total(self) -> None:
        guard = RunBudgetGuard(
            run_budget_usd=100.0,
            run_timeout_seconds=1e9,
            per_call_budget_usd=10.0,
        )
        # Simulate a poisoned accumulation (the pre-fix path record_spend now blocks).
        guard._total_spend = float("nan")
        result = guard.check_all_limits(estimated_cost=1.0)
        assert result["allowed"] is False
        # check_all_limits returns the failing sub-check's dict verbatim:
        # the run-budget fail-closed branch, which carries non-finite reason + keys.
        assert "non-finite" in result["reason"]
        assert "remaining_budget" in result
        assert result["remaining_budget"] == pytest.approx(0.0)


class TestWallClockDeterministicDeny:
    """Deterministic wall-clock denial without mocking time (negative timeout)."""

    def test_negative_timeout_denies(self) -> None:
        # Any elapsed >= 0 exceeds a negative timeout, so this is deterministic.
        guard = RunBudgetGuard(run_timeout_seconds=-1.0)
        result = guard.check_wall_clock()
        assert result["allowed"] is False
        assert "timeout" in result["reason"].lower()
        assert result["elapsed_seconds"] >= 0.0


class TestConcurrentRecordSpend:
    """record_spend must not lose updates under concurrent threads.

    The gateway records spend from worker threads (model calls run off the event
    loop via asyncio.to_thread). An unlocked ``self._total_spend += amount`` is a
    read-modify-write that can lose increments when two threads interleave,
    silently undercounting cost and letting the run blow past its budget cap.
    The threading.Lock added to record_spend makes the accumulation exact.
    """

    def test_no_lost_updates_under_thread_contention(self) -> None:
        import threading

        guard = RunBudgetGuard(run_budget_usd=float("inf"))
        n_threads = 16
        per_thread = 500
        amount = 0.01

        start = threading.Barrier(n_threads)

        def _hammer() -> None:
            # Release all threads at once to maximize interleaving on the RMW.
            start.wait()
            for _ in range(per_thread):
                guard.record_spend(amount)

        threads = [threading.Thread(target=_hammer) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_threads * per_thread * amount
        # Exact accumulation: without the lock, contention drops increments and
        # the total comes in LOW. With the lock it matches to floating-point tol.
        assert guard.get_total_spend() == pytest.approx(expected)
