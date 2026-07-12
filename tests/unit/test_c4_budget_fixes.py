"""C4 budget/spend correctness family — SECURITY P1.

F1: estimate_call_cost returns conservative default for unknown models (not 0.0)
F4: SpendLimiter.restore seeds monotonic timestamp across restarts
F5: SpendLimiter.restore does not double-count on repeated restore
F6: BudgetManager daily rollover clears stale reservations
"""

from __future__ import annotations

import logging

import pytest

from general_ludd.controllers.budget_manager import BudgetManager
from general_ludd.controllers.pid import BudgetController
from general_ludd.controllers.spend_limiter import SpendLimiter

# ---------------------------------------------------------------------------
# F1: estimate_call_cost unknown-model fallback
# ---------------------------------------------------------------------------

class TestEstimateCallCostUnknownModel:
    def test_estimate_call_cost_unknown_model_returns_conservative_default(
        self,
    ) -> None:
        """When cost_per_1k is 0.0 (unknown/unpriced model), the cost must NOT
        be 0.0 — treating an unknown model as free is a security bug.  Instead
        a configurable conservative default must be used.
        """
        controller = BudgetController(
            unknown_model_cost_per_1k_default=0.01,
        )
        cost = controller.estimate_call_cost(tokens=1000, cost_per_1k=0.0)
        assert cost > 0.0, f"Expected positive cost for unknown model, got {cost}"
        assert cost == pytest.approx(0.01), (
            f"Expected 0.01 (default), got {cost}"
        )

    def test_estimate_call_cost_unknown_model_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A WARNING must be logged when an unknown/unpriced model cost is
        projected so operators can audit unknown-model usage.
        """
        controller = BudgetController(
            unknown_model_cost_per_1k_default=0.01,
        )
        with caplog.at_level(logging.WARNING, logger="general_ludd.controllers.pid"):
            controller.estimate_call_cost(tokens=1000, cost_per_1k=0.0)

        assert any(
            "unknown" in record.getMessage().lower() or "unpriced" in record.getMessage().lower()
            for record in caplog.records
        ), f"No unknown/unpriced warning logged; records={caplog.records}"

    def test_estimate_call_cost_known_model_uses_provided_cost(
        self,
    ) -> None:
        """When cost_per_1k > 0 (known/priced model), the provided rate is used
        and the default is NOT substituted.
        """
        controller = BudgetController(
            unknown_model_cost_per_1k_default=0.01,
        )
        cost = controller.estimate_call_cost(tokens=2000, cost_per_1k=0.003)
        assert cost == pytest.approx(0.006), (
            f"Expected 2000/1000 * 0.003 = 0.006, got {cost}"
        )

    def test_unknown_model_cost_default_is_configurable(self) -> None:
        """The conservative default is configurable via constructor kwarg."""
        controller = BudgetController(
            unknown_model_cost_per_1k_default=0.05,
        )
        cost = controller.estimate_call_cost(tokens=500, cost_per_1k=0.0)
        assert cost == pytest.approx(0.025), (
            f"Expected 500/1000 * 0.05 = 0.025, got {cost}"
        )


# ---------------------------------------------------------------------------
# F4: SpendLimiter.restore monotonic-timestamp restart fix
# ---------------------------------------------------------------------------

class TestRestoreSeedsMonotonicTimestamp:
    def test_restore_seeds_monotonic_timestamp(self) -> None:
        """After restore, the next record() call must use a timestamp >= the
        max restored timestamp — not a lower clock value that would break
        monotonicity across the restart boundary.

        Bug: restore() added records with timestamps {10, 20}, then record()
        used self._clock() (starting fresh at ~0) → new record got ts=0
        even though the window already had ts=20. This creates a non-monotonic
        sequence.
        """
        clock_values: list[float] = [25.0, 15.0]
        clock = clock_values.pop

        limiter = SpendLimiter(
            limit_usd=100.0,
            window_seconds=3600.0,
            clock=clock,
        )
        limiter.restore([
            (10.0, 0.50),
            (20.0, 0.75),
        ])
        limiter.record(cost_usd=0.10, kind="token")

        records = limiter.snapshot()
        assert len(records) == 3
        timestamps = [r[0] for r in records]
        assert timestamps == sorted(timestamps), (
            f"Timestamps not monotonic: {timestamps}"
        )
        assert timestamps[2] >= 20.0, (
            f"New record timestamp {timestamps[2]} < max restored 20.0"
        )

    def test_restore_with_clock_ahead_of_restored_uses_clock(
        self,
    ) -> None:
        """When the live clock is ahead of restored timestamps, the clock value
        is used — the floor is not a ceiling.
        """
        clock_values: list[float] = [100.0, 101.0]
        clock = clock_values.pop

        limiter = SpendLimiter(
            limit_usd=100.0,
            window_seconds=3600.0,
            clock=clock,
        )
        limiter.restore([
            (10.0, 0.10),
            (20.0, 0.20),
        ])
        limiter.record(cost_usd=0.30, kind="token")

        timestamps = [r[0] for r in limiter.snapshot()]
        # restore consumed one clock tick (102.0 via pop → now= for clamping),
        # record consumed the next (101.0 via pop). The new ts must be >= max
        # restored (20.0) and equal to the live clock value.
        assert timestamps[2] >= 20.0, (
            f"New record ts={timestamps[2]} < max restored 20.0"
        )
        assert timestamps[2] == pytest.approx(100.0), (
            f"Expected clock value 100.0, got {timestamps[2]}"
        )


# ---------------------------------------------------------------------------
# F5: SpendLimiter.restore double-count fix
# ---------------------------------------------------------------------------

class TestRestoreDoesNotDoubleCount:
    def test_restore_does_not_double_count(self) -> None:
        """charge → snapshot → restore → charge must NOT double-count.

        Bug: restore() blindly extended self._records. If called with the
        same persisted data multiple times (e.g. daemon restart re-reads DB),
        every restore invocation appended duplicate records, inflating
        window_spend().
        """
        clock_values: list[float] = [1.0, 2.0, 6.0, 4.0, 5.0]
        clock = clock_values.pop

        limiter = SpendLimiter(
            limit_usd=100.0,
            window_seconds=3600.0,
            clock=clock,
        )
        limiter.record(cost_usd=0.50, kind="token")
        limiter.record(cost_usd=0.75, kind="token")

        # snapshot the in-memory state
        snap = limiter.snapshot()
        assert len(snap) == 2

        # Restore the SAME records a second time — must be a no-op.
        limiter.restore(snap)

        # After restore, there should still be exactly 2 records, not 4.
        records = limiter.snapshot()
        assert len(records) == 2, (
            f"Expected 2 records after restore, got {len(records)}"
        )

        total = limiter.window_spend(now=6.0)
        assert total == pytest.approx(1.25), (
            f"Expected 0.50 + 0.75 = 1.25, got {total}"
        )

    def test_restore_partial_overlap_only_adds_new(self) -> None:
        """When some restored records are already present and some are new,
        only the new records are added.
        """
        clock_values: list[float] = [0.5, 1.0, 3.0, 4.0, 5.0]
        clock = clock_values.pop

        limiter = SpendLimiter(
            limit_usd=100.0,
            window_seconds=3600.0,
            clock=clock,
        )
        limiter.record(cost_usd=0.50, kind="token")
        # record id #2
        limiter.record(cost_usd=0.75, kind="token")

        # Restore the first record (already present, exact match) + a new third record.
        limiter.restore([
            (5.0, 0.50),   # already present → skip (exact ts match on record #1)
            (0.5, 1.00),   # new → add
        ])

        records = limiter.snapshot()
        assert len(records) == 3, (
            f"Expected 3 records (2 existing + 1 new), got {len(records)}"
        )
        total = limiter.window_spend(now=4.0)
        assert total == pytest.approx(2.25), (
            f"Expected 0.50 + 0.75 + 1.00 = 2.25, got {total}"
        )


# ---------------------------------------------------------------------------
# F6: BudgetManager daily-rollover stale reservation cleanup
# ---------------------------------------------------------------------------

class TestDailyRolloverClearsStaleReservations:
    def test_daily_rollover_clears_stale_reservations(self) -> None:
        """After a daily window rollover, stale todo/daily reservations from
        the previous day MUST be cleared.

        Bug: _reset_daily_if_needed() reset _daily_spend and _paused but
        left _daily_reservations and _todo_reservations populated. A stale
        reservation from the old day could ghost-inflate the new day's
        ledger when reconciled.
        """
        manager = BudgetManager(
            daily_limit_usd=10.0,
            per_todo_limit_usd=5.0,
        )
        # Reserve on the current day.
        manager.check_daily_budget_reserved("task-a", 0.50)
        manager.check_todo_budget("task-a", 0.50)

        assert len(manager._daily_reservations) == 1
        assert len(manager._todo_reservations) == 1

        # Simulate a daily window rollover by back-dating _daily_start.
        manager._daily_start -= 86_401

        # Trigger the rollover via any method that calls _reset_daily_if_needed.
        result = manager.check_daily_budget_reserved("task-b", 0.10)
        assert result["allowed"] is True

        # After rollover the prior day's reservations must be cleared.
        assert len(manager._daily_reservations) == 1, (
            f"Expected only the new day's reservation, "
            f"got {manager._daily_reservations}"
        )
        assert "task-a" not in manager._daily_reservations, (
            "Stale daily reservation task-a should have been cleared on rollover"
        )
        assert "task-a" not in manager._todo_reservations, (
            "Stale todo reservation task-a should have been cleared on rollover"
        )
        assert "task-b" in manager._daily_reservations

    def test_daily_rollover_clears_stale_reservation_then_reconcile(
        self,
    ) -> None:
        """After rollover clears stale reservations, a new reservation +
        reconciliation on the fresh day works correctly (no cross-day
        contamination).
        """
        manager = BudgetManager(
            daily_limit_usd=10.0,
            per_todo_limit_usd=5.0,
        )
        # Day 1: reserve for task-a.
        manager.check_daily_budget_reserved("task-a", 0.30)
        manager.check_todo_budget("task-a", 0.30)
        assert manager._daily_spend == pytest.approx(0.30)

        # Day 1 rolls over.
        manager._daily_start -= 86_401

        # Day 2: reserve for task-b, record, verify ledger.
        r = manager.check_daily_budget_reserved("task-b", 0.20)
        assert r["allowed"] is True
        manager.check_todo_budget("task-b", 0.20)
        manager.record_spend("task-b", 0.15)

        # Task-a's stale reservation is gone; day 2 spend is from task-b only.
        assert manager._daily_spend == pytest.approx(0.15), (
            f"Day 2 daily spend should be 0.15, got {manager._daily_spend}"
        )
        assert manager._todo_spend.get("task-b", 0.0) == pytest.approx(0.15)
        # Task-a should have no lingering ledger entry.
        assert "task-a" not in manager._daily_reservations
        assert "task-a" not in manager._todo_reservations
