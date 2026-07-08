"""Unit tests for the IPC WriteQueue (Phase 1 of the gunicorn multi-worker architecture).

Mirrors the bounding/overflow coverage of ``test_receiver_buffer.py`` but for
the async :class:`WriteQueue` used on the egress (publish) side. The overflow
contract is the load-bearing piece: a stuck consumer must never grow the
daemon's memory without limit, whether the queue drops oldest or rejects.
"""

from __future__ import annotations

import asyncio
import queue as stdqueue
import time

import pytest

from general_ludd.ipc import (
    DEFAULT_WRITE_QUEUE_MAXSIZE,
    Envelope,
    OverflowPolicy,
    WriteQueue,
)


def _env(i: int) -> Envelope:
    return Envelope(topic="t", payload={"seq": i})


class TestBounding:
    @pytest.mark.asyncio
    async def test_drop_oldest_evicts_when_full(self) -> None:
        q = WriteQueue(maxsize=3, policy=OverflowPolicy.DROP_OLDEST)
        for i in range(5):
            assert await q.put(_env(i)) is True
        assert len(q) == 3
        # Oldest two (0,1) evicted; FIFO returns 2,3,4.
        out = [await q.get() for _ in range(3)]
        assert [e.payload["seq"] for e in out] == [2, 3, 4]
        assert q.total_dropped == 2
        assert q.total_offered == 5

    @pytest.mark.asyncio
    async def test_reject_refuses_when_full(self) -> None:
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.REJECT)
        assert await q.put(_env(0)) is True
        assert await q.put(_env(1)) is True
        assert await q.put(_env(2)) is False  # full -> rejected
        assert len(q) == 2
        assert q.total_rejected == 1
        out = [await q.get() for _ in range(2)]
        assert [e.payload["seq"] for e in out] == [0, 1]

    @pytest.mark.asyncio
    async def test_is_full(self) -> None:
        q = WriteQueue(maxsize=1, policy=OverflowPolicy.REJECT)
        assert q.is_full() is False
        await q.put(_env(0))
        assert q.is_full() is True

    def test_maxsize_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            WriteQueue(maxsize=0)

    def test_default_maxsize(self) -> None:
        q = WriteQueue()
        assert q.maxsize == DEFAULT_WRITE_QUEUE_MAXSIZE


class TestGetPut:
    @pytest.mark.asyncio
    async def test_fifo_order(self) -> None:
        q = WriteQueue(maxsize=10)
        for i in range(5):
            await q.put(_env(i))
        out = [await q.get() for _ in range(5)]
        assert [e.payload["seq"] for e in out] == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_get_blocks_until_put(self) -> None:
        q = WriteQueue(maxsize=2)

        async def producer() -> None:
            await asyncio.sleep(0.01)
            await q.put(_env(99))

        task = asyncio.create_task(producer())
        envelope = await q.get()
        await task
        assert envelope.payload["seq"] == 99

    @pytest.mark.asyncio
    async def test_clear_empties_queue(self) -> None:
        q = WriteQueue(maxsize=5)
        await q.put(_env(0))
        await q.put(_env(1))
        q.clear()
        assert len(q) == 0
        snap = q.snapshot()
        assert snap["size"] == 0


class TestGetNowait:
    """B3.1.3 Slice 5: synchronous non-blocking dequeue used by the drain hook."""

    @pytest.mark.asyncio
    async def test_get_nowait_returns_envelope(self) -> None:
        q = WriteQueue(maxsize=5)
        e1 = _env(1)
        await q.put(e1)
        assert q.get_nowait() is e1
        assert len(q) == 0

    @pytest.mark.asyncio
    async def test_get_nowait_raises_when_empty(self) -> None:
        q = WriteQueue(maxsize=5)
        with pytest.raises(stdqueue.Empty):
            q.get_nowait()

    @pytest.mark.asyncio
    async def test_get_nowait_does_not_block(self) -> None:
        q = WriteQueue(maxsize=5)
        start = time.monotonic()
        with pytest.raises(stdqueue.Empty):
            q.get_nowait()
        elapsed_ms = (time.monotonic() - start) * 1000
        # Non-blocking: an empty queue must return (raise) near-instantly.
        assert elapsed_ms < 10.0
