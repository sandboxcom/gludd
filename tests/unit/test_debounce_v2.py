"""Unit tests for DebounceV2 and AsyncDebounceV2."""

import asyncio

import pytest

from general_ludd.util.debounce_v2 import AsyncDebounceV2, DebounceV2


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, delta):
        self.t += delta


class TestDebounceV2:
    def test_trailing(self):
        calls = []
        clock = FakeClock()
        d = DebounceV2(lambda x: calls.append(x), wait=1.0, clock=clock)
        d("a")
        d("b")
        assert len(calls) == 0
        d.drive(1.5)
        assert calls == ["b"]

    def test_leading(self):
        calls = []
        clock = FakeClock()
        d = DebounceV2(lambda x: calls.append(x), wait=1.0, leading=True, trailing=False, clock=clock)
        d("a")
        assert calls == ["a"]
        d("b")
        assert calls == ["a"]

    def test_both(self):
        calls = []
        clock = FakeClock()
        d = DebounceV2(lambda x: calls.append(x), wait=1.0, leading=True, trailing=True, clock=clock)
        d("a")
        assert calls == ["a"]
        d("b")
        d.drive(1.5)
        assert calls == ["a", "b"]

    def test_flush(self):
        calls = []
        clock = FakeClock()
        d = DebounceV2(lambda x: calls.append(x), wait=1.0, clock=clock)
        d("a")
        d.flush()
        assert calls == ["a"]

    def test_cancel(self):
        calls = []
        clock = FakeClock()
        d = DebounceV2(lambda x: calls.append(x), wait=1.0, clock=clock)
        d("a")
        d.cancel()
        d.drive(2.0)
        assert calls == []

    def test_reset(self):
        calls = []
        clock = FakeClock()
        d = DebounceV2(lambda x: calls.append(x), wait=1.0, leading=True, clock=clock)
        d("a")
        d.reset()
        d("b")
        assert calls == ["a", "b"]

    def test_max_wait(self):
        calls = []
        clock = FakeClock()
        d = DebounceV2(lambda x: calls.append(x), wait=10.0, max_wait=2.0, clock=clock)
        d("a")
        d.drive(2.5)
        assert calls == ["a"]

    def test_invalid_wait(self):
        with pytest.raises(ValueError, match="wait must be finite"):
            DebounceV2(lambda: None, wait=-1.0)

    def test_invalid_max_wait(self):
        with pytest.raises(ValueError, match="max_wait must be finite"):
            DebounceV2(lambda: None, wait=1.0, max_wait=0.0)

    def test_no_edge(self):
        with pytest.raises(ValueError, match="at least one of leading/trailing"):
            DebounceV2(lambda: None, wait=1.0, leading=False, trailing=False)


class TestAsyncDebounceV2:
    @pytest.mark.asyncio
    async def test_trailing(self):
        calls = []

        async def fn(x):
            calls.append(x)

        d = AsyncDebounceV2(fn, wait=0.05)
        d("a")
        d("b")
        await asyncio.sleep(0.1)
        assert "b" in calls

    @pytest.mark.asyncio
    async def test_leading(self):
        calls = []

        async def fn(x):
            calls.append(x)

        d = AsyncDebounceV2(fn, wait=0.05, leading=True, trailing=False)
        d("a")
        await asyncio.sleep(0.01)
        assert calls == ["a"]

    @pytest.mark.asyncio
    async def test_cancel(self):
        calls = []

        async def fn(x):
            calls.append(x)

        d = AsyncDebounceV2(fn, wait=0.2)
        d("a")
        d.cancel()
        await asyncio.sleep(0.05)
        assert calls == []

    @pytest.mark.asyncio
    async def test_reset(self):
        calls = []

        async def fn(x):
            calls.append(x)

        d = AsyncDebounceV2(fn, wait=0.05, leading=True)
        d("a")
        d.reset()
        d("b")
        await asyncio.sleep(0.01)
        assert "b" in calls
