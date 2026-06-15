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
