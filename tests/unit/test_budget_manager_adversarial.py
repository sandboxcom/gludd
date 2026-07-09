"""Adversarial unit tests for the budget kill-switch (BudgetManager).

These tests assert the CURRENT real behavior of
``general_ludd.controllers.budget_manager.BudgetManager`` as-is (no source
edits). The kill switch fails CLOSED on an uncomputable (NaN/unknown)
estimated cost under a real cap: such a cost is rejected (and, for the daily
check, trips the sticky ``_paused`` switch) rather than silently slipping
through the always-False NaN comparison.

Behavior map (read from source):
  * check_todo_budget(todo, est): strict ``>`` boundary; at-limit ALLOWED,
    over-limit BLOCKED. Non-mutating. Does NOT honor the ``_paused`` kill switch.
  * check_daily_budget(est): honors ``_paused`` first (sticky kill switch);
    strict ``>`` boundary; sets ``_paused=True`` on first breach.
  * record_spend(todo, amt): pure accumulation, NEVER enforces a limit.
  * _reset_daily_if_needed(): rolls over only after >86400s elapsed, and
    clears ``_paused``.
"""

from __future__ import annotations

import math
import time

import pytest

from general_ludd.controllers.budget_manager import BudgetManager

# --------------------------------------------------------------------------
# Per-todo boundary: at-limit vs over-limit (strict ``>``)
# --------------------------------------------------------------------------


def test_todo_at_limit_is_allowed() -> None:
    """Spend exactly equal to the per-todo limit is allowed (strict >)."""
    m = BudgetManager(per_todo_limit_usd=10.0)
    res = m.check_todo_budget("t1", 10.0)
    assert res["allowed"] is True
    assert res["reason"] == "ok"


def test_todo_over_limit_is_blocked() -> None:
    """A hair over the per-todo limit is blocked."""
    m = BudgetManager(per_todo_limit_usd=10.0)
    res = m.check_todo_budget("t1", 10.0001)
    assert res["allowed"] is False
    assert "Per-todo budget exceeded" in res["reason"]


def test_todo_boundary_with_prior_spend() -> None:
    """Prior recorded spend counts toward the per-todo limit."""
    m = BudgetManager(per_todo_limit_usd=10.0)
    m.record_spend("t1", 7.0)
    # 7 + 3 == 10 -> at limit -> allowed
    assert m.check_todo_budget("t1", 3.0)["allowed"] is True
    # 7 + 3.01 > 10 -> blocked
    assert m.check_todo_budget("t1", 3.01)["allowed"] is False


def test_todo_check_is_non_mutating() -> None:
    """check_todo_budget must not record spend; repeated checks are stable."""
    m = BudgetManager(per_todo_limit_usd=10.0)
    for _ in range(5):
        assert m.check_todo_budget("t1", 9.0)["allowed"] is True
    # No spend was accumulated by checking.
    assert m.get_status()["daily_spend"] == 0.0


def test_todo_per_id_isolation() -> None:
    """Each todo id has its own independent per-todo bucket."""
    m = BudgetManager(per_todo_limit_usd=10.0)
    m.record_spend("t1", 9.0)
    # t2 starts fresh, unaffected by t1's spend.
    assert m.check_todo_budget("t2", 9.0)["allowed"] is True
    assert m.check_todo_budget("t1", 2.0)["allowed"] is False


# --------------------------------------------------------------------------
# Daily boundary + sticky kill switch
# --------------------------------------------------------------------------


def test_daily_at_limit_is_allowed_and_not_paused() -> None:
    """Estimate exactly at the daily limit is allowed and does NOT pause."""
    m = BudgetManager(daily_limit_usd=100.0)
    res = m.check_daily_budget(100.0)
    assert res["allowed"] is True
    assert m.get_status()["paused"] is False


def test_daily_over_limit_blocks_and_sets_paused() -> None:
    """First over-limit estimate blocks and trips the sticky kill switch."""
    m = BudgetManager(daily_limit_usd=100.0)
    res = m.check_daily_budget(100.01)
    assert res["allowed"] is False
    assert "Daily budget exceeded" in res["reason"]
    assert m.get_status()["paused"] is True


def test_daily_kill_switch_is_sticky() -> None:
    """Once paused, even a $0 estimate is rejected as budget_exhausted."""
    m = BudgetManager(daily_limit_usd=100.0)
    m.check_daily_budget(200.0)  # trips the switch
    res = m.check_daily_budget(0.0)
    assert res["allowed"] is False
    assert res["reason"] == "budget_exhausted"


def test_daily_pause_blocks_even_a_tiny_charge() -> None:
    """Sticky pause ignores how small the next request is."""
    m = BudgetManager(daily_limit_usd=1.0)
    m.check_daily_budget(5.0)
    assert m.check_daily_budget(0.000001)["allowed"] is False


def test_daily_alert_threshold_does_not_block() -> None:
    """Crossing the alert percentage still allows the call (warning only)."""
    m = BudgetManager(daily_limit_usd=100.0, alert_threshold_pct=80.0)
    m.record_spend("t1", 85.0)  # 85% of daily
    res = m.check_daily_budget(1.0)  # 85 + 1 <= 100
    assert res["allowed"] is True


# --------------------------------------------------------------------------
# Zero / negative budgets
# --------------------------------------------------------------------------


def test_zero_per_todo_limit_blocks_any_positive_cost() -> None:
    """A zero per-todo limit blocks any positive estimate (0 > 0 is False...)."""
    m = BudgetManager(per_todo_limit_usd=0.0)
    # Exactly 0 cost: 0 > 0 is False -> allowed.
    assert m.check_todo_budget("t1", 0.0)["allowed"] is True
    # Any positive cost: blocked.
    assert m.check_todo_budget("t1", 0.01)["allowed"] is False


def test_zero_daily_limit_blocks_positive_and_pauses() -> None:
    """A zero daily limit blocks any positive estimate and pauses."""
    m = BudgetManager(daily_limit_usd=0.0)
    res = m.check_daily_budget(0.01)
    assert res["allowed"] is False
    assert m.get_status()["paused"] is True


def test_zero_daily_limit_allows_exactly_zero() -> None:
    """With a zero daily limit, a $0 estimate is at-limit -> allowed."""
    m = BudgetManager(daily_limit_usd=0.0)
    assert m.check_daily_budget(0.0)["allowed"] is True
    assert m.get_status()["paused"] is False


def test_negative_estimate_can_pass_todo_check() -> None:
    """A negative estimated cost trivially passes the per-todo check.

    Documents that the manager does not validate sign; a negative 'credit'
    estimate slips through. Recorded here as current behavior.
    """
    m = BudgetManager(per_todo_limit_usd=10.0)
    m.record_spend("t1", 20.0)  # already over the limit on record
    # current(20) + (-15) = 5 <= 10 -> allowed despite being over budget.
    assert m.check_todo_budget("t1", -15.0)["allowed"] is True


def test_negative_estimate_can_unpause_via_daily_check() -> None:
    """A negative estimate keeps the daily check under limit (no pause).

    With spend already high, a negative estimate brings the sum back under
    the limit, so the check allows and does not trip the kill switch.
    """
    m = BudgetManager(daily_limit_usd=100.0)
    m.record_spend("t1", 99.0)
    res = m.check_daily_budget(-50.0)  # 99 + (-50) = 49 <= 100
    assert res["allowed"] is True
    assert m.get_status()["paused"] is False


def test_negative_daily_limit_pauses_immediately_on_zero() -> None:
    """A negative daily limit makes even a $0 estimate exceed it."""
    m = BudgetManager(daily_limit_usd=-1.0)
    res = m.check_daily_budget(0.0)  # 0 > -1 -> exceeded
    assert res["allowed"] is False
    assert m.get_status()["paused"] is True


# --------------------------------------------------------------------------
# record_spend never enforces (charge ordering / overspend window)
# --------------------------------------------------------------------------


def test_record_spend_never_enforces_limit() -> None:
    """record_spend accumulates without enforcing the per-todo limit.

    This is the classic 'check small estimate, then charge large actual'
    overspend window: nothing stops record_spend from blowing the budget.
    """
    m = BudgetManager(per_todo_limit_usd=10.0)
    assert m.check_todo_budget("t1", 1.0)["allowed"] is True  # small estimate
    m.record_spend("t1", 1000.0)  # large actual charge, unchecked
    # The overspend is now realized; only a *subsequent* check notices.
    assert m.get_status()["daily_spend"] == pytest.approx(1000.0)
    assert m.check_todo_budget("t1", 0.01)["allowed"] is False


def test_sequential_charges_accumulate_in_daily_spend() -> None:
    """Sequential record_spend calls sum deterministically (charge ordering)."""
    m = BudgetManager(daily_limit_usd=float("inf"))
    for amt in (1.0, 2.0, 3.0, 4.0):
        m.record_spend("t1", amt)
    assert m.get_status()["daily_spend"] == pytest.approx(10.0)
    assert m.check_todo_budget("t1", 0.0)["allowed"] is True


def test_charge_ordering_independent_of_sequence() -> None:
    """Daily spend is order-independent for the same multiset of charges."""
    a = BudgetManager(daily_limit_usd=float("inf"))
    b = BudgetManager(daily_limit_usd=float("inf"))
    for amt in (5.0, 1.0, 3.0):
        a.record_spend("t", amt)
    for amt in (3.0, 5.0, 1.0):
        b.record_spend("t", amt)
    assert a.get_status()["daily_spend"] == pytest.approx(
        b.get_status()["daily_spend"]
    )


# --------------------------------------------------------------------------
# Reset / rollover behavior
# --------------------------------------------------------------------------


def test_daily_reset_clears_spend_and_unpauses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After >86400s elapsed, daily spend resets and the kill switch clears."""
    m = BudgetManager(daily_limit_usd=100.0)
    m.check_daily_budget(200.0)  # trip the kill switch
    assert m.get_status()["paused"] is True

    m._daily_start = time.monotonic() - 86401  # >86400s elapsed
    status = m.get_status()

    assert status["paused"] is False
    assert status["daily_spend"] == 0.0


def test_no_reset_just_before_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """At exactly 86400s or less elapsed, no rollover occurs."""
    m = BudgetManager(daily_limit_usd=100.0)
    m.record_spend("t1", 50.0)
    assert m._daily_spend == pytest.approx(50.0)


def test_check_daily_triggers_rollover(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_daily_budget resets the daily window BEFORE checking _paused.

    The reset runs first, so a sticky pause from a prior day is cleared at the
    rollover boundary: the first post-rollover check succeeds rather than being
    blocked by the stale pause (the bug this ordering fixes).
    """
    m = BudgetManager(daily_limit_usd=100.0)
    m.check_daily_budget(150.0)  # paused
    assert m.get_status()["paused"] is True

    m._daily_start = time.monotonic() - 86405  # >86400s elapsed
    res = m.check_daily_budget(1.0)

    assert res["allowed"] is True
    assert res["reason"] == "ok"
    assert m.get_status()["paused"] is False


# --------------------------------------------------------------------------
# Fail-open vs fail-closed on unknown / NaN cost
# --------------------------------------------------------------------------


def test_todo_nan_cost_should_fail_closed() -> None:
    """An uncomputable (NaN) per-todo cost fails CLOSED: the call is blocked.

    `current + NaN > limit` is always False, which would silently fail the
    kill switch OPEN. The source guards against this via `_is_uncomputable`
    and rejects the call when a real (finite) per-todo cap is set.
    """
    m = BudgetManager(per_todo_limit_usd=10.0)
    res = m.check_todo_budget("t1", math.nan)
    # Fail-closed behavior: blocked, with an explicit failed-closed reason.
    assert res["allowed"] is False
    assert "failed closed" in res["reason"]


def test_daily_nan_cost_should_fail_closed() -> None:
    """An uncomputable (NaN) daily cost fails CLOSED and trips the kill switch.

    `_daily_spend + NaN > _daily_limit` is always False, which would silently
    fail the daily kill switch OPEN. The source instead detects the
    uncomputable cost, blocks the call, and sets the sticky `_paused` switch.
    """
    m = BudgetManager(daily_limit_usd=100.0)
    res = m.check_daily_budget(math.nan)
    assert res["allowed"] is False
    assert "failed closed" in res["reason"]
    assert m.get_status()["paused"] is True


def test_todo_nan_cost_is_not_recorded_when_failing_closed() -> None:
    """A rejected NaN per-todo cost must not leak into recorded spend.

    Failing closed means the call is blocked AND no phantom spend is booked;
    check_todo_budget is non-mutating, so daily_spend stays at zero.
    """
    m = BudgetManager(per_todo_limit_usd=10.0)
    res = m.check_todo_budget("t1", math.nan)
    # Fail-closed: NaN is rejected, not silently allowed.
    assert res["allowed"] is False
    # And nothing is recorded by the (non-mutating) check.
    assert m.get_status()["daily_spend"] == 0.0


def test_daily_nan_cost_is_rejected_and_pauses_stickily() -> None:
    """A NaN daily cost fails closed: blocked, paused, and the pause is sticky.

    Once the uncomputable cost trips the kill switch, even a subsequent $0
    estimate is rejected as budget_exhausted until a reset path runs.
    """
    m = BudgetManager(daily_limit_usd=100.0)
    res = m.check_daily_budget(math.nan)
    assert res["allowed"] is False
    # The kill switch IS tripped (fail-closed).
    assert m.get_status()["paused"] is True
    # And the pause is sticky: a clean $0 estimate is still rejected.
    follow = m.check_daily_budget(0.0)
    assert follow["allowed"] is False
    assert follow["reason"] == "budget_exhausted"


def test_infinite_cost_fails_closed_on_finite_limit() -> None:
    """A +inf estimate correctly exceeds any finite limit (fail-closed)."""
    m_todo = BudgetManager(per_todo_limit_usd=10.0)
    assert m_todo.check_todo_budget("t1", math.inf)["allowed"] is False

    m_daily = BudgetManager(daily_limit_usd=100.0)
    res = m_daily.check_daily_budget(math.inf)
    assert res["allowed"] is False
    assert m_daily.get_status()["paused"] is True


def test_infinite_cost_with_infinite_limit_is_allowed() -> None:
    """With default inf limit, even an inf estimate passes (inf > inf False)."""
    m = BudgetManager()  # all limits default to inf
    assert m.check_todo_budget("t1", math.inf)["allowed"] is True
    assert m.check_daily_budget(math.inf)["allowed"] is True
