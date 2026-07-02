"""Regression tests for RunBudgetGuard and BudgetManager guard fixes.

Bug A: record_spend accepted NaN/negative -> poisoned _total_spend.
Bug B: check_todo_budget/check_daily_budget were unlocked -> TOCTOU overshoot.
Bug C: check_daily_budget + check_todo_budget each reserved projected cost, then
       record_spend added actual on top -> double/triple-count regression.
"""
from __future__ import annotations

import threading

import pytest

from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.controllers.budget_manager import BudgetManager

# ---------------------------------------------------------------------------
# Fix A: RunBudgetGuard.record_spend validation
# ---------------------------------------------------------------------------


def test_record_spend_rejects_nan() -> None:
    """record_spend must raise ValueError for NaN to prevent poisoned totals."""
    guard = RunBudgetGuard(run_budget_usd=10.0)
    with pytest.raises(ValueError, match="finite"):
        guard.record_spend(float("nan"))


def test_record_spend_rejects_negative() -> None:
    """record_spend must raise ValueError for negative amounts."""
    guard = RunBudgetGuard(run_budget_usd=10.0)
    with pytest.raises(ValueError, match="finite"):
        guard.record_spend(-0.01)


def test_record_spend_rejects_inf() -> None:
    """record_spend must raise ValueError for +inf."""
    guard = RunBudgetGuard(run_budget_usd=10.0)
    with pytest.raises(ValueError, match="finite"):
        guard.record_spend(float("inf"))


# ---------------------------------------------------------------------------
# Fix A: RunBudgetGuard.check_run_budget fail-closed on non-finite total
# ---------------------------------------------------------------------------


def test_check_run_budget_fail_closed_on_nan_total() -> None:
    """check_run_budget must return allowed=False when _total_spend is NaN.

    NaN > any_cap evaluates to False in Python, so without the explicit
    isfinite guard the budget check would silently pass with a poisoned total.
    """
    guard = RunBudgetGuard(run_budget_usd=100.0)
    # Inject a non-finite total directly (simulating the pre-fix poisoning path).
    guard._total_spend = float("nan")
    result = guard.check_run_budget()
    assert result["allowed"] is False
    assert "non-finite" in result["reason"]


# ---------------------------------------------------------------------------
# RunBudgetGuard.check_per_call fail-closed on non-finite estimated cost
# ---------------------------------------------------------------------------


def test_check_per_call_denies_nan() -> None:
    """check_per_call must fail CLOSED on a NaN cost — `NaN > cap` is False, so
    without the isfinite guard a poisoned cost would slip through the gate."""
    guard = RunBudgetGuard(per_call_budget_usd=1.0)
    result = guard.check_per_call(float("nan"))
    assert result["allowed"] is False
    assert "non-finite" in result["reason"]


def test_check_per_call_denies_inf() -> None:
    """check_per_call must fail CLOSED on +inf."""
    guard = RunBudgetGuard(per_call_budget_usd=1.0)
    result = guard.check_per_call(float("inf"))
    assert result["allowed"] is False
    assert "non-finite" in result["reason"]


def test_check_per_call_allows_finite_under_budget() -> None:
    """Regression: a normal finite under-budget cost is still allowed."""
    guard = RunBudgetGuard(per_call_budget_usd=1.0)
    result = guard.check_per_call(0.5)
    assert result["allowed"] is True


def test_check_all_limits_inherits_per_call_nan_denial() -> None:
    """check_all_limits delegates to check_per_call, so a NaN cost is denied."""
    guard = RunBudgetGuard(per_call_budget_usd=1.0)
    result = guard.check_all_limits(float("nan"))
    assert result["allowed"] is False


# ---------------------------------------------------------------------------
# Fix B: BudgetManager.check_todo_budget atomicity (TOCTOU)
# ---------------------------------------------------------------------------


def test_check_todo_budget_atomic_no_overshoot() -> None:
    """Concurrent check_todo_budget calls on DISTINCT todos must not overshoot.

    With a per_todo_limit of $1.00, two threads racing on two different todo
    ids ("t1"/"t2") that share an already-spent ledger compete for the same
    remaining headroom. Before the lock fix, both could read the same stale
    current, both pass the cap check, and both record — overshooting the cap.
    The lock serialises the read-modify-write so exactly one wins.

    (Two *concurrent* calls on the SAME todo id are a single logical
    reservation that replaces, not stacks — covered by the non-mutating /
    retry-idempotency tests — so this race uses distinct ids to exercise a
    genuine cap-overshoot scenario rather than the replace path.)
    """
    manager = BudgetManager(per_todo_limit_usd=1.0)
    # Seed each todo's ledger so a further $0.60 would exceed the $1.00 cap,
    # forcing the two reservations to compete for the headroom under the lock.
    manager.record_spend("t1", 0.6)
    manager.record_spend("t2", 0.6)
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def worker(todo_id: str) -> None:
        barrier.wait()  # both threads hit the check at the same instant
        result = manager.check_todo_budget(todo_id, 0.6)
        results.append(result)

    threads = [
        threading.Thread(target=worker, args=(tid,)) for tid in ("t1", "t2")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 0.6 (prior) + 0.6 (new) = 1.2 > 1.0 for each todo -> both denied.
    allowed_count = sum(1 for r in results if r["allowed"])
    denied_count = sum(1 for r in results if not r["allowed"])
    assert allowed_count == 0, f"Expected 0 allowed, got {allowed_count}; results={results}"
    assert denied_count == 2, f"Expected 2 denied, got {denied_count}; results={results}"


def test_check_todo_budget_concurrent_same_todo_replaces_not_stacks() -> None:
    """Concurrent checks on the SAME todo collapse to one replacing hold.

    A retry that races with itself on the same todo_id must not double-count
    its own outstanding reservation against the cap: both calls reserve the
    same $0.60 hold (replace semantics), so both are allowed and the recorded
    hold stays a single $0.60 — well within the $1.00 cap (no overshoot).
    """
    manager = BudgetManager(per_todo_limit_usd=1.0)
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        results.append(manager.check_todo_budget("t1", 0.6))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r["allowed"] for r in results), f"results={results}"
    # The hold replaced rather than stacked: a single $0.60 reservation.
    assert manager._todo_spend["t1"] == pytest.approx(0.6)
    assert manager._todo_reservations["t1"] == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Fix B: BudgetManager.check_daily_budget atomicity (TOCTOU)
# ---------------------------------------------------------------------------


def test_check_daily_budget_atomic_no_overshoot() -> None:
    """Two concurrent check_daily_budget calls must not both succeed.

    With a daily_limit of $1.00 and two threads each requesting $0.60,
    only one should be allowed. Before the fix (unlocked), both could read
    _daily_spend=0.0, pass the cap, and record — totalling $1.20.
    """
    manager = BudgetManager(daily_limit_usd=1.0)
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        result = manager.check_daily_budget(0.6)
        results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    allowed_count = sum(1 for r in results if r["allowed"])
    denied_count = sum(1 for r in results if not r["allowed"])
    assert allowed_count == 1, f"Expected exactly 1 allowed, got {allowed_count}; results={results}"
    assert denied_count == 1, f"Expected exactly 1 denied, got {denied_count}; results={results}"
    # The allowed result must have "charged": True (atomic record)
    allowed_result = next(r for r in results if r["allowed"])
    assert allowed_result.get("charged") is True


# ---------------------------------------------------------------------------
# Fix B: BudgetManager.record_spend validation
# ---------------------------------------------------------------------------


def test_budget_manager_record_spend_rejects_nan() -> None:
    """BudgetManager.record_spend must raise ValueError for NaN."""
    manager = BudgetManager()
    with pytest.raises(ValueError, match="finite"):
        manager.record_spend("t1", float("nan"))


def test_budget_manager_record_spend_rejects_negative() -> None:
    """BudgetManager.record_spend must raise ValueError for negative amounts."""
    manager = BudgetManager()
    with pytest.raises(ValueError, match="finite"):
        manager.record_spend("t1", -1.0)


# ---------------------------------------------------------------------------
# Fix C: reserve->reconcile flow (double-count regression)
# ---------------------------------------------------------------------------


def test_reserve_then_record_reconciles_to_actual() -> None:
    """After check_daily_budget_reserved + check_todo_budget + record_spend,
    both ledgers must equal the ACTUAL cost, not projected + actual.

    Regression: _gateway_executor called check_daily_budget (reserved projected)
    + check_todo_budget (reserved projected) then record_spend(actual) which
    added actual on top -> ledgers showed projected+actual instead of actual.
    """
    projected = 0.05
    actual = 0.03
    manager = BudgetManager(daily_limit_usd=10.0, per_todo_limit_usd=5.0)

    daily_result = manager.check_daily_budget_reserved("task-1", projected)
    assert daily_result["allowed"] is True, daily_result
    assert daily_result.get("charged") is True

    todo_result = manager.check_todo_budget("task-1", projected)
    assert todo_result["allowed"] is True, todo_result
    assert todo_result.get("charged") is True

    # Ledgers hold the reservation before reconciliation.
    assert manager._daily_spend == pytest.approx(projected)
    assert manager._todo_spend["task-1"] == pytest.approx(projected)

    manager.record_spend("task-1", actual)

    assert manager._daily_spend == pytest.approx(actual), (
        f"_daily_spend={manager._daily_spend!r} != actual={actual!r} "
        "(double-count regression: got projected+actual?)"
    )
    assert manager._todo_spend["task-1"] == pytest.approx(actual), (
        f"_todo_spend={manager._todo_spend['task-1']!r} != actual={actual!r}"
    )


def test_reserve_then_record_zero_actual() -> None:
    """record_spend(0.0) after reserving projected must zero the ledgers."""
    manager = BudgetManager(daily_limit_usd=10.0, per_todo_limit_usd=5.0)
    manager.check_daily_budget_reserved("task-2", 0.05)
    manager.check_todo_budget("task-2", 0.05)
    manager.record_spend("task-2", 0.0)
    assert manager._daily_spend == pytest.approx(0.0)
    assert manager._todo_spend["task-2"] == pytest.approx(0.0)


def test_no_reservation_record_spend_plain_add() -> None:
    """record_spend without a prior reservation is a plain add (backward compat)."""
    manager = BudgetManager()
    manager.record_spend("task-3", 0.07)
    assert manager._daily_spend == pytest.approx(0.07)
    assert manager._todo_spend["task-3"] == pytest.approx(0.07)
    manager.record_spend("task-3", 0.03)
    assert manager._daily_spend == pytest.approx(0.10)
    assert manager._todo_spend["task-3"] == pytest.approx(0.10)


def test_daily_budget_reserved_retry_replaces_reservation() -> None:
    """A second check_daily_budget_reserved before record_spend replaces the
    prior reservation so retries don't double-hold budget."""
    manager = BudgetManager(daily_limit_usd=10.0, per_todo_limit_usd=5.0)
    manager.check_daily_budget_reserved("task-4", 0.10)
    manager.check_todo_budget("task-4", 0.10)
    # Retry: re-check with the same projected cost.
    manager.check_daily_budget_reserved("task-4", 0.10)
    manager.check_todo_budget("task-4", 0.10)
    # Ledger still holds exactly one reservation.
    assert manager._daily_spend == pytest.approx(0.10)
    assert manager._todo_spend["task-4"] == pytest.approx(0.10)
    manager.record_spend("task-4", 0.08)
    assert manager._daily_spend == pytest.approx(0.08)
    assert manager._todo_spend["task-4"] == pytest.approx(0.08)


def test_release_reservation_frees_held_budget() -> None:
    """release_reservation reverses an unreconciled reservation (abort path)."""
    manager = BudgetManager(daily_limit_usd=10.0, per_todo_limit_usd=5.0)
    manager.check_daily_budget_reserved("task-5", 0.05)
    manager.check_todo_budget("task-5", 0.05)
    assert manager._daily_spend == pytest.approx(0.05)
    manager.release_reservation("task-5")
    assert manager._daily_spend == pytest.approx(0.0)
    assert manager._todo_spend["task-5"] == pytest.approx(0.0)
    # Idempotent: a second release is a no-op and a later record_spend is a plain add.
    manager.release_reservation("task-5")
    assert manager._daily_spend == pytest.approx(0.0)
