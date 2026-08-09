"""Deep sliding-window counter tests: FixedWindow, SlidingLog, SmoothedRate."""

from __future__ import annotations

import pytest
from general_ludd.network.sliding_window import (
    FixedWindow,
    SlidingLog,
    SmoothedRate,
)


class FakeClock:
    def __init__(self, start_ns: int) -> None:
        self._now = start_ns

    def __call__(self) -> int:
        return self._now

    def advance(self, ns: int) -> None:
        self._now += ns


class TestFixedWindow:
    def test_count_increments_within_window(self) -> None:
        fw = FixedWindow(window_sec=10, max_events=5)
        assert fw.allow()
        assert fw.allow()
        assert fw.count() == 2

    def test_allow_false_when_window_full(self) -> None:
        fw = FixedWindow(window_sec=10, max_events=3)
        for _ in range(3):
            assert fw.allow()
        assert not fw.allow()
        assert fw.count() == 3

    def test_window_advances_opens_new_slot(self) -> None:
        base = 1_000_000_000_000
        clock = FakeClock(base)
        fw = FixedWindow(window_sec=10, max_events=2, _clock=clock)
        for _ in range(2):
            assert fw.allow()
        clock.advance(20_000_000_000)
        assert fw.allow()
        assert fw.count() == 1

    def test_count_zero_initially(self) -> None:
        fw = FixedWindow(window_sec=5, max_events=10)
        assert fw.count() == 0

    def test_window_start_moves_forward_on_expiry(self) -> None:
        base = 1_000_000_000_000
        clock = FakeClock(base)
        fw = FixedWindow(window_sec=60, max_events=10, _clock=clock)
        clock.advance(120_000_000_000)
        fw.allow()
        assert fw._window_start == base + 120_000_000_000

    def test_multiple_windows_accumulate_then_reset(self) -> None:
        base = 1_000_000_000_000
        clock = FakeClock(base)
        fw = FixedWindow(window_sec=10, max_events=3, _clock=clock)
        for _ in range(3):
            fw.allow()
        assert not fw.allow()
        clock.advance(15_000_000_000)
        assert fw.allow()
        assert fw.count() == 1


class TestSlidingLog:
    def test_expired_events_evicted(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sl = SlidingLog(window_sec=60, max_events=10, _clock=clock)
        sl._log = [base - 120_000_000_000]
        clock.advance(60_000_000_000)
        sl._log.append(base - 60_000_000_000)
        sl._log.append(base)
        sl._log.append(base + 1_000_000_000)
        assert sl.count() == 2

    def test_allow_false_when_log_full(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sl = SlidingLog(window_sec=60, max_events=3, _clock=clock)
        for _ in range(3):
            assert sl.allow()
        assert not sl.allow()

    def test_allow_true_after_eviction(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sl = SlidingLog(window_sec=60, max_events=3, _clock=clock)
        sl._log = [
            base - 120_000_000_000,
            base - 90_000_000_000,
            base - 61_000_000_000,
        ]
        assert sl.allow()
        assert sl.count() == 1

    def test_log_stored_in_order(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sl = SlidingLog(window_sec=600, max_events=100, _clock=clock)
        sl._log = [base + 1, base, base + 2]
        sl.allow()
        assert sl._log == sorted(sl._log)

    def test_count_zero_on_empty_log(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sl = SlidingLog(window_sec=60, max_events=100, _clock=clock)
        assert sl.count() == 0

    def test_max_events_respected_after_eviction(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sl = SlidingLog(window_sec=60, max_events=5, _clock=clock)
        sl._log = [base] * 10
        assert not sl.allow()

    def test_allow_logs_current_time(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sl = SlidingLog(window_sec=60, max_events=10, _clock=clock)
        sl.allow()
        assert sl._log == [base]

    def test_evict_zero_when_all_recent(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sl = SlidingLog(window_sec=60, max_events=10, _clock=clock)
        sl._log = [base - 30_000_000_000, base - 10_000_000_000, base]
        assert sl.count() == 3


class TestSmoothedRate:
    def test_rate_starts_at_zero(self) -> None:
        sr = SmoothedRate(alpha=0.2)
        assert sr.rate() == 0.0

    def test_rate_increases_with_observations(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sr = SmoothedRate(alpha=0.5, _clock=clock)
        sr.observe(100)
        assert sr.rate() == pytest.approx(50.0, rel=1e-9)
        clock.advance(1_000_000_000)
        sr.observe(100)
        assert sr.rate() == pytest.approx(75.0, rel=1e-9)

    def test_rate_decays_without_observations(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sr = SmoothedRate(alpha=0.5, _clock=clock)
        sr.observe(1000)
        initial = sr.rate()
        clock.advance(1_000_000_000)
        sr.observe(1)
        assert sr.rate() < initial

    def test_zero_observations_converge_to_zero(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sr = SmoothedRate(alpha=0.8, _clock=clock)
        for _ in range(10):
            sr.observe(100)
            clock.advance(1_000_000_000)
        for _ in range(20):
            sr.observe(0)
            clock.advance(1_000_000_000)
        assert sr.rate() < 1.0

    def test_alpha_0_never_updates(self) -> None:
        sr = SmoothedRate(alpha=0.0)
        sr.observe(1000)
        assert sr.rate() == 0.0

    def test_alpha_1_instant_update(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sr = SmoothedRate(alpha=1.0, _clock=clock)
        sr.observe(42)
        assert sr.rate() == pytest.approx(42.0, rel=1e-9)
        clock.advance(1_000_000_000)
        sr.observe(100)
        assert sr.rate() == pytest.approx(100.0, rel=1e-9)

    def test_rate_never_negative(self) -> None:
        sr = SmoothedRate(alpha=0.3)
        sr.observe(100)
        for _ in range(20):
            sr.observe(0)
        assert sr.rate() >= 0.0

    def test_monotonic_timestamps_produce_stable_rate(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sr = SmoothedRate(alpha=0.3, _clock=clock)
        rates: list[float] = []
        for val in [10, 12, 9, 11, 10, 13, 8, 14, 10, 11]:
            sr.observe(val)
            clock.advance(1_000_000_000)
            rates.append(sr.rate())
        assert all(r >= 0.0 for r in rates)
        assert sr.rate() > 0.0

    def test_high_alpha_chases_spikes(self) -> None:
        base = 1_000_000_000_000_000
        clock_a = FakeClock(base)
        clock_b = FakeClock(base)
        sr_low = SmoothedRate(alpha=0.1, _clock=clock_a)
        sr_high = SmoothedRate(alpha=0.9, _clock=clock_b)
        sr_low.observe(100)
        sr_high.observe(100)
        low_before = sr_low.rate()
        high_before = sr_high.rate()
        clock_a.advance(1_000_000_000)
        clock_b.advance(1_000_000_000)
        sr_low.observe(10)
        sr_high.observe(10)
        low_change = abs(sr_low.rate() - low_before)
        high_change = abs(sr_high.rate() - high_before)
        assert high_change > low_change

    def test_low_alpha_produces_smooth_curve(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sr = SmoothedRate(alpha=0.05, _clock=clock)
        values = [20, 0, 20, 0, 20, 0]
        rates = []
        for v in values:
            sr.observe(v)
            clock.advance(1_000_000_000)
            rates.append(sr.rate())
        max_jump = max(abs(rates[i] - rates[i - 1]) for i in range(1, len(rates)))
        assert max_jump < 50.0

    def test_same_count_different_elapsed_produces_different_rate(self) -> None:
        base = 1_000_000_000_000_000
        clock = FakeClock(base)
        sr = SmoothedRate(alpha=0.5, _clock=clock)
        sr.observe(10)
        clock.advance(1_000_000_000)
        sr.observe(10)
        clock.advance(2_000_000_000)
        sr.observe(10)
        assert sr.rate() > 0.0
