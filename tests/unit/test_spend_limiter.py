"""Tests for SpendLimiter: rolling-window soft cap, clock injection, boundary math."""

from __future__ import annotations

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter


def _make_limiter(limit_usd: float, window_seconds: float) -> tuple[SpendLimiter, list[float]]:
    """Return a SpendLimiter and a mutable list acting as a fake monotonic clock."""
    clock_val: list[float] = [0.0]

    def fake_clock() -> float:
        return clock_val[0]

    limiter = SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=fake_clock)
    return limiter, clock_val


class TestWindowSpend:
    def test_empty_window_is_zero(self) -> None:
        sl, _ = _make_limiter(10.0, 3600.0)
        assert sl.window_spend() == pytest.approx(0.0)

    def test_single_record_within_window(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 100.0
        sl.record(1.5, kind="token")
        assert sl.window_spend() == pytest.approx(1.5)

    def test_multiple_records_within_window(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 100.0
        sl.record(1.0, kind="token")
        sl.record(2.0, kind="infra")
        assert sl.window_spend() == pytest.approx(3.0)

    def test_old_records_pruned_outside_window(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 0.0
        sl.record(5.0, kind="token")  # at t=0, window is [t-3600, t]
        clock[0] = 3601.0             # now t=3601; the record at t=0 is outside [t-3600, t]
        assert sl.window_spend() == pytest.approx(0.0)

    def test_records_on_boundary_included(self) -> None:
        """A record recorded at exactly (now - window_seconds) must still be included."""
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 0.0
        sl.record(3.0, kind="token")
        clock[0] = 3600.0   # record is exactly at the boundary (t=0, window starts at t-3600=0)
        assert sl.window_spend() == pytest.approx(3.0)

    def test_records_just_after_boundary_excluded(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 0.0
        sl.record(3.0, kind="token")
        clock[0] = 3600.1   # just past boundary
        assert sl.window_spend() == pytest.approx(0.0)

    def test_mixed_window_keeps_only_recent(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 0.0
        sl.record(5.0, kind="token")   # old record
        clock[0] = 1800.0
        sl.record(2.0, kind="infra")   # still in window when clock=3601
        clock[0] = 3601.0
        # at t=3601: window is [1, 3601]; t=0 is excluded, t=1800 is included
        assert sl.window_spend() == pytest.approx(2.0)

    def test_record_with_explicit_at_parameter(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1000.0
        sl.record(4.0, kind="token", at=999.0)  # pass explicit timestamp
        assert sl.window_spend() == pytest.approx(4.0)


class TestRemaining:
    def test_remaining_starts_at_limit(self) -> None:
        sl, _ = _make_limiter(20.0, 3600.0)
        assert sl.remaining() == pytest.approx(20.0)

    def test_remaining_decreases_after_record(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(5.0, kind="token")
        assert sl.remaining() == pytest.approx(15.0)

    def test_remaining_never_negative(self) -> None:
        sl, clock = _make_limiter(5.0, 3600.0)
        clock[0] = 1.0
        sl.record(10.0, kind="token")  # exceeds limit
        assert sl.remaining() == pytest.approx(0.0)

    def test_remaining_zero_when_fully_consumed(self) -> None:
        sl, clock = _make_limiter(5.0, 3600.0)
        clock[0] = 1.0
        sl.record(5.0, kind="token")
        assert sl.remaining() == pytest.approx(0.0)


class TestWouldExceed:
    def test_below_limit_does_not_exceed(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(5.0, kind="token")
        assert not sl.would_exceed(10.0)  # 5 + 10 = 15 <= 20

    def test_exactly_at_limit_does_not_exceed(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(10.0, kind="token")
        assert not sl.would_exceed(10.0)  # 10 + 10 = 20, exactly at limit -> not exceeded

    def test_above_limit_would_exceed(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(10.0, kind="token")
        assert sl.would_exceed(11.0)   # 10 + 11 = 21 > 20

    def test_at_zero_remaining_always_exceeds(self) -> None:
        sl, clock = _make_limiter(5.0, 3600.0)
        clock[0] = 1.0
        sl.record(5.0, kind="token")
        assert sl.would_exceed(0.01)   # even tiny projected cost exceeds

    def test_zero_projected_at_full_window_does_not_exceed(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(20.0, kind="token")  # exactly at limit
        assert not sl.would_exceed(0.0)

    def test_roughly_met_boundary(self) -> None:
        """Verify 'roughly met not exceeded': if remaining > 0, a dispatch that
        fits remaining is allowed; once remaining <= 0, any positive projected
        cost is deferred."""
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(9.99, kind="token")
        # remaining ~ 0.01; projected 0.01 fits
        assert not sl.would_exceed(0.01)
        # projected 0.02 would push over
        assert sl.would_exceed(0.02)


class TestMixedCostKinds:
    def test_token_and_infra_costs_sum_in_window(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(2.0, kind="token")
        sl.record(3.0, kind="infra")
        assert sl.window_spend() == pytest.approx(5.0)
        assert sl.remaining() == pytest.approx(5.0)

    def test_model_kwarg_accepted(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token", model="claude-3-5-sonnet-20241022")
        assert sl.window_spend() == pytest.approx(1.0)


class TestDefaultClock:
    def test_default_clock_uses_monotonic(self) -> None:
        """SpendLimiter without injected clock must not raise on construction or use."""
        sl = SpendLimiter(limit_usd=5.0, window_seconds=60.0)
        sl.record(0.01, kind="token")
        assert sl.window_spend() > 0.0
        assert sl.remaining() < 5.0


class TestRestoreValidation:
    """Security tests: restore() must drop invalid (negative / non-finite) cost records.

    A hostile restore() payload carrying cost=-1000.0 would deflate window_spend()
    and let an attacker evade the cap.  NaN/inf costs must also be rejected because
    NaN compares False against every limit and inf overflows the sum.
    """

    def test_restore_negative_cost_does_not_deflate_spend(self) -> None:
        """Negative cost in restore() must be silently dropped; the window spend
        must remain at its pre-restore level and the cap must still be enforced."""
        sl, clock = _make_limiter(limit_usd=1.0, window_seconds=60.0)
        clock[0] = 10.0
        # Record legitimate spend of 0.9 USD (near cap).
        sl.record(0.9, kind="token")
        # Attempt to deflate via a negative-cost restore record.
        sl.restore([(10.0, -1000.0)])
        # Window spend must not be deflated to -999.1.
        assert sl.window_spend() == pytest.approx(0.9)
        # Cap must still be enforced: 0.9 + 0.15 > 1.0.
        assert sl.would_exceed(0.15) is True

    def test_restore_nan_cost_is_dropped(self) -> None:
        """NaN cost in restore() must be silently dropped; window spend unchanged."""
        sl, clock = _make_limiter(limit_usd=1.0, window_seconds=60.0)
        clock[0] = 10.0
        sl.record(0.9, kind="token")
        sl.restore([(10.0, float("nan"))])
        assert sl.window_spend() == pytest.approx(0.9)
        assert sl.would_exceed(0.15) is True

    def test_restore_inf_cost_is_dropped(self) -> None:
        """Positive inf, negative inf costs in restore() must all be dropped."""
        sl, clock = _make_limiter(limit_usd=1.0, window_seconds=60.0)
        clock[0] = 10.0
        sl.record(0.9, kind="token")
        sl.restore([
            (10.0, float("inf")),
            (10.0, float("-inf")),
        ])
        assert sl.window_spend() == pytest.approx(0.9)
        assert sl.would_exceed(0.15) is True


class TestRecordGuard:
    """Security tests: record() must RAISE on negative / non-finite cost (live path).

    Unlike restore() which silently drops bad records (offline/deserialization path),
    record() is the LIVE path called by try_charge() on every dispatch.  A negative
    cost here would DEFLATE window_spend(), letting an attacker evade the cap by
    injecting a negative charge.  NaN/inf costs would poison sum() so would_exceed()
    returns False (unlimited spend).  We raise ValueError so the caller sees a hard
    error and cannot continue as if the charge were accepted.
    """

    def test_record_negative_cost_raises(self) -> None:
        """record() with a negative cost must raise ValueError."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        with pytest.raises(ValueError, match="non-negative"):
            sl.record(-1.0, kind="token")

    def test_record_negative_cost_does_not_add_to_window(self) -> None:
        """After a failed record() call the window must be unchanged (no partial write)."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(2.0, kind="token")
        with pytest.raises(ValueError):
            sl.record(-1.0, kind="token")
        # Window must still be 2.0, not 1.0 (deflated) or anything else.
        assert sl.window_spend() == pytest.approx(2.0)

    def test_record_nan_cost_raises(self) -> None:
        """record() with NaN cost must raise ValueError."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        with pytest.raises(ValueError):
            sl.record(float("nan"), kind="token")

    def test_record_nan_does_not_poison_window(self) -> None:
        """A failed record(NaN) must leave window_spend() unaffected (no NaN in sum)."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(3.0, kind="token")
        with pytest.raises(ValueError):
            sl.record(float("nan"), kind="token")
        assert sl.window_spend() == pytest.approx(3.0)
        # Cap enforcement must still work (not poisoned to always-False).
        assert sl.would_exceed(8.0) is True  # 3 + 8 = 11 > 10

    def test_record_inf_cost_raises(self) -> None:
        """record() with +inf cost must raise ValueError."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        with pytest.raises(ValueError):
            sl.record(float("inf"), kind="token")

    def test_record_neg_inf_cost_raises(self) -> None:
        """record() with -inf cost must raise ValueError."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        with pytest.raises(ValueError):
            sl.record(float("-inf"), kind="token")

    def test_record_positive_cost_works(self) -> None:
        """The normal positive-cost path must still succeed after the guard is added."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(0.05, kind="token")
        assert sl.window_spend() == pytest.approx(0.05)

    def test_record_zero_cost_works(self) -> None:
        """Zero is a valid (non-negative, finite) cost and must be accepted."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(0.0, kind="infra")
        assert sl.window_spend() == pytest.approx(0.0)

    def test_try_charge_negative_cost_refused_no_headroom_created(self) -> None:
        """try_charge() with a negative cost must not create phantom headroom.

        A negative cost passed through try_charge → record() would reduce
        window_spend() and let subsequent charges bypass the cap.  The guard
        in record() must raise, which propagates out of try_charge() so the
        cap is never evaded.
        """
        sl, clock = _make_limiter(limit_usd=1.0, window_seconds=3600.0)
        clock[0] = 1.0
        # Fill the cap to within 0.05 USD.
        sl.record(0.95, kind="token")
        assert sl.remaining() == pytest.approx(0.05)
        # Attempt to inject negative cost via try_charge — must raise.
        with pytest.raises(ValueError):
            sl.try_charge(-1.0, kind="token")
        # Remaining must be unchanged (not inflated to 1.05).
        assert sl.remaining() == pytest.approx(0.05)

    def test_try_charge_positive_cost_still_recorded(self) -> None:
        """try_charge() normal positive path must still record the charge."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        accepted = sl.try_charge(2.0, kind="token")
        assert accepted is True
        assert sl.window_spend() == pytest.approx(2.0)
