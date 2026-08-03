"""Deep message bus and event bus tests: pub/sub routing, wildcard matching,
backpressure, replay, persistent queues, dead letter handling.

Covers:
  - EventBus (events/bus.py) — concurrency, iterator safety, drain, history edge cases
  - InProcessBroker (ipc/broker.py) — multi-topic, handler lifecycle, concurrent publish
  - WriteQueue (ipc/queue.py) — backpressure semantics, concurrent puts, dead letter via REJECT
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event, EventType
from general_ludd.ipc.broker import InProcessBroker
from general_ludd.ipc.queue import Envelope, OverflowPolicy, WriteQueue

# ── EventBus — concurrency and iterator safety ──────────────────────────────


class TestEventBusThreadSafety:
    def test_concurrent_publishes_from_multiple_threads(self):
        bus = EventBus(history_size=100)
        received: list[int] = []
        lock = threading.Lock()

        def handler(e: Event) -> None:
            with lock:
                received.append(e.payload["seq"])

        bus.subscribe("concurrent", handler)

        def publish_range(start: int, count: int) -> None:
            for i in range(start, start + count):
                bus.publish(Event(type="concurrent", payload={"seq": i}))

        threads = [
            threading.Thread(target=publish_range, args=(0, 50)),
            threading.Thread(target=publish_range, args=(50, 50)),
            threading.Thread(target=publish_range, args=(100, 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) == 150
        assert sorted(received) == list(range(150))

    def test_subscribe_during_publish_is_safe(self):
        bus = EventBus()
        results: list[str] = []

        def handler(e: Event) -> None:
            results.append(e.payload["marker"])

        bus.subscribe("live", handler)

        def late_subscriber(e: Event) -> None:
            if e.payload.get("subscribe_now"):
                bus.subscribe("live", lambda ev: results.append("late-" + ev.payload["marker"]))

        bus.subscribe("live", late_subscriber)
        bus.publish(Event(type="live", payload={"marker": "first", "subscribe_now": True}))
        bus.publish(Event(type="live", payload={"marker": "second"}))

        assert "first" in results
        assert "second" in results
        assert "late-second" in results

    def test_unsubscribe_during_publish_is_safe(self):
        bus = EventBus()
        received: list[str] = []
        sub_id_to_remove: list[str] = []

        def remover(e: Event) -> None:
            if sub_id_to_remove:
                bus.unsubscribe(sub_id_to_remove[0])
            received.append("remover-" + e.payload["n"])

        def target(e: Event) -> None:
            received.append("target-" + e.payload["n"])

        sid = bus.subscribe("safe", target)
        sub_id_to_remove.append(sid)
        bus.subscribe("safe", remover)

        bus.publish(Event(type="safe", payload={"n": "1"}))
        bus.publish(Event(type="safe", payload={"n": "2"}))

        assert "remover-1" in received
        assert "remover-2" in received
        assert "target-1" in received
        assert "target-2" not in received

    def test_many_subscribers_on_single_topic(self):
        bus = EventBus()
        N = 500
        counts: dict[int, int] = {i: 0 for i in range(N)}

        for i in range(N):

            def make_handler(idx: int):
                def h(e: Event) -> None:
                    counts[idx] += 1

                return h

            bus.subscribe("bulk", make_handler(i))

        bus.publish(Event(type="bulk", payload={}))
        assert all(v == 1 for v in counts.values())

    def test_subscribe_after_clear_restarts_ids(self):
        bus = EventBus()
        sid1 = bus.subscribe("t", lambda e: None)
        bus.clear()
        sid2 = bus.subscribe("t", lambda e: None)
        assert sid1 == "sub-0"
        assert sid2 == "sub-1"


# ── EventBus — wildcard matching deep ───────────────────────────────────────


class TestEventBusWildcardDeep:
    def test_wildcard_receives_all_event_types(self):
        bus = EventBus(history_size=10)
        received: list[str] = []

        bus.subscribe("*", lambda e: received.append(e.type if isinstance(e.type, str) else e.type.value))

        bus.publish(Event(type=EventType.MODEL_ADDED, payload={}))
        bus.publish(Event(type=EventType.WORKER_PING, payload={}))
        bus.publish(Event(type="custom.foo", payload={}))

        assert "model_added" in received
        assert "worker_ping" in received
        assert "custom.foo" in received
        assert len(received) == 3

    def test_wildcard_and_exact_both_fire(self):
        bus = EventBus()
        exact_received: list[Event] = []
        wild_received: list[Event] = []

        bus.subscribe("dual", exact_received.append)
        bus.subscribe("*", wild_received.append)

        event = Event(type="dual", payload={"k": "v"})
        bus.publish(event)

        assert len(exact_received) == 1
        assert exact_received[0] is event
        assert len(wild_received) == 1
        assert wild_received[0] is event

    def test_wildcard_not_matched_by_non_wildcard(self):
        bus = EventBus()
        received: list[Event] = []

        bus.subscribe("only.this", received.append)
        bus.publish(Event(type="other", payload={}))
        assert len(received) == 0

    def test_wildcard_with_history_captures_all(self):
        bus = EventBus(history_size=3)
        bus.subscribe("*", lambda e: None)

        bus.publish(Event(type="a"))
        bus.publish(Event(type="b"))
        bus.publish(Event(type="c"))

        history = bus.get_history()
        assert [e.type for e in history] == ["a", "b", "c"]

    def test_wildcard_subscriber_exception_isolated(self):
        bus = EventBus()

        def failing(_e: Event) -> None:
            raise RuntimeError("wild boom")

        good: list[str] = []
        bus.subscribe("*", failing)
        bus.subscribe("*", lambda e: good.append(e.type if isinstance(e.type, str) else e.type.value))

        bus.publish(Event(type="x"))
        assert "x" in good


# ── EventBus — history replay and edge cases ────────────────────────────────


class TestEventBusHistoryDeep:
    def test_history_size_one_truncates_correctly(self):
        bus = EventBus(history_size=1)
        e1 = Event(type="first")
        e2 = Event(type="second")
        bus.publish(e1)
        bus.publish(e2)
        assert bus.get_history() == [e2]

    def test_history_survives_clear(self):
        bus = EventBus(history_size=5)
        bus.publish(Event(type="keep"))
        assert len(bus.get_history()) == 1
        bus.clear()
        assert len(bus.get_history()) == 1

    def test_history_returns_copy_not_reference(self):
        bus = EventBus(history_size=5)
        bus.publish(Event(type="original"))
        hist = bus.get_history()
        hist.clear()
        assert len(bus.get_history()) == 1

    def test_replay_all_events_in_order(self):
        bus = EventBus(history_size=100)
        types = ["a", "b", "c", "d", "e"]
        for t in types:
            bus.publish(Event(type=t))

        replay = bus.get_history()
        assert [e.type for e in replay] == types
        assert len(replay) == 5

    def test_history_with_no_subscribers_still_records(self):
        bus = EventBus(history_size=10)
        bus.publish(Event(type="lonely"))
        assert bus.get_history()[0].type == "lonely"


# ── EventBus — drain and async task lifecycle ───────────────────────────────


class TestEventBusDrainDeep:
    @pytest.mark.asyncio
    async def test_drain_awaits_all_outstanding_async_tasks(self):
        bus = EventBus()
        completed: list[str] = []

        async def slow(event: Event) -> None:
            await asyncio.sleep(0.02)
            completed.append(event.type if isinstance(event.type, str) else event.type.value)

        bus.subscribe("drain.test", slow)
        bus.publish(Event(type="drain.test"))
        bus.publish(Event(type="drain.test"))
        assert len(bus._background_tasks) == 2

        await bus.drain()
        assert len(completed) == 2
        assert len(bus._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_drain_handles_chained_publishes(self):
        bus = EventBus()
        count = 0

        async def publisher(event: Event) -> None:
            nonlocal count
            count += 1
            if count < 3:
                bus.publish(Event(type="chain"))

        bus.subscribe("chain", publisher)
        bus.publish(Event(type="chain"))
        for _ in range(10):
            await asyncio.sleep(0.005)
            if count >= 3 and not bus._background_tasks:
                break
        assert count == 3
        assert len(bus._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_drain_on_empty_bus_is_noop(self):
        bus = EventBus()
        await bus.drain()

    @pytest.mark.asyncio
    async def test_cancelled_task_does_not_log_error(self):
        bus = EventBus()
        bus_logger = logging.getLogger("general_ludd.events.bus")

        async def doomed(event: Event) -> None:
            await asyncio.sleep(0)

        bus.subscribe("doomed", doomed)
        bus.publish(Event(type="doomed"))

        task = next(iter(bus._background_tasks))
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(task, return_exceptions=True)

        with patch.object(bus_logger, "error", wraps=bus_logger.error) as mock_error:
            bus._on_task_done(task)

        error_texts = [str(a) for call in mock_error.mock_calls if call.args for a in call.args]
        assert not any("failed" in t and "cancelled" not in t.lower() for t in error_texts)

    def test_dispatch_coro_outside_running_loop(self):
        bus = EventBus()

        async def side_effect(event: Event) -> None:
            raise RuntimeError("isolated loop boom")

        bus.subscribe("isolated.fail", side_effect)
        delivered = bus.publish(Event(type="isolated.fail"))
        assert delivered == 1


# ── EventBus — event attribute propagation ──────────────────────────────────


class TestEventBusEventIntegrity:
    def test_event_id_preserved(self):
        bus = EventBus(history_size=5)
        event = Event(type="id.test", event_id="abc123")
        bus.publish(event)
        assert bus.get_history()[0].event_id == "abc123"

    def test_correlation_id_propagates(self):
        bus = EventBus()
        seen_cid: list[str | None] = []

        bus.subscribe("cid.test", lambda e: seen_cid.append(e.correlation_id))
        event = Event(type="cid.test", correlation_id="corr-42")
        bus.publish(event)
        assert seen_cid == ["corr-42"]

    def test_timestamp_roughly_now(self):
        bus = EventBus(history_size=1)
        before = time.time()
        event = Event(type="ts.test")
        bus.publish(event)
        after = time.time()
        ts = bus.get_history()[0].timestamp
        assert before <= ts <= after + 0.01

    def test_source_attribute_preserved(self):
        bus = EventBus(history_size=5)
        event = Event(type="src.test", source="agent-7")
        bus.publish(event)
        assert bus.get_history()[0].source == "agent-7"


# ── InProcessBroker — deep routing and lifecycle ────────────────────────────


class TestInProcessBrokerDeep:
    @pytest.mark.asyncio
    async def test_publish_to_wrong_topic_not_delivered(self):
        b = InProcessBroker()
        received: list[dict] = []
        b.subscribe("topic.a", received.append)
        await b.publish("topic.b", {"msg": "hello"})
        assert received == []

    @pytest.mark.asyncio
    async def test_multiple_handlers_on_same_topic_all_fire(self):
        b = InProcessBroker()
        results: list[int] = []
        b.subscribe("multi", lambda m: results.append(1))
        b.subscribe("multi", lambda m: results.append(2))
        b.subscribe("multi", lambda m: results.append(3))
        await b.publish("multi", {})
        assert results == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_handler_can_subscribe_during_publish(self):
        b = InProcessBroker()
        seen: list[str] = []

        def adder(m: dict) -> None:
            b.subscribe("dynamic", lambda msg: seen.append("dynamic-" + msg["n"]))

        b.subscribe("trigger", adder)

        await b.publish("trigger", {"n": "1"})
        await b.publish("dynamic", {"n": "2"})
        assert "dynamic-2" in seen

    @pytest.mark.asyncio
    async def test_handler_returning_coroutine_is_awaited(self):
        b = InProcessBroker()
        done: list[int] = []

        def handler(m: dict) -> Any:
            async def inner():
                await asyncio.sleep(0)
                done.append(m["id"])

            return inner()

        b.subscribe("coro", handler)
        await b.publish("coro", {"id": 77})
        assert done == [77]

    @pytest.mark.asyncio
    async def test_unsubscribe_idempotent(self):
        b = InProcessBroker()

        def h(m: dict) -> None:
            pass

        b.subscribe("t", h)
        b.unsubscribe("t", h)
        b.unsubscribe("t", h)

        assert await b.publish("t", {}) == 0

    @pytest.mark.asyncio
    async def test_concurrent_publishes_serialize_safely(self):
        b = InProcessBroker()
        received: list[int] = []
        lock = asyncio.Lock()

        async def handler(m: dict) -> None:
            async with lock:
                received.append(m["seq"])

        b.subscribe("concurrent", handler)

        async def publish_range(start: int, count: int) -> None:
            for i in range(start, start + count):
                await b.publish("concurrent", {"seq": i})

        await asyncio.gather(
            publish_range(0, 30),
            publish_range(30, 30),
            publish_range(60, 30),
        )
        assert sorted(received) == list(range(90))

    @pytest.mark.asyncio
    async def test_handler_exception_excluded_from_delivered_count(self):
        b = InProcessBroker()
        good: list[int] = []

        def bad(_m: dict) -> None:
            raise ValueError("broker boom")

        b.subscribe("mixed", bad)
        b.subscribe("mixed", lambda m: good.append(1))
        delivered = await b.publish("mixed", {})
        assert delivered == 1
        assert good == [1]


# ── WriteQueue — backpressure deep ──────────────────────────────────────────


class TestWriteQueueBackpressureDeep:
    @pytest.mark.asyncio
    async def test_drop_oldest_evicts_in_fifo_order(self):
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.DROP_OLDEST)
        await q.put(Envelope(topic="1"))
        await q.put(Envelope(topic="2"))
        await q.put(Envelope(topic="3"))
        await q.put(Envelope(topic="4"))
        assert (await q.get()).topic == "3"
        assert (await q.get()).topic == "4"
        assert q.total_dropped == 2

    @pytest.mark.asyncio
    async def test_drop_oldest_on_maxsize_one(self):
        q = WriteQueue(maxsize=1, policy=OverflowPolicy.DROP_OLDEST)
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        assert (await q.get()).topic == "b"
        assert q.total_dropped == 1

    @pytest.mark.asyncio
    async def test_reject_dead_letter_semantics(self):
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.REJECT)
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        dead_letter_envelope = Envelope(topic="dead", payload={"reason": "overflow"})
        result = await q.put(dead_letter_envelope)
        assert result is False
        assert q.total_rejected == 1
        assert len(q) == 2
        assert [e.topic for e in [await q.get(), await q.get()]] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_reject_then_drain_then_accept(self):
        q = WriteQueue(maxsize=1, policy=OverflowPolicy.REJECT)
        await q.put(Envelope(topic="first"))
        assert await q.put(Envelope(topic="second")) is False
        await q.get()
        assert await q.put(Envelope(topic="third")) is True
        assert (await q.get()).topic == "third"

    @pytest.mark.asyncio
    async def test_drop_oldest_never_rejects(self):
        q = WriteQueue(maxsize=1, policy=OverflowPolicy.DROP_OLDEST)
        for i in range(50):
            result = await q.put(Envelope(topic=str(i)))
            assert result is True
        assert q.total_dropped == 49
        assert len(q) == 1


# ── WriteQueue — concurrent puts and get blocking ───────────────────────────


class TestWriteQueueConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_puts_drop_oldest(self):
        q = WriteQueue(maxsize=5, policy=OverflowPolicy.DROP_OLDEST)

        async def producer(start: int) -> None:
            for i in range(start, start + 20):
                await q.put(Envelope(topic=str(i)))

        await asyncio.gather(producer(0), producer(100), producer(200))
        assert q.total_offered == 60
        assert len(q) <= 5

    @pytest.mark.asyncio
    async def test_concurrent_puts_reject(self):
        q = WriteQueue(maxsize=3, policy=OverflowPolicy.REJECT)

        async def producer(start: int) -> None:
            for i in range(start, start + 3):
                await q.put(Envelope(topic=str(i)))

        await asyncio.gather(producer(0), producer(10), producer(20), producer(30))
        assert len(q) <= 3

    @pytest.mark.asyncio
    async def test_put_get_producer_consumer(self):
        q = WriteQueue(maxsize=20)
        consumed: list[str] = []

        async def consumer() -> None:
            for _ in range(20):
                env = await q.get()
                consumed.append(env.topic)

        async def producer() -> None:
            for i in range(20):
                await q.put(Envelope(topic=str(i)))

        await producer()
        await consumer()
        assert len(consumed) == 20
        assert consumed == [str(i) for i in range(20)]


# ── WriteQueue — snapshot, clear, and counters ──────────────────────────────


class TestWriteQueueCounters:
    @pytest.mark.asyncio
    async def test_snapshot_reflects_state(self):
        q = WriteQueue(maxsize=10, policy=OverflowPolicy.REJECT)
        await q.put(Envelope(topic="one"))
        await q.put(Envelope(topic="two"))
        snap = q.snapshot()
        assert snap["size"] == 2
        assert snap["total_offered"] == 2
        assert snap["policy"] == "reject"

    @pytest.mark.asyncio
    async def test_clear_resets_queue_but_preserves_counters(self):
        q = WriteQueue(maxsize=10)
        await q.put(Envelope(topic="a"))
        q.clear()
        assert len(q) == 0
        assert q.total_offered == 1

    @pytest.mark.asyncio
    async def test_clear_then_put_works(self):
        q = WriteQueue(maxsize=10)
        await q.put(Envelope(topic="before"))
        q.clear()
        await q.put(Envelope(topic="after"))
        assert len(q) == 1
        assert (await q.get()).topic == "after"


# ── InProcessBroker — Broker Protocol conformance ───────────────────────────


class TestBrokerProtocolConformance:
    def test_satisfies_runtime_checkable(self):
        from general_ludd.ipc.broker import Broker

        assert isinstance(InProcessBroker(), Broker)

    def test_message_type_compatibility(self):
        from general_ludd.ipc.broker import Message

        msg: Message = {"key1": "val1", "key2": 42, "key3": [1, 2, 3]}
        assert isinstance(msg, dict)
        assert msg["key2"] == 42
