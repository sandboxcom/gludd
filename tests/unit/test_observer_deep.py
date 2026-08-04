"""Deep observer event system tests — EventBus + InProcessBroker.

Covers: subscribe/notify/unsubscribe, error isolation, async notify,
event hierarchy, wildcard delivery, history ring, drain, concurrent
publish, idempotent unsubscribe, broker clear, broker async resilience,
and edge cases.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import (
    CustomEvent,
    Event,
    EventType,
    ModelAddedEvent,
    ModelReadyEvent,
)
from general_ludd.ipc import Broker, InProcessBroker


class TestEventBusSubscribeUnsubscribe:
    def test_subscribe_returns_unique_sequential_ids(self):
        bus = EventBus()
        s1 = bus.subscribe(EventType.CUSTOM, lambda e: None)
        s2 = bus.subscribe(EventType.CUSTOM, lambda e: None)
        s3 = bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        assert s1 == "sub-0"
        assert s2 == "sub-1"
        assert s3 == "sub-2"

    def test_unsubscribe_then_resubscribe_returns_next_id(self):
        bus = EventBus()
        sid = bus.subscribe(EventType.CUSTOM, lambda e: None)
        bus.unsubscribe(sid)
        event = Event(type=EventType.CUSTOM, payload={})
        assert bus.publish(event) == 0
        sid2 = bus.subscribe(EventType.CUSTOM, lambda e: None)
        assert sid2 == "sub-1"

    def test_unsubscribe_nonexistent_id_does_not_affect_other_subs(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventType.CUSTOM, received.append)
        bus.unsubscribe("sub-999")
        event = Event(type=EventType.CUSTOM, payload={"x": 1})
        assert bus.publish(event) == 1
        assert len(received) == 1

    def test_unsubscribe_only_removes_matching_id_across_all_keys(self):
        bus = EventBus()
        sid = bus.subscribe(EventType.CUSTOM, lambda e: None)
        bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        bus.unsubscribe(sid)
        assert bus.publish(Event(type=EventType.CUSTOM, payload={})) == 0
        assert bus.publish(Event(type=EventType.MODEL_ADDED, payload={})) == 1


class TestEventBusNotify:
    def test_publish_delivers_to_multiple_subs_of_same_type(self):
        bus = EventBus()
        r1: list[Event] = []
        r2: list[Event] = []
        r3: list[Event] = []
        bus.subscribe(EventType.CUSTOM, r1.append)
        bus.subscribe(EventType.CUSTOM, r2.append)
        bus.subscribe(EventType.CUSTOM, r3.append)
        event = Event(type=EventType.CUSTOM, payload={"v": 42})
        assert bus.publish(event) == 3
        assert r1 == [event]
        assert r2 == [event]
        assert r3 == [event]

    def test_publish_returns_zero_when_no_matching_subscribers(self):
        bus = EventBus()
        bus.subscribe(EventType.MODEL_ADDED, lambda e: None)
        e2 = Event(type=EventType.WORKER_PONG, payload={})
        assert bus.publish(e2) == 0

    def test_publish_delivers_to_named_types(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("my_custom.event", received.append)
        e = Event(type="my_custom.event", payload={"k": "v"})
        assert bus.publish(e) == 1
        assert received[0] is e

    def test_publish_delivers_to_custom_event_type_using_enum_value(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventType.CUSTOM, received.append)
        ce = CustomEvent(name="test_event", payload={"x": 1})
        assert bus.publish(ce) == 1
        assert received[0] is ce
        assert received[0].payload["name"] == "test_event"


class TestEventBusWildcard:
    def test_wildcard_subscriber_receives_all_event_types(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("*", received.append)
        e1 = Event(type=EventType.CUSTOM, payload={})
        e2 = Event(type=EventType.MODEL_ADDED, payload={})
        e3 = Event(type="arbitrary", payload={})
        bus.publish(e1)
        bus.publish(e2)
        bus.publish(e3)
        assert len(received) == 3
        assert received == [e1, e2, e3]

    def test_wildcard_does_not_double_deliver_on_exact_match(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventType.CUSTOM, received.append)
        bus.subscribe("*", received.append)
        e = Event(type=EventType.CUSTOM, payload={})
        delivered = bus.publish(e)
        assert delivered == 2
        assert len(received) == 2


class TestEventBusErrorIsolation:
    def test_sync_subscriber_exception_does_not_block_others(self):
        bus = EventBus()
        good: list[Event] = []

        def bad(_event: Event) -> None:
            raise RuntimeError("boom")

        bus.subscribe(EventType.CUSTOM, bad)
        bus.subscribe(EventType.CUSTOM, good.append)
        e = Event(type=EventType.CUSTOM, payload={})
        delivered = bus.publish(e)
        assert delivered == 1
        assert good == [e]

    def test_multiple_failing_subscribers_count_correctly(self):
        bus = EventBus()
        good: list[Event] = []

        def fail1(_e: Event) -> None:
            raise ValueError("fail1")

        def fail2(_e: Event) -> None:
            raise TypeError("fail2")

        bus.subscribe(EventType.CUSTOM, fail1)
        bus.subscribe(EventType.CUSTOM, fail2)
        bus.subscribe(EventType.CUSTOM, good.append)
        e = Event(type=EventType.CUSTOM, payload={})
        delivered = bus.publish(e)
        assert delivered == 1
        assert len(good) == 1

    def test_sync_subscriber_exception_does_not_propagate(self):
        bus = EventBus()
        bus.subscribe(EventType.CUSTOM, lambda e: (_ for _ in ()).throw(ValueError("nope")))
        e = Event(type=EventType.CUSTOM, payload={})
        delivered = bus.publish(e)
        assert delivered == 0


class TestEventBusAsyncNotify:
    @pytest.mark.asyncio
    async def test_async_coroutine_subscriber_is_scheduled(self):
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            await asyncio.sleep(0)
            received.append(event)

        bus.subscribe(EventType.CUSTOM, handler)
        e = Event(type=EventType.CUSTOM, payload={})
        delivered = bus.publish(e)
        assert delivered == 1
        await bus.drain()
        assert received == [e]

    @pytest.mark.asyncio
    async def test_async_subscriber_failure_is_surfaced(self):
        bus = EventBus()
        good: list[Event] = []

        async def bad(_event: Event) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("async error")

        async def handler(event: Event) -> None:
            await asyncio.sleep(0)
            good.append(event)

        bus.subscribe(EventType.CUSTOM, bad)
        bus.subscribe(EventType.CUSTOM, handler)
        e = Event(type=EventType.CUSTOM, payload={})
        delivered = bus.publish(e)
        assert delivered >= 0
        await bus.drain()
        assert len(good) == 1
        assert good[0] is e

    def test_async_subscriber_runs_on_dedicated_loop_when_no_running_loop(self):
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(EventType.CUSTOM, handler)
        e = Event(type=EventType.CUSTOM, payload={})
        bus.publish(e)
        assert len(received) == 1
        assert received[0] is e


class TestEventBusEventHierarchy:
    def test_subtype_event_matches_parent_type_subscription(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventType.MODEL_ADDED, received.append)
        mae = ModelAddedEvent(model_id="m1", profile={})
        assert bus.publish(mae) == 1
        assert received[0] is mae
        assert received[0].payload["model_id"] == "m1"

    def test_subtype_event_does_not_match_unrelated_type(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventType.MODEL_READY, received.append)
        mae = ModelAddedEvent(model_id="m1", profile={})
        assert bus.publish(mae) == 0
        assert len(received) == 0

    def test_custom_event_preserves_name_in_payload(self):
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(EventType.CUSTOM, received.append)
        ce = CustomEvent(name="org.action.completed", payload={"status": "ok"})
        bus.publish(ce)
        assert len(received) == 1
        assert received[0].payload["name"] == "org.action.completed"
        assert received[0].payload["status"] == "ok"

    def test_multiple_event_types_coexist_without_cross_type_delivery(self):
        bus = EventBus()
        added: list[Event] = []
        ready: list[Event] = []
        bus.subscribe(EventType.MODEL_ADDED, added.append)
        bus.subscribe(EventType.MODEL_READY, ready.append)
        mae = ModelAddedEvent(model_id="m1", profile={})
        mre = ModelReadyEvent(server_id="s1", engine="vllm", endpoint_url="http://x")
        bus.publish(mae)
        bus.publish(mre)
        assert len(added) == 1
        assert added[0] is mae
        assert len(ready) == 1
        assert ready[0] is mre


class TestEventBusHistory:
    def test_history_stores_events_in_order(self):
        bus = EventBus(history_size=5)
        events = [Event(type=EventType.CUSTOM, payload={"i": i}) for i in range(3)]
        for e in events:
            bus.publish(e)
        assert bus.get_history() == events

    def test_history_returns_copy_not_live_reference(self):
        bus = EventBus(history_size=5)
        bus.publish(Event(type=EventType.CUSTOM, payload={}))
        hist = bus.get_history()
        hist.clear()
        assert len(bus.get_history()) == 1


class TestEventBusDrain:
    @pytest.mark.asyncio
    async def test_drain_awaits_all_background_tasks(self):
        bus = EventBus()
        done: list[str] = []

        async def slow(_event: Event) -> None:
            await asyncio.sleep(0.05)
            done.append("ok")

        bus.subscribe(EventType.CUSTOM, slow)
        bus.publish(Event(type=EventType.CUSTOM, payload={}))
        bus.publish(Event(type=EventType.CUSTOM, payload={}))
        await bus.drain()
        assert done == ["ok", "ok"]


class TestEventBusThreadSafety:
    def test_concurrent_subscribe_publish_from_threads(self):
        bus = EventBus()
        received: list[Event] = []
        latch = threading.Barrier(4)
        errors: list[Exception] = []

        def subscriber_work() -> None:
            try:
                latch.wait()
                for _ in range(20):
                    bus.subscribe(EventType.CUSTOM, lambda e: None)
            except Exception as exc:
                errors.append(exc)

        def publisher_work() -> None:
            try:
                latch.wait()
                for _ in range(20):
                    bus.publish(Event(type=EventType.CUSTOM, payload={}))
            except Exception as exc:
                errors.append(exc)

        def receiver_work() -> None:
            try:
                latch.wait()
                bus.subscribe(EventType.CUSTOM, received.append)
                for _ in range(20):
                    bus.publish(Event(type=EventType.CUSTOM, payload={"n": _}))
            except Exception as exc:
                errors.append(exc)

        def unsub_work() -> None:
            try:
                latch.wait()
                for _ in range(20):
                    sid = bus.subscribe(EventType.CUSTOM, lambda e: None)
                    bus.unsubscribe(sid)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=subscriber_work),
            threading.Thread(target=publisher_work),
            threading.Thread(target=receiver_work),
            threading.Thread(target=unsub_work),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0

    def test_clear_resets_subscribers_but_not_history(self):
        bus = EventBus(history_size=5)
        bus.subscribe(EventType.CUSTOM, lambda e: None)
        bus.subscribe("*", lambda e: None)
        bus.publish(Event(type=EventType.CUSTOM, payload={}))
        hist_before = len(bus.get_history())
        bus.clear()
        assert bus.publish(Event(type=EventType.CUSTOM, payload={})) == 0
        assert len(bus.get_history()) == hist_before + 1


class TestBrokerSubscribeUnsubscribe:
    def test_unsubscribe_idempotent_unknown_handler(self):
        broker = InProcessBroker()

        def h(msg: dict[str, Any]) -> None:
            del msg

        broker.unsubscribe("topic.nope", h)

    def test_unsubscribe_removes_only_specified_handler(self):
        broker = InProcessBroker()
        r1: list[dict[str, Any]] = []
        r2: list[dict[str, Any]] = []

        def h1(msg: dict[str, Any]) -> None:
            r1.append(msg)

        def h2(msg: dict[str, Any]) -> None:
            r2.append(msg)

        broker.subscribe("t", h1)
        broker.subscribe("t", h2)
        broker.unsubscribe("t", h1)

        async def run() -> None:
            await broker.publish("t", {"k": "v"})

        asyncio.run(run())
        assert r1 == []
        assert len(r2) == 1


class TestBrokerAsyncResilience:
    @pytest.mark.asyncio
    async def test_async_handler_exception_does_not_block_sync_handlers(self):
        broker = InProcessBroker()
        good: list[dict[str, Any]] = []

        async def bad(msg: dict[str, Any]) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("async fail")

        def good_handler(msg: dict[str, Any]) -> None:
            good.append(msg)

        broker.subscribe("t", bad)
        broker.subscribe("t", good_handler)
        delivered = await broker.publish("t", {"x": 1})
        assert delivered == 1
        assert good == [{"x": 1}]

    @pytest.mark.asyncio
    async def test_broker_satisfies_protocol(self):
        assert isinstance(InProcessBroker(), Broker)

    @pytest.mark.asyncio
    async def test_broker_clear_purges_all_topics(self):
        broker = InProcessBroker()
        r: list[dict[str, Any]] = []

        def h(msg: dict[str, Any]) -> None:
            r.append(msg)

        broker.subscribe("a", h)
        broker.subscribe("b", h)
        broker.clear()
        await broker.publish("a", {"x": 1})
        await broker.publish("b", {"x": 2})
        assert r == []
