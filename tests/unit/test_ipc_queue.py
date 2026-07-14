"""Structural tests for ipc/queue.py — WriteQueue, Envelope, OverflowPolicy."""

from __future__ import annotations

import queue as _stdqueue

import pytest

from general_ludd.ipc.queue import (
    DEFAULT_WRITE_QUEUE_MAXSIZE,
    Envelope,
    OverflowPolicy,
    WriteQueue,
)


class TestOverflowPolicy:
    def test_members(self):
        assert OverflowPolicy.DROP_OLDEST.value == "drop_oldest"
        assert OverflowPolicy.REJECT.value == "reject"

    def test_default_maxsize(self):
        assert DEFAULT_WRITE_QUEUE_MAXSIZE == 1000


class TestEnvelope:
    def test_creation(self):
        env = Envelope(topic="test.topic")
        assert env.topic == "test.topic"
        assert env.payload == {}

    def test_with_payload(self):
        env = Envelope(topic="test", payload={"key": "val"})
        assert env.payload == {"key": "val"}


class TestWriteQueueInit:
    def test_default_maxsize(self):
        q = WriteQueue()
        assert q.maxsize == 1000

    def test_custom_maxsize(self):
        q = WriteQueue(maxsize=50)
        assert q.maxsize == 50

    def test_default_policy(self):
        q = WriteQueue()
        assert q.policy == OverflowPolicy.DROP_OLDEST

    def test_custom_policy(self):
        q = WriteQueue(policy=OverflowPolicy.REJECT)
        assert q.policy == OverflowPolicy.REJECT

    def test_negative_maxsize_raises(self):
        with pytest.raises(ValueError, match="positive"):
            WriteQueue(maxsize=-1)

    def test_zero_maxsize_raises(self):
        with pytest.raises(ValueError, match="positive"):
            WriteQueue(maxsize=0)


class TestWriteQueueStats:
    def test_initially_empty(self):
        q = WriteQueue()
        assert len(q) == 0
        assert not q.is_full()

    def test_total_offered_zero_initially(self):
        q = WriteQueue()
        assert q.total_offered == 0

    def test_total_dropped_zero_initially(self):
        q = WriteQueue()
        assert q.total_dropped == 0

    def test_total_rejected_zero_initially(self):
        q = WriteQueue()
        assert q.total_rejected == 0


class TestWriteQueuePut:
    @pytest.mark.asyncio
    async def test_put_increments_offered(self):
        q = WriteQueue(maxsize=10)
        await q.put(Envelope(topic="t"))
        assert q.total_offered == 1

    @pytest.mark.asyncio
    async def test_put_increases_length(self):
        q = WriteQueue(maxsize=10)
        await q.put(Envelope(topic="t"))
        assert len(q) == 1

    @pytest.mark.asyncio
    async def test_put_returns_true_drop_oldest(self):
        q = WriteQueue(maxsize=10, policy=OverflowPolicy.DROP_OLDEST)
        result = await q.put(Envelope(topic="t"))
        assert result is True


class TestWriteQueueGet:
    @pytest.mark.asyncio
    async def test_get_returns_put_envelope(self):
        q = WriteQueue(maxsize=10)
        env = Envelope(topic="test", payload={"x": 1})
        await q.put(env)
        result = await q.get()
        assert result.topic == "test"
        assert result.payload == {"x": 1}

    @pytest.mark.asyncio
    async def test_get_removes_from_queue(self):
        q = WriteQueue(maxsize=10)
        await q.put(Envelope(topic="t"))
        await q.get()
        assert len(q) == 0

    @pytest.mark.asyncio
    async def test_get_fifo_order(self):
        q = WriteQueue(maxsize=10)
        await q.put(Envelope(topic="first"))
        await q.put(Envelope(topic="second"))
        assert (await q.get()).topic == "first"
        assert (await q.get()).topic == "second"


class TestGetNowait:
    def test_empty_raises_queue_empty(self):
        q = WriteQueue()
        with pytest.raises(_stdqueue.Empty):
            q.get_nowait()

    @pytest.mark.asyncio
    async def test_returns_envelope(self):
        q = WriteQueue()
        await q.put(Envelope(topic="t"))
        result = q.get_nowait()
        assert result.topic == "t"

    @pytest.mark.asyncio
    async def test_decreases_length(self):
        q = WriteQueue()
        await q.put(Envelope(topic="t"))
        q.get_nowait()
        assert len(q) == 0


class TestWriteQueueFull:
    @pytest.mark.asyncio
    async def test_is_full_true(self):
        q = WriteQueue(maxsize=2)
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        assert q.is_full()

    @pytest.mark.asyncio
    async def test_is_full_false_below_max(self):
        q = WriteQueue(maxsize=5)
        await q.put(Envelope(topic="a"))
        assert not q.is_full()

    @pytest.mark.asyncio
    async def test_drop_oldest_never_rejects(self):
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.DROP_OLDEST)
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        result = await q.put(Envelope(topic="c"))
        assert result is True
        assert q.total_dropped == 1

    @pytest.mark.asyncio
    async def test_reject_refuses_on_full(self):
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.REJECT)
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        result = await q.put(Envelope(topic="c"))
        assert result is False
        assert q.total_rejected == 1


class TestWriteQueueDropOldest:
    @pytest.mark.asyncio
    async def test_evicts_oldest(self):
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.DROP_OLDEST)
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        await q.put(Envelope(topic="c"))
        assert (await q.get()).topic == "b"
        assert (await q.get()).topic == "c"
        assert len(q) == 0

    @pytest.mark.asyncio
    async def test_counter_increments_on_eviction(self):
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.DROP_OLDEST)
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        assert q.total_dropped == 0
        await q.put(Envelope(topic="c"))
        assert q.total_dropped == 1


class TestWriteQueueReject:
    @pytest.mark.asyncio
    async def test_reject_preserves_existing(self):
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.REJECT)
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        result = await q.put(Envelope(topic="c"))
        assert result is False
        assert len(q) == 2
        assert (await q.get()).topic == "a"
        assert (await q.get()).topic == "b"


class TestWriteQueueSnapshot:
    def test_returns_initial_state(self):
        q = WriteQueue(maxsize=50, policy=OverflowPolicy.REJECT)
        snap = q.snapshot()
        assert snap["size"] == 0
        assert snap["maxsize"] == 50
        assert snap["policy"] == "reject"
        assert snap["total_offered"] == 0

    @pytest.mark.asyncio
    async def test_reflects_put(self):
        q = WriteQueue(maxsize=10)
        await q.put(Envelope(topic="t"))
        snap = q.snapshot()
        assert snap["size"] == 1
        assert snap["total_offered"] == 1


class TestWriteQueueClear:
    @pytest.mark.asyncio
    async def test_clears_envelopes(self):
        q = WriteQueue()
        await q.put(Envelope(topic="a"))
        await q.put(Envelope(topic="b"))
        q.clear()
        assert len(q) == 0
