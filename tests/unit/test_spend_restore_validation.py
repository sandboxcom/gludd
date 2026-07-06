"""Tests for SpendLimiter.restore() validation: drop invalid records, clamp future ts."""

from __future__ import annotations

from typing import Any, cast

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter


def _make_limiter(
    limit_usd: float = 100.0,
    window_seconds: float = 3600.0,
    start_time: float = 1000.0,
) -> tuple[SpendLimiter, list[float]]:
    """Return a SpendLimiter and a mutable list acting as a fake monotonic clock."""
    clock_val: list[float] = [start_time]

    def fake_clock() -> float:
        return clock_val[0]

    limiter = SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=fake_clock)
    return limiter, clock_val


class TestRestoreDropsNegativeCost:
    def test_negative_cost_record_is_dropped(self) -> None:
        """A negative cost_usd must be silently dropped and must NOT deflate window_spend."""
        sl, clock = _make_limiter(limit_usd=100.0, start_time=1000.0)
        now = clock[0]
        # First record a real positive spend so window_spend > 0
        sl.record(50.0, kind="token", at=now)
        # Now restore a record with a negative cost attempting cap evasion
        sl.restore([(now, -500.0)])
        # window_spend must not be reduced below 50.0
        spend = sl.window_spend()
        assert spend == pytest.approx(50.0), (
            f"Expected window_spend ~50.0 after dropping negative cost, got {spend}"
        )

    def test_negative_cost_alone_does_not_reduce_spend_below_zero(self) -> None:
        """Even with no prior spend, a negative restore record must not create negative spend."""
        sl, clock = _make_limiter(limit_usd=100.0, start_time=1000.0)
        now = clock[0]
        sl.restore([(now, -500.0)])
        spend = sl.window_spend()
        assert spend == pytest.approx(0.0), (
            f"Expected window_spend 0.0 with only dropped negative record, got {spend}"
        )

    def test_negative_cost_does_not_raise_the_cap(self) -> None:
        """After dropping a negative restore record the limiter still enforces the cap."""
        sl, clock = _make_limiter(limit_usd=10.0, start_time=1000.0)
        now = clock[0]
        sl.record(9.0, kind="token", at=now)
        # Attempt to evade: restore a -500 record to make window_spend go negative
        sl.restore([(now, -500.0)])
        # Cap should still be close to exhausted (9.0 spent, 1.0 remaining)
        assert sl.would_exceed(2.0), "Cap was evaded: would_exceed should be True after 9.0 spend"


class TestRestoreDropsNanAndInf:
    def test_nan_cost_is_dropped(self) -> None:
        """A NaN cost_usd must be dropped."""
        sl, clock = _make_limiter(limit_usd=100.0, start_time=1000.0)
        now = clock[0]
        sl.restore([(now, float("nan"))])
        assert sl.window_spend() == pytest.approx(0.0)

    def test_positive_inf_cost_is_dropped(self) -> None:
        """A +inf cost_usd must be dropped (non-finite)."""
        sl, clock = _make_limiter(limit_usd=100.0, start_time=1000.0)
        now = clock[0]
        sl.restore([(now, float("inf"))])
        assert sl.window_spend() == pytest.approx(0.0)

    def test_negative_inf_cost_is_dropped(self) -> None:
        """A -inf cost_usd must be dropped (non-finite and negative)."""
        sl, clock = _make_limiter(limit_usd=100.0, start_time=1000.0)
        now = clock[0]
        sl.restore([(now, float("-inf"))])
        assert sl.window_spend() == pytest.approx(0.0)


class TestRestoreClampsFutureTimestamp:
    def test_future_timestamp_clamped_to_now(self) -> None:
        """A record with ts > now should be clamped to now and still count toward spend."""
        sl, clock = _make_limiter(
            limit_usd=100.0, window_seconds=3600.0, start_time=1000.0
        )
        now = clock[0]
        future_ts = now + 3600.0  # 1 hour in the future
        sl.restore([(future_ts, 5.0)])
        # Record must be present in the window (clamped to now=1000)
        spend = sl.window_spend()
        assert spend == pytest.approx(5.0), (
            f"Expected 5.0 after clamped-future restore, got {spend}"
        )

    def test_future_timestamp_does_not_survive_window_end(self) -> None:
        """A clamped future record must expire when the window rolls past it."""
        sl, clock = _make_limiter(
            limit_usd=100.0, window_seconds=3600.0, start_time=1000.0
        )
        now = clock[0]
        future_ts = now + 9000.0
        # restore should clamp to now=1000
        sl.restore([(future_ts, 5.0)])
        # Advance clock past window: 1000 + 3601 = 4601 → cutoff 4601-3600=1001 > 1000
        clock[0] = now + 3601.0
        spend = sl.window_spend()
        assert spend == pytest.approx(0.0), (
            f"Clamped record should have expired, but window_spend={spend}"
        )


class TestRestoreKeepsValidRecords:
    def test_normal_positive_record_is_kept(self) -> None:
        """A valid positive cost record must be accepted and count toward spend."""
        sl, clock = _make_limiter(limit_usd=100.0, start_time=1000.0)
        now = clock[0]
        sl.restore([(now, 7.5)])
        assert sl.window_spend() == pytest.approx(7.5)

    def test_zero_cost_record_is_kept(self) -> None:
        """A zero-cost record is valid (non-negative, finite) and must be accepted."""
        sl, clock = _make_limiter(limit_usd=100.0, start_time=1000.0)
        now = clock[0]
        sl.restore([(now, 0.0)])
        assert sl.window_spend() == pytest.approx(0.0)

    def test_mixed_valid_invalid_records(self) -> None:
        """Only valid records contribute; invalid ones are dropped."""
        sl, clock = _make_limiter(limit_usd=100.0, start_time=1000.0)
        now = clock[0]
        sl.restore([
            (now, 10.0),          # valid
            (now, -5.0),          # invalid: negative -> dropped
            (now, float("nan")),  # invalid: NaN -> dropped
            (now, 3.0),           # valid
        ])
        assert sl.window_spend() == pytest.approx(13.0)

    def test_restore_none_is_noop(self) -> None:
        """restore(None) must not raise and must not change spend."""
        sl, _ = _make_limiter()
        cast(Any, sl).restore(None)
        assert sl.window_spend() == pytest.approx(0.0)

    def test_restore_empty_list_is_noop(self) -> None:
        """restore([]) must not raise and must not change spend."""
        sl, _ = _make_limiter()
        sl.restore([])
        assert sl.window_spend() == pytest.approx(0.0)
