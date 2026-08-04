"""Deep pubsub/broadcast tests: publish/subscribe, unsub, wildcard topics,
message ordering, fan-out, persistence, replay.

Covers:
  - EventBus — fan-out, ordering, wildcard replay, persistence, concurrent publish
  - WorkerBroadcaster — fan-out, register/unregister, heartbeat, stale cleanup
  - InProcessBroker — deep fan-out, ordering, handler lifecycle
  - WriteQueue — persistence-as-queue semantics
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event, EventType
from general_ludd.ipc.broker import InProcessBroker
from general_ludd.ipc.queue import Envelope, OverflowPolicy, WriteQueue
from general_ludd.reload.worker_broadcast import (
    BroadcastResult,
    WorkerBroadcaster,
    WorkerInfo,
)

# ── EventBus — fan-out and message ordering ──────────────────────────────────


class TestEventBusFanOut:
    def test_fan_out_to_many_subscribers_on_one_topic(self):
        bus = EventBus()
        N = 200
        received: list[list[str]] = [[] for _ in range(N)]

        for i in range(N):

            def make_handler(idx: int):
                def h(e: Event) -> None:
                    received[idx].append(e.payload["marker"])

                return h

            bus.subscribe("fanout", make_handler(i))

        bus.publish(Event(type="fanout", payload={"marker": "x"}))
        assert all(len(r) == 1 for r in received)
        assert all(r[0] == "x" for r in received)

    def test_fan_out_across_different_topics_same_handler(self):
        bus = EventBus()
        seen: list[str] = []

        def catcher(e: Event) -> None:
            seen.append(e.type if isinstance(e.type, str) else e.type.value)

        bus.subscribe("a", catcher)
        bus.subscribe("b", catcher)
        bus.subscribe("c", catcher)
        bus.publish(Event(type="a"))
        bus.publish(Event(type="b"))
        bus.publish(Event(type="c"))
        assert seen == ["a", "b", "c"]

    def test_messages_delivered_in_publish_order(self):
        bus = EventBus()
        order: list[int] = []

        bus.subscribe("seq", lambda e: order.append(e.payload["n"]))
        for i in range(100):
            bus.publish(Event(type="seq", payload={"n": i}))
        assert order == list(range(100))

    def test_ordering_preserved_under_concurrent_publishes(self):
        bus = EventBus()
        delivered: list[int] = []
        lock = threading.Lock()

        def handler(e: Event) -> None:
            with lock:
                delivered.append(e.payload["seq"])

        bus.subscribe("concurrent", handler)

        def publish_range(start: int, count: int) -> None:
            for i in range(start, start + count):
                bus.publish(Event(type="concurrent", payload={"seq": i}))

        threads = [
            threading.Thread(target=publish_range, args=(0, 40)),
            threading.Thread(target=publish_range, args=(40, 40)),
            threading.Thread(target=publish_range, args=(80, 40)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(delivered) == 120
        assert sorted(delivered) == list(range(120))


# ── EventBus — wildcard topics ───────────────────────────────────────────────


class TestEventBusWildcardTopics:
    def test_wildcard_receives_every_event_type(self):
        bus = EventBus(history_size=20)
        wild: list[str] = []
        bus.subscribe("*", lambda e: wild.append(e.type if isinstance(e.type, str) else e.type.value))
        for et in list(EventType):
            bus.publish(Event(type=et, payload={}))
        assert len(wild) == len(EventType)

    def test_wildcard_and_exact_do_not_duplicate_delivery_count(self):
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("t", lambda e: seen.append("exact"))
        bus.subscribe("*", lambda e: seen.append("wild"))
        delivered = bus.publish(Event(type="t"))
        assert delivered == 2
        assert seen == ["exact", "wild"]

    def test_wildcard_unsubscribe_removes_only_wildcard(self):
        bus = EventBus()
        exact_hits: list[str] = []
        wild_hits: list[str] = []

        bus.subscribe("t", lambda e: exact_hits.append("exact"))
        sid = bus.subscribe("*", lambda e: wild_hits.append("wild"))
        bus.unsubscribe(sid)
        bus.publish(Event(type="t"))
        assert exact_hits == ["exact"]
        assert wild_hits == []

    def test_wildcard_subscriber_still_receives_unmatched_event(self):
        bus = EventBus()
        wild: list[str] = []
        exact: list[str] = []

        bus.subscribe("*", lambda e: wild.append(e.type if isinstance(e.type, str) else e.type.value))
        bus.subscribe("only_this", lambda e: exact.append("hit"))
        bus.publish(Event(type="unmatched_topic"))
        assert "unmatched_topic" in wild
        assert exact == []


# ── EventBus — persistence and replay via history ────────────────────────────


class TestEventBusPersistenceReplay:
    def test_history_ring_buffer_persists_across_publishes(self):
        bus = EventBus(history_size=50)
        for i in range(100):
            bus.publish(Event(type="t", payload={"n": i}))
        hist = bus.get_history()
        assert len(hist) == 50
        assert hist[0].payload["n"] == 50
        assert hist[-1].payload["n"] == 99

    def test_replay_from_history_after_late_subscription(self):
        bus = EventBus(history_size=10)
        for i in range(5):
            bus.publish(Event(type="t", payload={"n": i}))

        replayed: list[int] = []
        for e in bus.get_history():
            replayed.append(e.payload["n"])
        assert replayed == [0, 1, 2, 3, 4]

    def test_history_persists_after_subscriber_errors(self):
        bus = EventBus(history_size=5)

        def failing(_e: Event) -> None:
            raise ValueError("boom")

        bus.subscribe("t", failing)
        bus.publish(Event(type="t", payload={"k": "v"}))
        assert len(bus.get_history()) == 1
        assert bus.get_history()[0].payload["k"] == "v"

    def test_history_size_zero_no_persistence(self):
        bus = EventBus(history_size=0)
        bus.publish(Event(type="t"))
        assert bus.get_history() == []

    def test_history_replay_from_multiple_types(self):
        bus = EventBus(history_size=20)
        types = [EventType.MODEL_ADDED, EventType.WORKER_PING, "custom.x", EventType.CONFIG_RELOADED, "custom.y"]
        for t in types:
            bus.publish(Event(type=t))
        hist = bus.get_history()
        hist_types = [e.type if isinstance(e.type, str) else e.type.value for e in hist]
        assert "model_added" in hist_types
        assert "worker_ping" in hist_types
        assert "custom.x" in hist_types
        assert "config_reloaded" in hist_types
        assert "custom.y" in hist_types


# ── EventBus — unsubscribe lifecycle ─────────────────────────────────────────


class TestEventBusUnsubscribeLifecycle:
    def test_unsubscribe_then_resubscribe_new_id(self):
        bus = EventBus()
        sid1 = bus.subscribe("t", lambda e: None)
        bus.unsubscribe(sid1)
        sid2 = bus.subscribe("t", lambda e: None)
        assert sid1 != sid2
        assert "sub-0" not in str(bus._subscribers.get("t", []))

    def test_unsubscribe_does_not_affect_other_topics(self):
        bus = EventBus()
        hits_a: list[str] = []
        hits_b: list[str] = []

        sid_a = bus.subscribe("a", lambda e: hits_a.append("a"))
        bus.subscribe("b", lambda e: hits_b.append("b"))
        bus.unsubscribe(sid_a)

        bus.publish(Event(type="a"))
        bus.publish(Event(type="b"))
        assert hits_a == []
        assert hits_b == ["b"]

    def test_unsubscribe_nonexistent_id_no_error(self):
        bus = EventBus()
        bus.unsubscribe("nonexistent")
        bus.subscribe("t", lambda e: None)
        bus.publish(Event(type="t"))
        # Should not raise


# ── WorkerBroadcaster — fan-out ──────────────────────────────────────────────


class TestWorkerBroadcasterFanOut:
    def test_fan_out_to_multiple_workers(self):
        b = WorkerBroadcaster()
        for i in range(5):
            b.register(
                WorkerInfo(
                    worker_id=f"w{i}",
                    address=f"https://worker-{i}.internal:8001",
                )
            )
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = b.broadcast_reload("ALL")
        assert len(results) == 5
        assert all(r.success for r in results)
        assert mock_post.call_count == 5

    def test_fan_out_results_reflect_per_worker_outcome(self):
        b = WorkerBroadcaster()
        b.register(WorkerInfo(worker_id="ok", address="https://ok.internal:8001"))
        b.register(WorkerInfo(worker_id="fail", address="https://fail.internal:8001"))
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.side_effect = [
                MagicMock(status_code=200),
                MagicMock(status_code=500),
            ]
            results = b.broadcast_reload("ALL")
        assert len(results) == 2
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].error == "HTTP 500"

    def test_broadcast_preserves_registration_order(self):
        b = WorkerBroadcaster()
        ids = ["z", "a", "m", "b"]
        for wid in ids:
            b.register(WorkerInfo(worker_id=wid, address=f"https://{wid}.internal:8001"))
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            results = b.broadcast_reload("ALL")
        assert [r.worker_id for r in results] == ids


# ── WorkerBroadcaster — register/unregister lifecycle ────────────────────────


class TestWorkerBroadcasterLifecycle:
    def test_register_then_unregister(self):
        b = WorkerBroadcaster()
        b.register(WorkerInfo(worker_id="w1", address="https://w1.internal:8001"))
        assert len(b.list_workers()) == 1
        b.unregister("w1")
        assert len(b.list_workers()) == 0

    def test_unregister_nonexistent_no_error(self):
        b = WorkerBroadcaster()
        b.unregister("ghost")

    def test_register_same_id_overwrites(self):
        b = WorkerBroadcaster()
        b.register(WorkerInfo(worker_id="w1", address="https://old.internal:8001"))
        b.register(WorkerInfo(worker_id="w1", address="https://new.internal:8001"))
        workers = b.list_workers()
        assert len(workers) == 1
        assert workers[0].address == "https://new.internal:8001"

    def test_heartbeat_updates_last_seen(self):
        b = WorkerBroadcaster()
        b.register(WorkerInfo(worker_id="w1", address="https://w1.internal:8001"))
        old_seen = b.list_workers()[0].last_seen
        time.sleep(0.01)
        b.heartbeat("w1")
        new_seen = b.list_workers()[0].last_seen
        assert new_seen > old_seen

    def test_heartbeat_nonexistent_worker_no_error(self):
        b = WorkerBroadcaster()
        b.heartbeat("ghost")

    def test_cleanup_stale_removes_expired_workers(self):
        b = WorkerBroadcaster(stale_threshold_seconds=0.001)
        b.register(WorkerInfo(worker_id="w1", address="https://w1.internal:8001"))
        time.sleep(0.01)
        b.cleanup_stale()
        assert len(b.list_workers()) == 0

    def test_cleanup_stale_preserves_fresh_workers(self):
        b = WorkerBroadcaster(stale_threshold_seconds=5.0)
        b.register(WorkerInfo(worker_id="w1", address="https://w1.internal:8001"))
        b.register(WorkerInfo(worker_id="w2", address="https://w2.internal:8001"))
        b.cleanup_stale()
        assert len(b.list_workers()) == 2

    def test_broadcast_model_update_fan_out_with_results(self):
        b = WorkerBroadcaster()
        b.register(WorkerInfo(worker_id="w1", address="https://w1.internal:8001"))
        b.register(WorkerInfo(worker_id="w2", address="https://w2.internal:8001"))
        with patch("general_ludd.reload.worker_broadcast.httpx.post") as mock_post:
            mock_post.side_effect = [
                MagicMock(status_code=200),
                Exception("network error"),
            ]
            results = b.broadcast_model_update("add", "m1", {"p": "x"})
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False
        assert results[1].error is not None
        assert "network error" in results[1].error


# ── WorkerBroadcaster — BroadcastResult dataclass ────────────────────────────


class TestBroadcastResult:
    def test_defaults(self):
        r = BroadcastResult(worker_id="w1", success=True)
        assert r.worker_id == "w1"
        assert r.success is True
        assert r.error is None

    def test_with_error(self):
        r = BroadcastResult(worker_id="w1", success=False, error="timeout")
        assert r.error == "timeout"


# ── InProcessBroker — deep fan-out and ordering ──────────────────────────────


class TestInProcessBrokerDeep:
    @pytest.mark.asyncio
    async def test_fan_out_to_many_handlers(self):
        b = InProcessBroker()
        N = 100
        received: list[list[int]] = [[] for _ in range(N)]

        for i in range(N):

            def make_handler(idx: int):
                def h(m: dict) -> None:
                    received[idx].append(m["n"])

                return h

            b.subscribe("fan", make_handler(i))

        await b.publish("fan", {"n": 42})
        assert all(len(r) == 1 for r in received)
        assert all(r[0] == 42 for r in received)

    @pytest.mark.asyncio
    async def test_ordering_across_multiple_publishes(self):
        b = InProcessBroker()
        order: list[int] = []

        b.subscribe("seq", lambda m: order.append(m["n"]))
        for i in range(50):
            await b.publish("seq", {"n": i})
        assert order == list(range(50))

    @pytest.mark.asyncio
    async def test_clear_drops_all_subscribers(self):
        b = InProcessBroker()
        b.subscribe("t", lambda m: None)
        b.clear()
        assert await b.publish("t", {}) == 0

    @pytest.mark.asyncio
    async def test_publish_to_empty_topic_returns_zero(self):
        b = InProcessBroker()
        assert await b.publish("nonexistent", {}) == 0

    @pytest.mark.asyncio
    async def test_async_handler_awaited_in_publish(self):
        b = InProcessBroker()
        done: list[int] = []

        def handler(m: dict) -> object:
            async def inner():
                await asyncio.sleep(0)
                done.append(m["n"])

            return inner()

        b.subscribe("async", handler)
        await b.publish("async", {"n": 99})
        assert done == [99]


# ── WriteQueue — persistence-as-queue ────────────────────────────────────────


class TestWriteQueuePersistence:
    @pytest.mark.asyncio
    async def test_fifo_ordering_preserved(self):
        q = WriteQueue(maxsize=20)
        for i in range(10):
            await q.put(Envelope(topic="t", payload={"n": i}))
        items = []
        for _ in range(10):
            items.append((await q.get()).payload["n"])
        assert items == list(range(10))

    @pytest.mark.asyncio
    async def test_snapshot_reflects_all_counters(self):
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.DROP_OLDEST)
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        await q.put(Envelope(topic="c"))
        snap = q.snapshot()
        assert snap["total_offered"] == 3
        assert snap["total_dropped"] == 1
        assert snap["size"] == 2

    @pytest.mark.asyncio
    async def test_envelope_carries_topic_and_payload(self):
        q = WriteQueue(maxsize=10)
        await q.put(Envelope(topic="alerts", payload={"severity": "high"}))
        env = await q.get()
        assert env.topic == "alerts"
        assert env.payload == {"severity": "high"}

    @pytest.mark.asyncio
    async def test_empty_queue_get_blocks(self):
        q = WriteQueue(maxsize=10)
        done = False

        async def delayed_put() -> None:
            nonlocal done
            await asyncio.sleep(0.02)
            await q.put(Envelope(topic="late"))
            done = True

        async def consumer() -> Envelope:
            return await q.get()

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)
        assert not task.done()
        await delayed_put()
        result = await asyncio.wait_for(task, timeout=0.5)
        assert result.topic == "late"
        assert done
