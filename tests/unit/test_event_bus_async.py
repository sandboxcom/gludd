"""Tests for events/bus.py — covering async subscriber paths to push coverage >85%."""

from __future__ import annotations

import asyncio

import pytest

from general_ludd.events.types import Event


class TestEventBusAsyncPaths:
    @pytest.mark.asyncio
    async def test_emit_to_async_callback_with_running_loop(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test.running_loop", handler)
        bus.publish(Event(type="test.running_loop", payload={"data": "hello"}))

        await asyncio.sleep(0.01)
        assert len(received) >= 1
        assert received[0].type == "test.running_loop"
        assert received[0].payload["data"] == "hello"

    @pytest.mark.asyncio
    async def test_emit_to_sync_callback_returning_coroutine(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        received: list[Event] = []

        def handler(event: Event):
            async def _inner():
                received.append(event)

            return _inner()

        bus.subscribe("test.coro_return", handler)
        bus.publish(Event(type="test.coro_return", payload={"x": 1}))

        await asyncio.sleep(0.01)
        assert len(received) >= 1
        assert received[0].payload["x"] == 1

    @pytest.mark.asyncio
    async def test_emit_multiple_subscribers(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        results: list[str] = []

        async def sub1(event: Event) -> None:
            results.append("sub1")

        async def sub2(event: Event) -> None:
            results.append("sub2")

        bus.subscribe("test.multi", sub1)
        bus.subscribe("test.multi", sub2)
        bus.publish(Event(type="test.multi"))

        await asyncio.sleep(0.01)
        assert "sub1" in results
        assert "sub2" in results

    @pytest.mark.asyncio
    async def test_emit_no_subscribers(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        bus.publish(Event(type="test.nobody"))

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_handler(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        results: list[str] = []

        async def handler(event: Event) -> None:
            results.append("called")

        sub_id = bus.subscribe("test.unsub", handler)
        bus.unsubscribe(sub_id)
        bus.publish(Event(type="test.unsub"))

        await asyncio.sleep(0.01)
        assert results == []

    @pytest.mark.asyncio
    async def test_subscriber_error_does_not_crash_bus(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        results: list[str] = []

        async def bad_handler(event: Event) -> None:
            raise ValueError("test error")

        async def good_handler(event: Event) -> None:
            results.append("good")

        bus.subscribe("test.error", bad_handler)
        bus.subscribe("test.error", good_handler)
        bus.publish(Event(type="test.error"))

        await asyncio.sleep(0.01)
        assert "good" in results

    @pytest.mark.asyncio
    async def test_async_generator_callback(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        received: list[Event] = []
        done = asyncio.Event()

        def handler(event: Event):
            async def _process():
                received.append(event)
                done.set()

            return _process()

        bus.subscribe("test.gen", handler)
        bus.publish(Event(type="test.gen", payload={"value": 42}))

        await asyncio.wait_for(done.wait(), timeout=1.0)
        assert received[0].payload["value"] == 42

    @pytest.mark.asyncio
    async def test_emit_then_sleep_long_enough(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test.sleep", handler)
        bus.publish(Event(type="test.sleep", payload={"msg": "delayed"}))

        await asyncio.sleep(0.05)
        assert len(received) >= 1

    @pytest.mark.asyncio
    async def test_multiple_emits_sequentially(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        received: list[int] = []

        async def handler(event: Event) -> None:
            received.append(event.payload["seq"])

        bus.subscribe("test.seq", handler)
        for i in range(5):
            bus.publish(Event(type="test.seq", payload={"seq": i}))

        await asyncio.sleep(0.02)
        assert received == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_background_tasks_cleaned_up(self):
        from general_ludd.events.bus import EventBus

        bus = EventBus()
        count = 0

        async def handler(event: Event) -> None:
            nonlocal count
            count += 1

        bus.subscribe("test.cleanup", handler)
        for _ in range(10):
            bus.publish(Event(type="test.cleanup"))

        await asyncio.sleep(0.02)
        assert count == 10
        assert len(bus._background_tasks) == 0
