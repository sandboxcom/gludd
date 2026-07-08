"""Unit tests for the writer bridge (B3.1.3 Slice 2).

The :class:`QueueWriteSession` buffers writes inside HTTP workers and flushes
them to the :class:`WriteQueue` on commit, so routers do not need to know
whether they are running in inline (single-process) or queued (multi-worker)
mode. The :func:`enqueue_or_commit` helper branches on
``app.state._write_queue`` to pick the path — letting the SAME router code
run in both modes until a later slice wires it in.

Coverage matrix (7 tests, mirroring the spec in STABILIZATION_PLAN.md WP-B1):

* put() captures an :class:`Envelope` with the session topic + supplied payload
* commit() flushes pending envelopes; rollback() drops them
* enqueue_or_commit() enqueues when ``app.state._write_queue`` is set
* enqueue_or_commit() falls back to an inline commit callback when it is None
* DROP_OLDEST overflow on commit evicts the oldest queued envelope
* REJECT overflow on commit raises :class:`QueueFullError`
* enqueue_or_commit() returns HTTP 202 when enqueued vs 200 when inline
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from general_ludd.ipc import Envelope, OverflowPolicy, WriteQueue
from general_ludd.writer.bridge import (
    HTTP_ENQUEUED,
    HTTP_INLINE_COMMIT,
    QueueFullError,
    QueueWriteSession,
    enqueue_or_commit,
)


class TestQueueWriteSessionPut:
    @pytest.mark.asyncio
    async def test_put_enqueues_envelope(self) -> None:
        """put() buffers an Envelope carrying the session topic + payload."""
        q = WriteQueue(maxsize=10)
        session = QueueWriteSession(topic="todo.create", queue=q)
        payload = {"title": "ship beta.3", "priority": 1}

        await session.put(payload)

        # The pending buffer holds exactly one envelope...
        assert len(session.pending) == 1
        env = session.pending[0]
        # ...which carries the session's topic and the supplied payload verbatim.
        assert env.topic == "todo.create"
        assert env.payload == payload
        # Nothing has reached the underlying queue yet (put only buffers).
        assert len(q) == 0


class TestQueueWriteSessionCommitRollback:
    @pytest.mark.asyncio
    async def test_commit_flushes_pending_to_queue(self) -> None:
        """commit() drains the pending buffer onto the WriteQueue in FIFO order."""
        q = WriteQueue(maxsize=10)
        session = QueueWriteSession(topic="todo.create", queue=q)
        await session.put({"seq": 0})
        await session.put({"seq": 1})

        assert len(session.pending) == 2
        assert len(q) == 0

        flushed = await session.commit()

        assert flushed == 2
        assert len(session.pending) == 0
        assert len(q) == 2
        out = [await q.get() for _ in range(2)]
        assert [e.payload["seq"] for e in out] == [0, 1]
        assert all(e.topic == "todo.create" for e in out)

    @pytest.mark.asyncio
    async def test_rollback_drops_pending(self) -> None:
        """rollback() discards the pending buffer without touching the queue."""
        q = WriteQueue(maxsize=10)
        session = QueueWriteSession(topic="todo.update", queue=q)
        await session.put({"seq": 9})

        await session.rollback()

        assert len(session.pending) == 0
        assert len(q) == 0

    @pytest.mark.asyncio
    async def test_commit_is_noop_when_pending_empty(self) -> None:
        """commit() on an empty session flushes zero envelopes and harms nothing."""
        q = WriteQueue(maxsize=10)
        session = QueueWriteSession(topic="todo.create", queue=q)

        flushed = await session.commit()

        assert flushed == 0
        assert len(q) == 0


class TestQueueWriteSessionOverflow:
    @pytest.mark.asyncio
    async def test_drop_oldest_evicts_oldest_when_full(self) -> None:
        """DROP_OLDEST: committing into a full queue evicts the oldest queued item."""
        q = WriteQueue(maxsize=2, policy=OverflowPolicy.DROP_OLDEST)
        # Pre-fill the queue so the next commit must evict.
        await q.put(Envelope(topic="t", payload={"seq": 0}))
        await q.put(Envelope(topic="t", payload={"seq": 1}))
        assert q.is_full()

        session = QueueWriteSession(topic="todo.create", queue=q)
        await session.put({"seq": 2})
        flushed = await session.commit()

        assert flushed == 1
        # Queue remains capped at 2; oldest (seq=0) evicted → [1, 2].
        assert len(q) == 2
        out = [await q.get() for _ in range(2)]
        assert [e.payload["seq"] for e in out] == [1, 2]
        assert q.total_dropped == 1
        assert len(session.pending) == 0

    @pytest.mark.asyncio
    async def test_reject_raises_when_full(self) -> None:
        """REJECT: committing into a full queue raises QueueFullError."""
        q = WriteQueue(maxsize=1, policy=OverflowPolicy.REJECT)
        await q.put(Envelope(topic="t", payload={"seq": 0}))
        assert q.is_full()

        session = QueueWriteSession(topic="todo.create", queue=q)
        await session.put({"seq": 1})

        with pytest.raises(QueueFullError):
            await session.commit()

        # Queue unchanged; pending preserved so the caller can retry/backoff.
        assert q.total_rejected == 1
        assert len(q) == 1
        assert len(session.pending) == 1
        assert session.pending[0].payload == {"seq": 1}


class TestEnqueueOrCommit:
    @pytest.mark.asyncio
    async def test_uses_queue_when_present(self) -> None:
        """When app.state._write_queue is set, enqueue_or_commit enqueues + returns 202."""
        app = FastAPI()
        q = WriteQueue(maxsize=10)
        app.state._write_queue = q

        payload = {"title": "ship beta.3", "project_id": "gludd"}
        enqueued, status = await enqueue_or_commit(
            app, topic="todo.create", payload=payload
        )

        assert enqueued is True
        assert status == HTTP_ENQUEUED
        assert status == 202
        assert len(q) == 1
        env = await q.get()
        assert env.topic == "todo.create"
        assert env.payload == payload

    @pytest.mark.asyncio
    async def test_falls_back_to_inline_commit_when_queue_absent(self) -> None:
        """When app.state._write_queue is None, the inline_commit callback runs + returns 200."""
        app = FastAPI()
        # Inline mode: no write queue attached.
        assert getattr(app.state, "_write_queue", None) is None

        called: list[dict[str, object]] = []

        async def inline_commit() -> None:
            called.append({"inline": True})

        payload = {"title": "ship beta.3"}
        enqueued, status = await enqueue_or_commit(
            app,
            topic="todo.create",
            payload=payload,
            inline_commit=inline_commit,
        )

        assert enqueued is False
        assert status == HTTP_INLINE_COMMIT
        assert status == 200
        # The inline callback ran exactly once.
        assert called == [{"inline": True}]

    @pytest.mark.asyncio
    async def test_inline_mode_without_callback_returns_not_enqueued(self) -> None:
        """Inline mode with no callback still signals (False, 200); router owns the write."""
        app = FastAPI()

        enqueued, status = await enqueue_or_commit(
            app, topic="todo.create", payload={"x": 1}
        )

        assert enqueued is False
        assert status == 200

    @pytest.mark.asyncio
    async def test_returns_202_accepted_when_enqueued(self) -> None:
        """The 202 status code is the explicit 'accepted, deferred to writer' signal."""
        app = FastAPI()
        app.state._write_queue = WriteQueue(maxsize=10)

        _, status = await enqueue_or_commit(
            app, topic="todo.create", payload={"x": 1}
        )

        # 202 Accepted — the request has been queued for the writer subprocess,
        # not yet applied. Distinct from 200/201 which mean "applied now."
        assert status == 202
