"""E2E tests for the IPC layer: InProcessBroker + WriteQueue round-trips.

Exercises full pipelines — enqueue to queue, drain, publish to broker,
handler delivery — that go beyond the isolated unit tests. The unit tests
(broker_inprocess.py, ipc_write_queue.py) cover individual behaviors;
this file tests the two primitives wired together as they would run inside
the daemon's event loop.
"""
from __future__ import annotations

import asyncio
import queue as stdqueue
from typing import Any

import pytest

from general_ludd.ipc import (
    Envelope,
    InProcessBroker,
    OverflowPolicy,
    WriteQueue,
)


def _env(topic: str, seq: int) -> Envelope:
    return Envelope(topic=topic, payload={"seq": seq, "data": f"msg_{seq}"})


# ── Broker + Queue Pipeline ────────────────────────────────────────────


class TestBrokerQueuePipeline:
    """End-to-end: enqueue envelopes, drain them, publish via broker,
    verify correct handlers receive correct messages."""

    @pytest.mark.asyncio
    async def test_single_topic_queue_to_broker_delivery(self) -> None:
        broker = InProcessBroker()
        queue = WriteQueue(maxsize=10)
        received: list[dict[str, Any]] = []

        def handler(msg: dict[str, Any]) -> None:
            received.append(msg)

        broker.subscribe("pipe.t", handler)

        for i in range(3):
            await queue.put(_env("pipe.t", i))

        drained: list[Envelope] = []
        for _ in range(3):
            drained.append(await queue.get())

        for env in drained:
            await broker.publish(env.topic, env.payload)

        assert len(received) == 3
        assert [r["seq"] for r in received] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_multi_topic_queue_to_broker_routing(self) -> None:
        broker = InProcessBroker()
        queue = WriteQueue(maxsize=20)
        received_a: list[dict[str, Any]] = []
        received_b: list[dict[str, Any]] = []

        broker.subscribe("topic.a", lambda m: received_a.append(m))
        broker.subscribe("topic.b", lambda m: received_b.append(m))

        for i in range(5):
            await queue.put(_env("topic.a", i))
        for i in range(3):
            await queue.put(_env("topic.b", i + 100))

        while len(queue) > 0:
            env = await queue.get()
            await broker.publish(env.topic, env.payload)

        assert len(received_a) == 5
        assert [r["seq"] for r in received_a] == list(range(5))
        assert len(received_b) == 3
        assert [r["seq"] for r in received_b] == [100, 101, 102]

    @pytest.mark.asyncio
    async def test_async_handler_in_full_pipeline(self) -> None:
        broker = InProcessBroker()
        queue = WriteQueue(maxsize=10)
        received: list[dict[str, Any]] = []

        async def async_handler(msg: dict[str, Any]) -> None:
            await asyncio.sleep(0)
            received.append(msg)

        broker.subscribe("async.pipe", async_handler)
        await queue.put(_env("async.pipe", 42))

        env = await queue.get()
        delivered = await broker.publish(env.topic, env.payload)

        assert delivered == 1
        assert len(received) == 1
        assert received[0]["seq"] == 42

    @pytest.mark.asyncio
    async def test_failing_handler_does_not_block_pipeline_delivery(self) -> None:
        broker = InProcessBroker()
        queue = WriteQueue(maxsize=10)
        received: list[dict[str, Any]] = []

        def bad(msg: dict[str, Any]) -> None:
            raise RuntimeError("handler failure")

        def good(msg: dict[str, Any]) -> None:
            received.append(msg)

        broker.subscribe("resilient", bad)
        broker.subscribe("resilient", good)

        for i in range(3):
            await queue.put(_env("resilient", i))

        for _ in range(3):
            env = await queue.get()
            await broker.publish(env.topic, env.payload)

        assert len(received) == 3
        assert [r["seq"] for r in received] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_pipeline_with_concurrent_producers(self) -> None:
        broker = InProcessBroker()
        queue = WriteQueue(maxsize=20, policy=OverflowPolicy.DROP_OLDEST)
        received: list[dict[str, Any]] = []

        def handler(msg: dict[str, Any]) -> None:
            received.append(msg)

        broker.subscribe("conc", handler)

        async def producer(start: int) -> None:
            for i in range(4):
                await queue.put(_env("conc", start + i))

        await asyncio.gather(
            producer(0),
            producer(100),
            producer(200),
        )

        while len(queue) > 0:
            env = await queue.get()
            await broker.publish(env.topic, env.payload)

        assert len(received) == 12
        seqs = sorted(r["seq"] for r in received)
        assert len(set(seqs)) == 12


# ── Queue Overflow with Broker Delivery ────────────────────────────────


class TestQueueOverflowWithBroker:
    """Full round-trips through overflow boundaries, verifying correct
    deliveries land at the broker."""

    @pytest.mark.asyncio
    async def test_drop_oldest_remaining_delivered_to_broker(self) -> None:
        broker = InProcessBroker()
        queue = WriteQueue(maxsize=3, policy=OverflowPolicy.DROP_OLDEST)
        received: list[dict[str, Any]] = []

        broker.subscribe("drop", lambda m: received.append(m))

        for i in range(7):
            await queue.put(_env("drop", i))

        assert queue.total_dropped == 4

        while len(queue) > 0:
            env = await queue.get()
            await broker.publish(env.topic, env.payload)

        assert len(received) == 3
        assert [r["seq"] for r in received] == [4, 5, 6]

    @pytest.mark.asyncio
    async def test_reject_backpressure_then_retry_after_drain(self) -> None:
        broker = InProcessBroker()
        queue = WriteQueue(maxsize=2, policy=OverflowPolicy.REJECT)
        received: list[dict[str, Any]] = []

        broker.subscribe("reject", lambda m: received.append(m))

        for i in range(2):
            assert await queue.put(_env("reject", i)) is True

        rejected = await queue.put(_env("reject", 99))
        assert rejected is False
        assert queue.total_rejected == 1

        env = await queue.get()
        await broker.publish(env.topic, env.payload)
        await asyncio.sleep(0)

        success = await queue.put(_env("reject", 99))
        assert success is True

        while len(queue) > 0:
            env = await queue.get()
            await broker.publish(env.topic, env.payload)

        seqs = sorted(r["seq"] for r in received)
        assert seqs == [0, 1, 99]


# ── Broker Round-Trip Behaviors ────────────────────────────────────────


class TestBrokerRoundTrip:
    """Broker-level round-trips that go beyond the unit tests' per-method checks."""

    @pytest.mark.asyncio
    async def test_handler_mutation_during_dispatch_is_safe(self) -> None:
        broker = InProcessBroker()
        received: list[str] = []

        def self_removing(msg: dict[str, Any]) -> None:
            received.append("rm")
            broker.unsubscribe("mut", self_removing)

        def stable(msg: dict[str, Any]) -> None:
            received.append("st")

        broker.subscribe("mut", self_removing)
        broker.subscribe("mut", stable)

        await broker.publish("mut", {})
        assert received == ["rm", "st"]

        await broker.publish("mut", {})
        assert received == ["rm", "st", "st"]

    @pytest.mark.asyncio
    async def test_topic_isolation_multi_publish(self) -> None:
        broker = InProcessBroker()
        r_a: list[int] = []
        r_b: list[int] = []

        broker.subscribe("iso.a", lambda m: r_a.append(m["seq"]))
        broker.subscribe("iso.b", lambda m: r_b.append(m["seq"]))

        await broker.publish("iso.a", {"seq": 1})
        await broker.publish("iso.b", {"seq": 50})
        await broker.publish("iso.a", {"seq": 2})

        assert r_a == [1, 2]
        assert r_b == [50]

    @pytest.mark.asyncio
    async def test_mixed_sync_async_handlers_same_topic(self) -> None:
        broker = InProcessBroker()
        sync_received: list[str] = []
        async_received: list[str] = []

        def sync_h(msg: dict[str, Any]) -> None:
            sync_received.append(msg["tag"])

        async def async_h(msg: dict[str, Any]) -> None:
            await asyncio.sleep(0)
            async_received.append(msg["tag"])

        broker.subscribe("mixed", sync_h)
        broker.subscribe("mixed", async_h)

        delivered = await broker.publish("mixed", {"tag": "hello"})
        assert delivered == 2
        assert sync_received == ["hello"]
        assert async_received == ["hello"]

    @pytest.mark.asyncio
    async def test_clear_and_resubscribe_roundtrip(self) -> None:
        broker = InProcessBroker()
        received_old: list[int] = []
        received_new: list[int] = []

        broker.subscribe("rebuild", lambda m: received_old.append(m["seq"]))
        await broker.publish("rebuild", {"seq": 10})
        assert received_old == [10]

        broker.clear()
        await broker.publish("rebuild", {"seq": 20})
        assert received_old == [10]

        broker.subscribe("rebuild", lambda m: received_new.append(m["seq"]))
        await broker.publish("rebuild", {"seq": 30})
        assert received_new == [30]


# ── Envelope Integrity Through Pipeline ────────────────────────────────


class TestEnvelopeIntegrity:
    """Envelope topic + payload is preserved end-to-end through
    queue → broker → handler."""

    @pytest.mark.asyncio
    async def test_envelope_payload_preserved_full_pipeline(self) -> None:
        broker = InProcessBroker()
        queue = WriteQueue(maxsize=10)
        captured: dict[str, dict[str, Any]] = {}

        def handler(msg: dict[str, Any]) -> None:
            captured[msg.get("id", "unknown")] = msg

        broker.subscribe("e2e", handler)

        envs = [
            Envelope(topic="e2e", payload={"id": "alpha", "count": 1, "nested": {"x": True}}),
            Envelope(topic="e2e", payload={"id": "beta", "count": 2, "nested": {"x": False}}),
        ]
        for e in envs:
            await queue.put(e)

        for _ in range(2):
            env = await queue.get()
            await broker.publish(env.topic, env.payload)

        assert captured["alpha"] == {"id": "alpha", "count": 1, "nested": {"x": True}}
        assert captured["beta"] == {"id": "beta", "count": 2, "nested": {"x": False}}

    @pytest.mark.asyncio
    async def test_snapshot_accuracy_during_pipeline_stages(self) -> None:
        queue = WriteQueue(maxsize=5, policy=OverflowPolicy.DROP_OLDEST)

        snap0 = queue.snapshot()
        assert snap0["size"] == 0

        for i in range(3):
            await queue.put(_env("s", i))

        snap1 = queue.snapshot()
        assert snap1["size"] == 3
        assert snap1["total_offered"] == 3
        assert snap1["total_dropped"] == 0

        await queue.get()
        snap2 = queue.snapshot()
        assert snap2["size"] == 2

        for _ in range(4):
            await queue.put(_env("s", 99))

        snap3 = queue.snapshot()
        assert snap3["size"] == 5
        assert snap3["total_dropped"] == 1

    @pytest.mark.asyncio
    async def test_get_nowait_full_cycle_with_broker(self) -> None:
        broker = InProcessBroker()
        queue = WriteQueue(maxsize=10)
        received: list[dict[str, Any]] = []

        broker.subscribe("nowait", lambda m: received.append(m))

        await queue.put(_env("nowait", 1))
        await queue.put(_env("nowait", 2))

        env = queue.get_nowait()
        await broker.publish(env.topic, env.payload)
        assert len(received) == 1

        env = queue.get_nowait()
        await broker.publish(env.topic, env.payload)
        assert len(received) == 2

        with pytest.raises(stdqueue.Empty):
            queue.get_nowait()
