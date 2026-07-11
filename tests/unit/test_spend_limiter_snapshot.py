"""Unit tests for SpendLimiter.snapshot() and restore().

These verify that spend persistence across daemon restarts works correctly,
preventing budget-cap evasion via restart cycles (snapshot before shutdown,
restore on boot). The rolling-window pruning, future-timestamp clamping, and
invalid-record dropping in restore() are also pinned.

Time control: a FakeClock is injected via the SpendLimiter `clock` parameter;
record()/try_charge() take an `at=` timestamp and window_spend()/remaining()
take a `now=` timestamp, so no real time or sleeping is needed.
"""

from __future__ import annotations

from general_ludd.controllers.spend_limiter import SpendLimiter


class FakeClock:
    """Controllable monotonic clock for deterministic tests."""

    def __init__(self, start_time: float = 0.0) -> None:
        self.now = start_time

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


class TestSpendLimiterSnapshot:
    def test_snapshot_returns_records(self) -> None:
        clock = FakeClock(0.0)
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0, clock=clock)
        limiter.record(2.0, kind="token", at=0.0)
        limiter.record(3.0, kind="token", at=0.0)

        snapshot = limiter.snapshot()
        assert len(snapshot) == 2
        assert snapshot[0] == (0.0, 2.0, None)
        assert snapshot[1] == (0.0, 3.0, None)

    def test_restore_preserves_spend(self) -> None:
        limiter1 = SpendLimiter(limit_usd=10.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter1.record(5.0, kind="token", at=0.0)
        snapshot = limiter1.snapshot()

        limiter2 = SpendLimiter(limit_usd=10.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter2.restore(snapshot)

        assert limiter1.window_spend(now=0.0) == 5.0
        assert limiter2.window_spend(now=0.0) == 5.0
        assert limiter2.remaining(now=0.0) == 5.0

    def test_restore_prunes_out_of_window_records(self) -> None:
        limiter1 = SpendLimiter(limit_usd=100.0, window_seconds=100.0, clock=FakeClock(0.0))
        limiter1.record(1.0, kind="token", at=0.0)
        limiter1.record(2.0, kind="token", at=50.0)
        limiter1.record(3.0, kind="token", at=150.0)
        snapshot = limiter1.snapshot()

        limiter2 = SpendLimiter(limit_usd=100.0, window_seconds=100.0, clock=FakeClock(200.0))
        limiter2.restore(snapshot)
        # Window at now=200 is [100, 200]; only the 150.0 record (cost 3.0) is in.
        assert limiter2.window_spend(now=200.0) == 3.0

    def test_restore_clamps_future_timestamps(self) -> None:
        # A record stamped in the future relative to the restore clock is clamped
        # to `now` (not dropped) — so it still counts against the cap.
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=500.0, clock=FakeClock(500.0))
        limiter.restore([(1000.0, 5.0)])
        assert limiter.window_spend(now=500.0) == 5.0

    def test_restore_drops_invalid_costs(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=1000.0, clock=FakeClock(0.0))
        limiter.restore(
            [
                (100.0, 5.0),
                (150.0, -1.0),
                (200.0, float("inf")),
                (250.0, float("nan")),
                (300.0, 3.0),
            ]
        )
        assert limiter.window_spend(now=500.0) == 8.0  # 5.0 + 3.0

    def test_restore_none_and_empty_are_noops(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=1000.0, clock=FakeClock(0.0))
        limiter.restore(None)
        assert limiter.window_spend(now=0.0) == 0.0
        limiter.restore([])
        assert limiter.window_spend(now=0.0) == 0.0

    def test_restart_does_not_reset_budget(self) -> None:
        # The core security property: a restart must NOT clear accumulated spend.
        limiter1 = SpendLimiter(limit_usd=50.0, window_seconds=10000.0, clock=FakeClock(0.0))
        assert limiter1.try_charge(10.0, kind="token", at=0.0) is True
        assert limiter1.try_charge(10.0, kind="token", at=0.001) is True
        assert limiter1.try_charge(10.0, kind="token", at=0.002) is True
        assert limiter1.window_spend(now=0.0) == 30.0
        snapshot = limiter1.snapshot()

        limiter2 = SpendLimiter(limit_usd=50.0, window_seconds=10000.0, clock=FakeClock(100.0))
        limiter2.restore(snapshot)
        assert limiter2.window_spend(now=0.0) == 30.0

        # $25 would push total to $55 > $50 cap → must be rejected post-restart.
        assert limiter2.try_charge(25.0, kind="token", at=0.0) is False
        assert limiter2.window_spend(now=0.0) == 30.0
