"""Deep tests for debounce/throttle primitives.

Covers: trailing edge, leading edge, max-wait, async debounce, throttle
(leading/trailing/both), immediate reset, cancel, flush, zero-wait,
multi-instance isolation, and argument preservation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest

# ---------------------------------------------------------------------------
# Inline implementations (extract to src/general_ludd/utils/debounce.py later)
# ---------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Any])
AF = TypeVar("AF", bound=Callable[..., Awaitable[Any]])


class Debouncer:
    """Trailing-edge debounce with optional leading-edge and max-wait support.

    ``wait`` seconds of inactivity must pass before *fn* is called.  If
    ``leading=True`` the very first call fires immediately and subsequent
    calls within the window are suppressed.  If *max_wait* is set and
    non-None, the callback is guaranteed to fire at least once per
    *max_wait* seconds during a sustained burst (trailing-only mode).
    """

    def __init__(
        self,
        fn: F,
        wait: float,
        *,
        leading: bool = False,
        max_wait: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if wait < 0:
            raise ValueError("wait must be >= 0")
        if max_wait is not None and max_wait < 0:
            raise ValueError("max_wait must be >= 0")
        if max_wait is not None and max_wait < wait:
            raise ValueError("max_wait must be >= wait")
        self._fn: Callable[..., Any] = fn
        self._wait: float = wait
        self._leading: bool = leading
        self._max_wait: float | None = max_wait
        self._clock: Any = clock
        self._timer: float | None = None
        self._first_call_at: float | None = None
        self._pending_args: tuple[tuple[Any, ...], dict[str, Any]] | None = None
        self._last_leading_epoch: float = -float("inf")

    @property
    def pending(self) -> bool:
        return self._pending_args is not None

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        now = self._clock()

        if self._leading:
            if now - self._last_leading_epoch >= self._wait:
                self._last_leading_epoch = now
                self._fn(*args, **kwargs)
            return

        self._pending_args = (args, kwargs)
        if self._first_call_at is None:
            self._first_call_at = now

        due_at = now + self._wait
        if self._max_wait is not None and self._first_call_at is not None:
            due_at = min(due_at, self._first_call_at + self._max_wait)

        if self._timer is None:
            self._timer = due_at

    def _tick(self) -> None:
        """Single clock tick — invoke if the timer has elapsed (caller drives)."""
        if self._pending_args is None or self._timer is None:
            return
        now = self._clock()
        if now < self._timer:
            return
        args, kwargs = self._pending_args
        self._pending_args = None
        self._timer = None
        self._first_call_at = None
        self._fn(*args, **kwargs)

    def drive(self, until: float) -> None:
        """Advance time (simulated clock only) and fire any due callbacks."""
        if callable(getattr(self._clock, "advance", None)):
            self._clock.advance(until - self._clock())
            self._tick()

    def cancel(self) -> None:
        self._pending_args = None
        self._timer = None
        self._first_call_at = None

    def flush(self) -> None:
        if self._pending_args is not None:
            args, kwargs = self._pending_args
            self.cancel()
            self._fn(*args, **kwargs)

    def reset(self) -> None:
        self.cancel()
        self._last_leading_epoch = 0.0


class Throttle:
    """Rate-limit *fn* to at most once per ``wait`` seconds.

    By default (leading=True, trailing=False) the very first call fires
    immediately and subsequent calls within the window are suppressed.
    When *trailing=True* the most recent args are held and fired after
    the window expires (even if intermediate calls arrived).  Both flags
    can be set together.
    """

    def __init__(
        self,
        fn: F,
        wait: float,
        *,
        leading: bool = True,
        trailing: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if wait < 0:
            raise ValueError("wait must be >= 0")
        self._fn: Callable[..., Any] = fn
        self._wait: float = wait
        self._leading: bool = leading
        self._trailing: bool = trailing
        self._clock: Any = clock
        self._last_fired: float = 0.0
        self._pending_args: tuple[tuple[Any, ...], dict[str, Any]] | None = None
        self._timer: float | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        now = self._clock()
        elapsed = now - self._last_fired

        if self._leading and elapsed >= self._wait:
            self._last_fired = now
            self._pending_args = None
            self._timer = None
            self._fn(*args, **kwargs)
            return

        if self._trailing:
            self._pending_args = (args, kwargs)
            if self._timer is None:
                self._timer = self._last_fired + self._wait

        if self._leading and elapsed < self._wait:
            pass

    def _tick(self) -> None:
        if self._pending_args is None or self._timer is None:
            return
        now = self._clock()
        if now < self._timer:
            return
        args, kwargs = self._pending_args
        self._pending_args = None
        self._timer = None
        self._last_fired = now
        self._fn(*args, **kwargs)

    def drive(self, until: float) -> None:
        if callable(getattr(self._clock, "advance", None)):
            self._clock.advance(until - self._clock())
            self._tick()

    def cancel(self) -> None:
        self._pending_args = None
        self._timer = None

    def reset(self) -> None:
        self.cancel()
        self._last_fired = 0.0


class AsyncDebouncer:
    """Trailing-edge async debouncer.  Must be driven via ``asyncio.sleep``
    in a task spawned by the caller.  *fn* is an async callable invoked
    after *wait* seconds of inactivity.
    """

    def __init__(self, fn: AF, wait: float) -> None:
        if wait < 0:
            raise ValueError("wait must be >= 0")
        self._fn: Callable[..., Awaitable[Any]] = fn
        self._wait: float = wait
        self._pending_args: tuple[tuple[Any, ...], dict[str, Any]] | None = None
        self._task: asyncio.Task[Any] | None = None

    @property
    def pending(self) -> bool:
        t = self._task
        return t is not None and not t.done()

    async def _run_after(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        await asyncio.sleep(self._wait)
        if self._pending_args == (args, kwargs):
            self._pending_args = None
            await self._fn(*args, **kwargs)

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self._pending_args = (args, kwargs)
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._run_after(args, kwargs))

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
        self._pending_args = None


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
        d(1)
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

    def test_max_wait_must_be_gte_wait(self) -> None:
        with pytest.raises(ValueError, match="max_wait"):
            Debouncer(lambda: None, wait=3.0, max_wait=2.0)

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
