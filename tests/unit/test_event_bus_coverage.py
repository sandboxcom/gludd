from __future__ import annotations

import asyncio

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event, EventType


class TestSubscribeWithStringEventType:
    @pytest.mark.asyncio
    async def test_subscribe_string_event_type(self):
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        sub_id = bus.subscribe("my.custom.event", handler)
        assert sub_id.startswith("sub-")
        bus.publish(Event(type="my.custom.event", payload={"v": 1}))
        await asyncio.sleep(0.01)
        assert len(received) == 1
        assert received[0].payload["v"] == 1


class TestSubscribeWithEventTypeEnum:
    @pytest.mark.asyncio
    async def test_subscribe_enum_event_type(self):
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(EventType.MODEL_ADDED, handler)
        bus.publish(Event(type=EventType.MODEL_ADDED, payload={"model_id": "x"}))
        await asyncio.sleep(0.01)
        assert len(received) == 1
        assert received[0].payload["model_id"] == "x"


class TestWildcardSubscribers:
    @pytest.mark.asyncio
    async def test_wildcard_receives_all_events(self):
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("*", handler)
        bus.publish(Event(type="anything"))
        bus.publish(Event(type="something.else"))
        await asyncio.sleep(0.01)
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_wildcard_and_specific_both_fire(self):
        bus = EventBus()
        specific: list[Event] = []
        wildcard: list[Event] = []

        async def specific_handler(event: Event) -> None:
            specific.append(event)

        async def wildcard_handler(event: Event) -> None:
            wildcard.append(event)

        bus.subscribe("test.wild", specific_handler)
        bus.subscribe("*", wildcard_handler)
        bus.publish(Event(type="test.wild"))
        await asyncio.sleep(0.01)
        assert len(specific) == 1
        assert len(wildcard) == 1


class TestPublishReturnsSubscriberCount:
    def test_returns_count(self):
        bus = EventBus()

        def handler(event: Event) -> None:
            pass

        bus.subscribe("test.count", handler)
        bus.subscribe("test.count", handler)
        count = bus.publish(Event(type="test.count"))
        assert count == 2

    def test_returns_zero_for_no_subscribers(self):
        bus = EventBus()
        count = bus.publish(Event(type="test.none"))
        assert count == 0


class TestPublishWithSyncCallback:
    def test_sync_callback_invoked_directly(self):
        bus = EventBus()
        received: list[Event] = []

        def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test.sync", handler)
        bus.publish(Event(type="test.sync"))
        assert len(received) == 1


class TestPublishWithCoroutineFunctionCallback:
    @pytest.mark.asyncio
    async def test_coroutine_function_callback(self):
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("test.corofn", handler)
        bus.publish(Event(type="test.corofn"))
        await asyncio.sleep(0.01)
        assert len(received) == 1


class TestPublishCallbackException:
    def test_sync_callback_exception_logged(self):
        bus = EventBus()

        def bad_handler(event: Event) -> None:
            raise RuntimeError("boom")

        bus.subscribe("test.exc", bad_handler)
        count = bus.publish(Event(type="test.exc"))
        assert count == 1

    @pytest.mark.asyncio
    async def test_async_callback_exception_does_not_stop_others(self):
        bus = EventBus()
        results: list[str] = []

        async def bad(event: Event) -> None:
            raise ValueError("async error")

        async def good(event: Event) -> None:
            results.append("ok")

        bus.subscribe("test.async_exc", bad)
        bus.subscribe("test.async_exc", good)
        bus.publish(Event(type="test.async_exc"))
        await asyncio.sleep(0.01)
        assert "ok" in results


class TestUnsubscribeMultipleKeys:
    @pytest.mark.asyncio
    async def test_unsubscribe_scans_all_keys(self):
        bus = EventBus()
        results: list[str] = []

        async def handler(event: Event) -> None:
            results.append(event.type)

        bus.subscribe("test.a", handler)
        sub_b = bus.subscribe("test.b", handler)
        bus.unsubscribe(sub_b)
        bus.publish(Event(type="test.b"))
        await asyncio.sleep(0.01)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_preserves_other_subscriptions(self):
        bus = EventBus()
        results: list[str] = []

        async def handler_a(event: Event) -> None:
            results.append("a")

        async def handler_b(event: Event) -> None:
            results.append("b")

        sub_a = bus.subscribe("test.keep", handler_a)
        bus.subscribe("test.keep", handler_b)
        bus.unsubscribe(sub_a)
        bus.publish(Event(type="test.keep"))
        await asyncio.sleep(0.01)
        assert results == ["b"]


class TestHistoryManagement:
    def test_history_disabled_by_default(self):
        bus = EventBus()
        bus.publish(Event(type="test.no_hist"))
        assert bus.get_history() == []

    def test_history_records_events(self):
        bus = EventBus(history_size=10)
        bus.publish(Event(type="test.hist1"))
        bus.publish(Event(type="test.hist2"))
        history = bus.get_history()
        assert len(history) == 2
        assert history[0].type == "test.hist1"
        assert history[1].type == "test.hist2"

    def test_history_trims_to_size(self):
        bus = EventBus(history_size=2)
        bus.publish(Event(type="test.a"))
        bus.publish(Event(type="test.b"))
        bus.publish(Event(type="test.c"))
        history = bus.get_history()
        assert len(history) == 2
        assert history[0].type == "test.b"
        assert history[1].type == "test.c"

    def test_get_history_returns_copy(self):
        bus = EventBus(history_size=10)
        bus.publish(Event(type="test.copy"))
        h1 = bus.get_history()
        h2 = bus.get_history()
        assert h1 is not h2
        assert h1 == h2


class TestClear:
    @pytest.mark.asyncio
    async def test_clear_removes_all_subscribers(self):
        bus = EventBus()
        results: list[str] = []

        async def handler(event: Event) -> None:
            results.append("called")

        bus.subscribe("test.clear", handler)
        bus.clear()
        bus.publish(Event(type="test.clear"))
        await asyncio.sleep(0.01)
        assert results == []

    @pytest.mark.asyncio
    async def test_clear_removes_wildcard_subscribers(self):
        bus = EventBus()
        results: list[str] = []

        async def handler(event: Event) -> None:
            results.append("called")

        bus.subscribe("*", handler)
        bus.clear()
        bus.publish(Event(type="anything"))
        await asyncio.sleep(0.01)
        assert results == []


class TestSubscribeIdIncrementing:
    def test_sub_ids_increment(self):
        bus = EventBus()

        def handler(event: Event) -> None:
            pass

        id1 = bus.subscribe("test.inc", handler)
        id2 = bus.subscribe("test.inc", handler)
        id3 = bus.subscribe("test.inc", handler)
        assert id1 == "sub-0"
        assert id2 == "sub-1"
        assert id3 == "sub-2"


class TestAsyncFallbackNoRunningLoop:
    def test_coroutine_result_asyncio_run_fallback(self):
        bus = EventBus()
        results: list[str] = []

        async def handler(event: Event) -> None:
            results.append(event.type)

        bus.subscribe("test.noloop", handler)
        count = bus.publish(Event(type="test.noloop"))
        assert count == 1
        assert results == ["test.noloop"]

    def test_coroutine_function_asyncio_run_fallback(self):
        bus = EventBus()
        results: list[str] = []

        async def handler(event: Event) -> None:
            results.append(event.type)

        bus.subscribe("test.noloop_corofn", handler)
        count = bus.publish(Event(type="test.noloop_corofn"))
        assert count == 1
        assert results == ["test.noloop_corofn"]

    def test_async_callback_exception_no_loop(self):
        bus = EventBus()

        async def bad(event: Event) -> None:
            raise RuntimeError("async boom no loop")

        bus.subscribe("test.async_exc_noloop", bad)
        count = bus.publish(Event(type="test.async_exc_noloop"))
        assert count == 1
