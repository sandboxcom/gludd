from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event


class TestCoroutineFunctionBranchWithRunningLoop:
    @pytest.mark.asyncio
    async def test_elif_branch_with_running_loop(self):
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test.elif_loop", handler)
        with patch("general_ludd.events.bus.asyncio.iscoroutine", return_value=False):
            bus.publish(Event(type="test.elif_loop", payload={"k": "v"}))

        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].payload["k"] == "v"


class TestCoroutineFunctionBranchNoRunningLoop:
    def test_elif_branch_asyncio_run_fallback(self):
        bus = EventBus()
        received: list[str] = []

        async def handler(event: Event) -> None:
            received.append(event.type)

        bus.subscribe("test.elif_noloop", handler)
        with patch("general_ludd.events.bus.asyncio.iscoroutine", return_value=False):
            bus.publish(Event(type="test.elif_noloop"))

        assert received == ["test.elif_noloop"]


class TestBackgroundTaskDiscard:
    @pytest.mark.asyncio
    async def test_background_tasks_cleaned_after_completion(self):
        bus = EventBus()

        async def handler(event: Event) -> None:
            pass

        bus.subscribe("test.discard", handler)
        bus.publish(Event(type="test.discard"))

        assert len(bus._background_tasks) > 0
        await asyncio.sleep(0.1)
        assert len(bus._background_tasks) == 0


class TestCoroutineFunctionBranchException:
    @pytest.mark.asyncio
    async def test_elif_branch_exception_in_running_loop(self):
        bus = EventBus()

        async def handler(event: Event) -> None:
            raise ValueError("elif boom")

        bus.subscribe("test.elif_exc", handler)
        with patch("general_ludd.events.bus.asyncio.iscoroutine", return_value=False):
            count = bus.publish(Event(type="test.elif_exc"))

        assert count == 1
        await asyncio.sleep(0.05)

    def test_elif_branch_exception_asyncio_run(self):
        bus = EventBus()

        async def handler(event: Event) -> None:
            raise ValueError("elif noloop boom")

        bus.subscribe("test.elif_exc_noloop", handler)
        with patch("general_ludd.events.bus.asyncio.iscoroutine", return_value=False):
            count = bus.publish(Event(type="test.elif_exc_noloop"))

        assert count == 1
