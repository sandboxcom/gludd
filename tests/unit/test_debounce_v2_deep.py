"""Deep tests for debounce_v2 — trailing, leading, both edges, max-wait, async."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from general_ludd.util.debounce_v2 import AsyncDebounceV2, DebounceV2

# ---------------------------------------------------------------------------
# Simulated clock
# ---------------------------------------------------------------------------


class SimulatedClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        self._now += delta


# ---------------------------------------------------------------------------
# Trailing edge
# ---------------------------------------------------------------------------


class TestDebounceV2Trailing:
    def test_single_call_fires_after_wait(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(100.0)
        d = DebounceV2(lambda x: calls.append(x), wait=5.0, trailing=True, clock=clock)
        d(42)
        assert calls == []
        d.drive(104.9)
        assert calls == []
        d.drive(105.0)
        assert calls == [42]

    def test_rapid_calls_only_last_fires(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=3.0, trailing=True, clock=clock)
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
        d = DebounceV2(lambda x: calls.append(x), wait=5.0, trailing=True, clock=clock)
        d(99)
        d.cancel()
        d.drive(10.0)
        assert calls == []

    def test_flush_invokes_immediately(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=60.0, trailing=True, clock=clock)
        d(77)
        d.flush()
        assert calls == [77]
        d.drive(120.0)
        assert calls == [77]

    def test_argument_preservation(self) -> None:
        captured: dict[str, Any] = {}

        def record(**kw: Any) -> None:
            captured.update(kw)

        clock = SimulatedClock(0.0)
        d = DebounceV2(record, wait=1.0, trailing=True, clock=clock)
        d(a=1, b="hello")
        d(a=2, b="world")
        d.drive(2.0)
        assert captured == {"a": 2, "b": "world"}

    def test_pending_property(self) -> None:
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: None, wait=10.0, trailing=True, clock=clock)
        assert not d.pending
        d(1)
        assert d.pending
        d.drive(10.0)
        assert not d.pending

    def test_rejects_negative_wait(self) -> None:
        with pytest.raises(ValueError, match="wait"):
            DebounceV2(lambda: None, wait=-0.1, trailing=True)

    @pytest.mark.parametrize("wait", [float("nan"), float("inf")])
    def test_rejects_non_finite_wait(self, wait: float) -> None:
        with pytest.raises(ValueError, match="wait"):
            DebounceV2(lambda: None, wait=wait, trailing=True)

    def test_rejects_no_edge(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            DebounceV2(lambda: None, wait=1.0, leading=False, trailing=False)


# ---------------------------------------------------------------------------
# Leading edge
# ---------------------------------------------------------------------------


class TestDebounceV2Leading:
    def test_first_call_fires_immediately(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=5.0, leading=True, trailing=False, clock=clock)
        d(10)
        assert calls == [10]

    def test_subsequent_calls_suppressed_in_window(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=5.0, leading=True, trailing=False, clock=clock)
        d(10)
        d(20)
        d(30)
        clock.advance(2.0)
        d(40)
        assert calls == [10]

    def test_fires_again_after_window(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=5.0, leading=True, trailing=False, clock=clock)
        d(1)
        clock.advance(6.0)
        d(2)
        assert calls == [1, 2]

    def test_reset_allows_immediate_leading(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=5.0, leading=True, trailing=False, clock=clock)
        d(1)
        d.reset()
        d(2)
        assert calls == [1, 2]


# ---------------------------------------------------------------------------
# Both edges (leading + trailing)
# ---------------------------------------------------------------------------


class TestDebounceV2Both:
    def test_leading_fires_then_trailing_after_window(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=3.0, leading=True, trailing=True, clock=clock)
        d(1)
        assert calls == [1]
        clock.advance(1.0)
        d(2)
        clock.advance(4.0)
        d._tick()
        assert calls == [1, 2]

    def test_no_double_fire_on_single_call(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=3.0, leading=True, trailing=True, clock=clock)
        d(1)
        clock.advance(5.0)
        d._tick()
        assert calls == [1]

    def test_three_bursts_both_edges(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=3.0, leading=True, trailing=True, clock=clock)
        d(1)
        clock.advance(1.0)
        d(2)
        clock.advance(1.0)
        d(3)
        assert calls == [1]
        clock.advance(2.0)
        d._tick()
        assert calls == [1, 3]
        d(4)
        assert calls == [1, 3, 4]


# ---------------------------------------------------------------------------
# Max wait
# ---------------------------------------------------------------------------


class TestDebounceV2MaxWait:
    def test_max_wait_fires_during_sustained_burst(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=10.0, max_wait=3.0, trailing=True, clock=clock)
        for i in range(20):
            d(i)
            clock.advance(0.5)
            d._tick()
        assert len(calls) >= 3, f"expected >=3 max-wait firings, got {len(calls)}"

    def test_max_wait_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_wait"):
            DebounceV2(lambda: None, wait=3.0, max_wait=0.0, trailing=True)

    @pytest.mark.parametrize("max_wait", [float("nan"), float("inf")])
    def test_max_wait_must_be_finite(self, max_wait: float) -> None:
        with pytest.raises(ValueError, match="max_wait"):
            DebounceV2(lambda: None, wait=3.0, max_wait=max_wait, trailing=True)

    def test_max_wait_single_call_still_waits(self) -> None:
        calls: list[int] = []
        clock = SimulatedClock(0.0)
        d = DebounceV2(lambda x: calls.append(x), wait=5.0, max_wait=10.0, trailing=True, clock=clock)
        d(1)
        d.drive(4.9)
        assert calls == []
        d.drive(5.0)
        assert calls == [1]


# ---------------------------------------------------------------------------
# Async debounce
# ---------------------------------------------------------------------------


class TestAsyncDebounceV2:
    def test_trailing_single_call(self) -> None:
        calls: list[int] = []

        async def fn(x: int) -> None:
            calls.append(x)

        async def run() -> None:
            d = AsyncDebounceV2(fn, wait=0.05, trailing=True)
            d(42)
            await asyncio.sleep(0.0)
            assert calls == []
            await asyncio.sleep(0.1)
            assert calls == [42]

        asyncio.run(run())

    def test_trailing_rapid_calls(self) -> None:
        calls: list[int] = []

        async def fn(x: int) -> None:
            calls.append(x)

        async def run() -> None:
            d = AsyncDebounceV2(fn, wait=0.05, trailing=True)
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
            d = AsyncDebounceV2(fn, wait=1.0, trailing=True)
            d(1)
            d.cancel()
            await asyncio.sleep(0.1)
            assert calls == []

        asyncio.run(run())

    def test_leading_fires_immediately(self) -> None:
        calls: list[int] = []

        async def fn(x: int) -> None:
            calls.append(x)

        async def run() -> None:
            d = AsyncDebounceV2(fn, wait=1.0, leading=True, trailing=False)
            d(10)
            await asyncio.sleep(0.01)
            assert calls == [10]
            d(20)
            await asyncio.sleep(0.01)
            assert calls == [10]

        asyncio.run(run())

    def test_both_edges_leading_and_trailing(self) -> None:
        calls: list[int] = []

        async def fn(x: int) -> None:
            calls.append(x)

        async def run() -> None:
            d = AsyncDebounceV2(fn, wait=0.05, leading=True, trailing=True)
            d(1)
            await asyncio.sleep(0.01)
            assert calls == [1]
            d(2)
            await asyncio.sleep(0.1)
            assert calls == [1, 2]

        asyncio.run(run())

    def test_aclose_cancels_and_awaits_leading_and_trailing_tasks(self) -> None:
        async def fn(_value: int) -> None:
            await asyncio.Event().wait()

        async def run() -> None:
            d = AsyncDebounceV2(fn, wait=60.0, leading=True, trailing=True)
            d(1)
            d(2)
            await asyncio.sleep(0)

            await d.aclose()

            assert d._task is None
            assert not d._leading_tasks

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Multi-instance isolation
# ---------------------------------------------------------------------------


class TestDebounceV2Isolation:
    def test_two_instances_independent(self) -> None:
        calls_a: list[str] = []
        calls_b: list[str] = []
        clock = SimulatedClock(0.0)
        a = DebounceV2(lambda x: calls_a.append(x), wait=2.0, trailing=True, clock=clock)
        b = DebounceV2(lambda x: calls_b.append(x), wait=4.0, trailing=True, clock=clock)
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
