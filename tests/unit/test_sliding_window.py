"""Unit tests for sliding_window rate limiters."""

import time

from general_ludd.network.sliding_window import FixedWindow, SlidingLog, SmoothedRate


class TestFixedWindow:
    def test_allow_within_limit(self):
        clock = time.monotonic_ns
        w = FixedWindow(window_sec=1.0, max_events=2, _clock=clock)
        assert w.allow() is True
        assert w.allow() is True
        assert w.allow() is False

    def test_count(self):
        clock = time.monotonic_ns
        w = FixedWindow(window_sec=1.0, max_events=2, _clock=clock)
        w.allow()
        assert w.count() == 1

    def test_advance_resets(self):
        class FakeClock:
            def __init__(self):
                self.t = 0

            def __call__(self):
                return self.t

            def advance(self, delta):
                self.t += int(delta)

        clock = FakeClock()
        w = FixedWindow(window_sec=1.0, max_events=1, _clock=clock)
        assert w.allow() is True
        assert w.allow() is False
        clock.advance(1_000_000_001)
        assert w.allow() is True


class TestSlidingLog:
    def test_allow_within_limit(self):
        clock = time.monotonic_ns
        w = SlidingLog(window_sec=1.0, max_events=2, _clock=clock)
        assert w.allow() is True
        assert w.allow() is True
        assert w.allow() is False

    def test_evict(self):
        class FakeClock:
            def __init__(self):
                self.t = 0

            def __call__(self):
                return self.t

            def advance(self, delta):
                self.t += int(delta)

        clock = FakeClock()
        w = SlidingLog(window_sec=1.0, max_events=1, _clock=clock)
        assert w.allow() is True
        assert w.allow() is False
        clock.advance(1_000_000_001)
        assert w.allow() is True


class TestSmoothedRate:
    def test_initial_rate(self):
        clock = time.monotonic_ns
        r = SmoothedRate(alpha=0.5, _clock=clock)
        assert r.rate() == 0.0

    def test_observe_updates(self):
        class FakeClock:
            def __init__(self):
                self.t = 0

            def __call__(self):
                return self.t

            def advance(self, delta):
                self.t += int(delta)

        clock = FakeClock()
        r = SmoothedRate(alpha=0.5, _clock=clock)
        r.observe(10)
        assert r.rate() > 0
        clock.advance(1_000_000_000)
        r.observe(10)
        assert r.rate() > 0
