"""Deep tests for debounce/throttle primitives.

Covers: trailing edge, leading edge, max-wait, async debounce, throttle
(leading/trailing/both), immediate reset, cancel, flush, zero-wait,
multi-instance isolation, and argument preservation.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from general_ludd.util.debounce import AsyncDebouncer, Debouncer, Throttle

# ---------------------------------------------------------------------------
# Simulated clock for deterministic tests
# ---------------------------------------------------------------------------


class SimulatedClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        self._now += delta


# ---------------------------------------------------------------------------
# Tests — trailing edge
# ---------------------------------------------------------------------------


class TestDebouncerTrailing:
    def test_single_call_fires_after_wait(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(100.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, clock=clock)
        d(42)
        assert calls == []
        d.drive(104.9)
        assert calls == []
        d.drive(105.0)
        assert calls == [42]

    def test_multiple_rapid_calls_only_last_fires(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=3.0, clock=clock)
        d(1)
        d(2)
        d(3)
        d(4)
        d.drive(2.9)
        assert calls == []
        d.drive(3.0)
        assert calls == [4]

    def test_cancel_prevents_pending_call(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, clock=clock)
        d(99)
        d.cancel()
        d.drive(10.0)
        assert calls == []

    def test_flush_invokes_pending_immediately(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=60.0, clock=clock)
        d(77)
        d.flush()
        assert calls == [77]
        d.drive(120.0)
        assert calls == [77]

    def test_two_bursts_two_firings(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=2.0, clock=clock)
        d(1)
        d.drive(2.5)
        assert calls == [1]
        d(2)
        d(3)
        d.drive(4.5)
        assert calls == [1, 3]

    def test_reset_clears_pending(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, clock=clock)
        d(1)
        d.reset()
        d.drive(10.0)
        assert calls == []

    def test_zero_wait_fires_immediately(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=0.0, clock=clock)
        d(1)
        d.drive(0.0)
        assert calls == [1]

    def test_rejects_negative_wait(self) -> None:
        with pytest.raises(ValueError, match="wait"):
            Debouncer(lambda: None, wait=-0.1)

    def test_pending_property(self) -> None:
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda: None, wait=10.0, clock=clock)
        assert not d.pending
        d()
        assert d.pending
        d.drive(10.0)
        assert not d.pending

    def test_argument_preservation(self) -> None:
        captured: dict[str, Any] = {}

        def record(**kw: Any) -> None:
            captured.update(kw)

        clock = SimulatedClock(0.0)
        d = Debouncer(record, wait=1.0, clock=clock)
        d(a=1, b="hello")
        d(a=2, b="world")
        d.drive(2.0)
        assert captured == {"a": 2, "b": "world"}

    def test_max_wait_fires_during_sustained_burst(self) -> None:
        """Continuous calls at 0.5 s intervals; max_wait=3 s guarantees
        at least one firing every 3 s."""
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, max_wait=3.0, clock=clock)
        for i in range(20):
            d(i)
            clock.advance(0.5)
            d._tick()
        # After 10 s = 3x max_wait, should have fired ~3 times
        assert len(calls) >= 2, f"expected >=2 max-wait firings, got {len(calls)}"

    def test_max_wait_single_call_still_waits(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, max_wait=10.0, clock=clock)
        d(1)
        d.drive(4.9)
        assert calls == []
        d.drive(5.0)
        assert calls == [1]

    def test_max_wait_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_wait"):
            Debouncer(lambda: None, wait=3.0, max_wait=0.0)

    def test_rejects_negative_max_wait(self) -> None:
        with pytest.raises(ValueError, match="max_wait"):
            Debouncer(lambda: None, wait=3.0, max_wait=-1.0)


# ---------------------------------------------------------------------------
# Tests — leading edge
# ---------------------------------------------------------------------------


class TestDebouncerLeading:
    def test_first_call_fires_immediately(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, leading=True, clock=clock)
        d(10)
        assert calls == [10]

    def test_subsequent_calls_suppressed_within_window(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, leading=True, clock=clock)
        d(10)
        d(20)
        d(30)
        clock.advance(2.0)
        d(40)
        assert calls == [10]

    def test_second_burst_after_wait_fires_again(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, leading=True, clock=clock)
        d(1)
        clock.advance(6.0)
        d(2)
        assert calls == [1, 2]

    def test_reset_allows_immediate_leading(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, leading=True, clock=clock)
        d(1)
        d.reset()
        d(2)
        assert calls == [1, 2]


# ---------------------------------------------------------------------------
# Tests — throttle
# ---------------------------------------------------------------------------


class TestThrottleLeading:
    def test_first_call_fires_immediately(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(lambda x: calls.append(x), wait=3.0, leading=True, clock=clock)
        t(1)
        assert calls == [1]

    def test_suppressed_within_window(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(lambda x: calls.append(x), wait=3.0, leading=True, clock=clock)
        t(1)
        clock.advance(1.0)
        t(2)
        clock.advance(1.0)
        t(3)
        assert calls == [1]

    def test_second_call_after_window_fires(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(lambda x: calls.append(x), wait=3.0, leading=True, clock=clock)
        t(1)
        clock.advance(4.0)
        t(2)
        assert calls == [1, 2]

    def test_reset_clears_last_fired(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(lambda x: calls.append(x), wait=5.0, leading=True, clock=clock)
        t(1)
        t.reset()
        t(2)
        assert calls == [1, 2]

    def test_rejects_negative_wait(self) -> None:
        with pytest.raises(ValueError, match="wait"):
            Throttle(lambda: None, wait=-1.0)

    @pytest.mark.parametrize("wait", [float("nan"), float("inf")])
    def test_rejects_non_finite_wait(self, wait: float) -> None:
        with pytest.raises(ValueError, match="wait"):
            Throttle(lambda: None, wait=wait)

    def test_rejects_no_enabled_edge(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            Throttle(lambda: None, wait=1.0, leading=False, trailing=False)


class TestThrottleTrailing:
    def test_trailing_fires_after_window(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(
            lambda x: calls.append(x),
            wait=3.0,
            leading=False,
            trailing=True,
            clock=clock,
        )
        t(1)
        assert calls == []
        clock.advance(4.0)
        t._tick()
        assert calls == [1]

    def test_trailing_uses_last_args(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(
            lambda x: calls.append(x),
            wait=3.0,
            leading=False,
            trailing=True,
            clock=clock,
        )
        t(1)
        clock.advance(1.0)
        t(2)
        clock.advance(1.0)
        t(3)
        clock.advance(4.0)
        t._tick()
        assert calls == [3]

    def test_cancel_clears_trailing(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(
            lambda x: calls.append(x),
            wait=3.0,
            leading=False,
            trailing=True,
            clock=clock,
        )
        t(1)
        t.cancel()
        clock.advance(10.0)
        t._tick()
        assert calls == []


class TestThrottleLeadingAndTrailing:
    def test_leading_fires_then_trailing(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(
            lambda x: calls.append(x),
            wait=3.0,
            leading=True,
            trailing=True,
            clock=clock,
        )
        t(1)
        assert calls == [1]
        clock.advance(1.0)
        t(2)
        clock.advance(4.0)
        t._tick()
        assert calls == [1, 2]

    def test_single_call_in_window_no_trailing_double(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(
            lambda x: calls.append(x),
            wait=3.0,
            leading=True,
            trailing=True,
            clock=clock,
        )
        t(1)
        clock.advance(5.0)
        t._tick()
        assert calls == [1]

    def test_leading_only_after_quiet_period(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        t = Throttle(
            lambda x: calls.append(x),
            wait=3.0,
            leading=True,
            trailing=True,
            clock=clock,
        )
        t(1)
        clock.advance(1.0)
        t(2)
        clock.advance(1.0)
        t(3)  # leading suppressed (1s since first), trailing holds (3)
        clock.advance(1.0)
        t(4)  # leading suppressed (2s since first), trailing holds (4)
        assert calls == [1]
        clock.advance(5.0)
        t._tick()
        assert calls == [1, 4]
        # Now a new leading should fire immediately
        t(5)
        assert calls == [1, 4, 5]


# ---------------------------------------------------------------------------
# Tests — async debounce
# ---------------------------------------------------------------------------


class TestAsyncDebounce:
    def test_single_call_fires_after_wait(self) -> None:
        calls: list[int] = []

        async def fn(x: int) -> None:
            calls.append(x)

        async def run() -> None:
            d = AsyncDebouncer(fn, wait=0.05)
            d(42)
            await asyncio.sleep(0.0)
            assert calls == []
            await asyncio.sleep(0.1)
            assert calls == [42]

        asyncio.run(run())

    def test_rapid_calls_only_last_fires(self) -> None:
        calls: list[int] = []

        async def fn(x: int) -> None:
            calls.append(x)

        async def run() -> None:
            d = AsyncDebouncer(fn, wait=0.05)
            d(1)
            d(2)
            d(3)
            await asyncio.sleep(0.0)
            assert calls == []
            await asyncio.sleep(0.1)
            assert calls == [3]

        asyncio.run(run())

    def test_cancel_prevents_firing(self) -> None:
        calls: list[int] = []

        async def fn(x: int) -> None:
            calls.append(x)

        async def run() -> None:
            d = AsyncDebouncer(fn, wait=1.0)
            d(1)
            d.cancel()
            await asyncio.sleep(0.1)
            assert calls == []

        asyncio.run(run())

    def test_rejects_negative_wait(self) -> None:
        async def fn() -> None:
            pass

        with pytest.raises(ValueError, match="wait"):
            AsyncDebouncer(fn, wait=-1.0)


# ---------------------------------------------------------------------------
# Tests — multi-instance isolation
# ---------------------------------------------------------------------------


class TestMultiInstanceIsolation:
    def test_two_debouncers_independent(self) -> None:
        calls_a: list[str] = []
        calls_b: list[str] = []
        clock = SimulatedClock(0.0)
        a = Debouncer(lambda x: calls_a.append(x), wait=2.0, clock=clock)
        b = Debouncer(lambda x: calls_b.append(x), wait=4.0, clock=clock)
        a("a1")
        b("b1")
        clock.advance(2.5)
        a._tick()
        b._tick()
        assert calls_a == ["a1"]
        assert calls_b == []
        clock.advance(2.0)
        b._tick()
        assert calls_b == ["b1"]

    def test_two_throttles_isolation(self) -> None:
        calls_a: list[int] = []
        calls_b: list[int] = []
        clock = SimulatedClock(0.0)
        a = Throttle(lambda x: calls_a.append(x), wait=3.0, clock=clock)
        b = Throttle(lambda x: calls_b.append(x), wait=5.0, clock=clock)
        a(1)
        b(10)
        assert calls_a == [1]
        assert calls_b == [10]
        clock.advance(1.0)
        a(2)
        assert calls_a == [1]
        clock.advance(4.0)
        a(3)
        b(20)
        assert calls_a == [1, 3]
        assert calls_b == [10, 20]


# ---------------------------------------------------------------------------
# Tests — immediate reset edge cases
# ---------------------------------------------------------------------------


class TestImmediateReset:
    def test_flush_after_cancel_is_safe(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, clock=clock)
        d(1)
        d.cancel()
        d.flush()
        assert calls == []

    def test_drive_without_clock_advance_is_noop(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=5.0, clock=clock)
        d(1)
        d.cancel()

        class StuckClock:
            def __call__(self) -> float:
                return 0.0

        # Create one with a real-like clock — drive is a no-op without advance()
        calls2: list[int] = []
        d2 = Debouncer(lambda x: calls2.append(x), wait=10.0, clock=StuckClock())
        d2(1)
        d2.drive(999.0)
        assert calls2 == []

    def test_tick_resets_first_call_at(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = Debouncer(lambda x: calls.append(x), wait=2.0, max_wait=5.0, clock=clock)
        for i in range(3):
            d(i)
            clock.advance(0.5)
            d._tick()
        assert calls == []
        clock.advance(2.0)
        d._tick()
        assert calls == [2]
        # After firing, first_call_at is reset — verify a new quick burst
        # doesn't fire prematurely
        d(10)
        clock.advance(0.5)
        d._tick()
        assert calls == [2]
        clock.advance(2.0)
        d._tick()
        assert calls == [2, 10]
