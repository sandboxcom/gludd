"""TDD tests for SpendLimiter enforcement bugs (#49).

These tests prove four bypasses are closed:

  #1  Spend is actually RECORDED through the enforcement path (the limiter is
      no longer inert) — a charge that is accepted shows up in window_spend().
  #2  Accumulated spend SURVIVES a restart — snapshot/restore round-trips the
      window so a daemon restart cannot reset the cap to zero.
  #3  Concurrent charges in one window cannot collectively exceed the cap —
      check-and-record is atomic under threads.
  #4  Unknown cost with a cap configured is REFUSED (fail closed), not allowed
      through silently (fail open).
"""

from __future__ import annotations

import math
import threading

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter


def _make_limiter(limit_usd: float, window_seconds: float) -> tuple[SpendLimiter, list[float]]:
    clock_val: list[float] = [0.0]

    def fake_clock() -> float:
        return clock_val[0]

    limiter = SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=fake_clock)
    return limiter, clock_val


class TestTryChargeRecordsSpend:
    """#1 — the limiter is no longer inert: an accepted charge is recorded."""

    def test_accepted_charge_is_recorded_in_window(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        assert sl.try_charge(3.0, kind="token") is True
        # The charge must have been recorded against the window.
        assert sl.window_spend() == pytest.approx(3.0)
        assert sl.remaining() == pytest.approx(7.0)

    def test_multiple_accepted_charges_accumulate(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        assert sl.try_charge(2.0, kind="token") is True
        assert sl.try_charge(3.0, kind="infra") is True
        assert sl.window_spend() == pytest.approx(5.0)

    def test_charge_over_cap_is_refused_and_not_recorded(self) -> None:
        sl, clock = _make_limiter(5.0, 3600.0)
        clock[0] = 1.0
        sl.try_charge(4.0, kind="token")
        # 4 + 2 = 6 > 5 -> refused, and the 2.0 must NOT be recorded.
        assert sl.try_charge(2.0, kind="token") is False
        assert sl.window_spend() == pytest.approx(4.0)


class TestUnknownCostFailsClosed:
    """#4 — unknown cost with a cap configured is refused, not waved through."""

    def test_unknown_cost_with_cap_is_refused(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        # Cost is unknown (None) but a positive cap is configured -> fail CLOSED.
        assert sl.try_charge(None, kind="token") is False
        # Nothing recorded.
        assert sl.window_spend() == pytest.approx(0.0)

    def test_unknown_cost_without_cap_is_allowed(self) -> None:
        # When no cap is configured (limit <= 0 means "unlimited"), unknown
        # cost is allowed — fail-closed only applies when a cap exists.
        sl, _ = _make_limiter(0.0, 3600.0)
        assert sl.cap_configured is False
        assert sl.try_charge(None, kind="token") is True

    def test_cap_configured_reflects_positive_limit(self) -> None:
        sl, _ = _make_limiter(0.01, 3600.0)
        assert sl.cap_configured is True


class TestNaNInfCostFailsClosed:
    """#71 — a NaN/inf projected cost must FAIL CLOSED, not fail open.

    NaN compares False against every limit (``nan > limit`` is False), so a
    naive ``spent + projected > limit`` check waves an unbounded/garbage cost
    straight through (unlimited spend). The limiter must treat any non-finite
    cost as over-limit and refuse it.
    """

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_would_exceed_true_for_non_finite_cost(self, bad: float) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        # Even with the window empty, a non-finite projected cost must be
        # treated as exceeding the cap (cannot prove it fits).
        assert sl.would_exceed(bad) is True

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_try_charge_refuses_non_finite_cost(self, bad: float) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        # A NaN/inf cost is BLOCKED (fail closed) and nothing is recorded —
        # otherwise the rolling window would be poisoned with a non-finite sum.
        assert sl.try_charge(bad, kind="token") is False
        assert sl.window_spend() == pytest.approx(0.0)


class TestConcurrentChargesRespectCap:
    """#3 — concurrent charges cannot collectively exceed the cap."""

    def test_concurrent_charges_do_not_overshoot(self) -> None:
        # Limit 10.0, each charge 1.0 -> at most 10 charges may be accepted no
        # matter how many threads race. Use the real (default) clock so all
        # records land inside the same window.
        sl = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)

        accepted: list[bool] = []
        accepted_lock = threading.Lock()
        start = threading.Barrier(50)

        def worker() -> None:
            start.wait()
            ok = sl.try_charge(1.0, kind="token")
            with accepted_lock:
                accepted.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 10 charges of 1.0 may be accepted (cap=10).
        assert sum(1 for a in accepted if a) == 10
        # And the recorded window spend never exceeds the cap.
        assert sl.window_spend() <= 10.0 + 1e-9


class TestSnapshotRestoreRoundTrip:
    """#2 — accumulated spend survives a restart via snapshot/restore."""

    def test_snapshot_restore_preserves_window_spend(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 100.0
        sl.try_charge(3.0, kind="token")
        sl.try_charge(2.0, kind="infra")
        assert sl.window_spend() == pytest.approx(5.0)

        snap = sl.snapshot()

        # Simulate a daemon restart: brand-new limiter, same clock position.
        sl2, clock2 = _make_limiter(10.0, 3600.0)
        clock2[0] = 100.0
        # A fresh limiter starts empty (this is the bug being guarded against).
        assert sl2.window_spend() == pytest.approx(0.0)

        sl2.restore(snap)
        # After restore the accumulated spend is back -> the cap cannot be
        # evaded by restarting.
        assert sl2.window_spend() == pytest.approx(5.0)
        assert sl2.remaining() == pytest.approx(5.0)

    def test_restore_respects_window_pruning_after_restart(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 0.0
        sl.try_charge(5.0, kind="token")  # recorded at t=0
        snap = sl.snapshot()

        # Restart far in the future: the restored record is outside the window.
        sl2, clock2 = _make_limiter(10.0, 3600.0)
        clock2[0] = 10_000.0
        sl2.restore(snap)
        assert sl2.window_spend() == pytest.approx(0.0)
