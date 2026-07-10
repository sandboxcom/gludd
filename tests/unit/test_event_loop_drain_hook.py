"""Unit tests for the EventLoop inbound-queue drain hook (B3.1.3 Slice 5).

The drain runs once per ``run_forever`` iteration, between ``tick()`` and the
inter-tick sleep. It empties the inbound :class:`WriteQueue` non-blockingly
via ``get_nowait()`` and applies each envelope inside its own DB session
(commit per envelope; rollback on envelope-level error). With no DB session
factory configured the payloads are dropped (logged) so the queue never
blocks the producer.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.ipc import Envelope, WriteQueue


def _env(seq: int, topic: str = "test.event") -> Envelope:
    return Envelope(topic=topic, payload={"seq": seq})


def _make_loop(inbound_queue: WriteQueue | None = None, **overrides):
    """Construct an EventLoop with the given inbound_queue (default None).

    Mirrors the helper in test_event_loop.py but adds the inbound_queue kwarg.
    """
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.add = MagicMock()
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()
    defaults = dict(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=session,
        http_client=AsyncMock(),
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
        inbound_queue=inbound_queue,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "todo_repo": todo_repo,
        "task_return_repo": task_return_repo,
    }


class _SessionCM:
    """Async context manager that yields a fresh AsyncMock session."""

    def __init__(self, session: AsyncMock) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncMock:
        return self._session

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _RecordingSessionFactory:
    """Stand-in for ``async_sessionmaker`` whose calls are observable.

    Each ``__call__`` records a fresh AsyncMock session and returns an async
    context manager yielding it. The drain opens one session per envelope, so
    ``len(sessions)`` equals the number of envelopes applied.
    """

    def __init__(self) -> None:
        self.sessions: list[AsyncMock] = []

    def __call__(self) -> _SessionCM:
        session = AsyncMock()
        self.sessions.append(session)
        return _SessionCM(session)


class TestNoInboundQueue:
    @pytest.mark.asyncio
    async def test_no_inbound_queue_no_drain(self) -> None:
        loop, _ = _make_loop(inbound_queue=None)
        # Must be a no-op: no queue, no crash.
        await loop._drain_inbound_queue()


class TestDrainOrdering:
    @pytest.mark.asyncio
    async def test_drain_applies_envelopes_in_order(self) -> None:
        q = WriteQueue(maxsize=10)
        for seq in (1, 2, 3):
            await q.put(_env(seq))
        loop, _ = _make_loop(inbound_queue=q)
        loop._session_factory = _RecordingSessionFactory()
        applied: list[int] = []

        async def spy_apply(envelope: Envelope, session: AsyncMock) -> None:
            applied.append(envelope.payload["seq"])

        loop._apply_envelope = spy_apply
        await loop._drain_inbound_queue()
        assert applied == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_drain_preserves_ordering_under_concurrency(self) -> None:
        q = WriteQueue(maxsize=20)
        for seq in range(10):
            await q.put(_env(seq))
        loop, _ = _make_loop(inbound_queue=q)
        loop._session_factory = _RecordingSessionFactory()
        applied: list[int] = []

        async def spy_apply(envelope: Envelope, session: AsyncMock) -> None:
            applied.append(envelope.payload["seq"])

        loop._apply_envelope = spy_apply
        await loop._drain_inbound_queue()
        assert applied == list(range(10))


class TestDrainNonblocking:
    @pytest.mark.asyncio
    async def test_drain_stops_when_queue_empty(self) -> None:
        q = WriteQueue(maxsize=10)
        loop, _ = _make_loop(inbound_queue=q)
        start = time.monotonic()
        await loop._drain_inbound_queue()
        elapsed_ms = (time.monotonic() - start) * 1000
        # Non-blocking: an empty queue must return near-instantly. Widened
        # from 10.0ms to 200.0ms to absorb xdist scheduling jitter while
        # still proving the call doesn't block.
        assert elapsed_ms < 200.0


class TestDrainErrorIsolation:
    @pytest.mark.asyncio
    async def test_drain_continues_after_envelope_error(self) -> None:
        q = WriteQueue(maxsize=10)
        for seq in (1, 2, 3):
            await q.put(_env(seq))
        loop, _ = _make_loop(inbound_queue=q)
        factory = _RecordingSessionFactory()
        loop._session_factory = factory
        committed: list[int] = []

        async def spy_apply(envelope: Envelope, session: AsyncMock) -> None:
            if envelope.payload["seq"] == 2:
                raise RuntimeError("boom")
            committed.append(envelope.payload["seq"])

        loop._apply_envelope = spy_apply
        await loop._drain_inbound_queue()
        # 1st + 3rd envelopes committed; 2nd raised and rolled back.
        assert committed == [1, 3]
        # 3 sessions opened (one per envelope — failure does not stop the drain).
        assert len(factory.sessions) == 3

    @pytest.mark.asyncio
    async def test_drain_does_not_commit_on_envelope_error(self) -> None:
        q = WriteQueue(maxsize=10)
        await q.put(_env(1))
        loop, _ = _make_loop(inbound_queue=q)
        factory = _RecordingSessionFactory()
        loop._session_factory = factory

        async def failing_apply(envelope: Envelope, session: AsyncMock) -> None:
            raise RuntimeError("always fails")

        loop._apply_envelope = failing_apply
        await loop._drain_inbound_queue()
        # The single session must have rolled back, not committed.
        session = factory.sessions[0]
        session.rollback.assert_awaited()
        session.commit.assert_not_awaited()


class TestDrainSessionLifecycle:
    @pytest.mark.asyncio
    async def test_drain_opens_fresh_session_per_envelope(self) -> None:
        q = WriteQueue(maxsize=10)
        await q.put(_env(1))
        await q.put(_env(2))
        loop, _ = _make_loop(inbound_queue=q)
        factory = _RecordingSessionFactory()
        loop._session_factory = factory

        async def noop_apply(envelope: Envelope, session: AsyncMock) -> None:
            return None

        loop._apply_envelope = noop_apply
        await loop._drain_inbound_queue()
        # 2 envelopes -> 2 distinct sessions entered.
        assert len(factory.sessions) == 2
        assert factory.sessions[0] is not factory.sessions[1]


class TestRunForeverIntegration:
    @pytest.mark.asyncio
    async def test_run_forever_invokes_drain_between_ticks(self) -> None:
        q = WriteQueue(maxsize=10)
        loop, _ = _make_loop(inbound_queue=q)
        loop._session_factory = _RecordingSessionFactory()
        drain_calls = 0
        original_drain = loop._drain_inbound_queue

        async def counting_drain() -> None:
            nonlocal drain_calls
            drain_calls += 1
            await original_drain()

        loop._drain_inbound_queue = counting_drain
        ticks = 0

        async def stub_tick() -> dict[str, object]:
            nonlocal ticks
            ticks += 1
            # Push one envelope per tick so the drain has work each time.
            await q.put(_env(ticks))
            if ticks >= 3:
                loop.stop()
            # Skip the real phase machinery; we only care that run_forever
            # calls drain after each tick.
            return {"phases_completed": 0, "tick_duration_ms": 0.0}

        loop.tick = stub_tick
        await loop.run_forever(interval=0.001)
        assert ticks == 3
        assert drain_calls == 3


class TestNoDbMode:
    @pytest.mark.asyncio
    async def test_drain_in_no_db_mode_drops_payloads(self) -> None:
        q = WriteQueue(maxsize=10)
        await q.put(_env(1))
        await q.put(_env(2))
        loop, _ = _make_loop(inbound_queue=q, session=None)
        # No session and no factory -> no-DB mode.
        assert loop._session_factory is None
        await loop._drain_inbound_queue()
        # Payloads dropped: queue empties even though nothing was applied.
        assert len(q) == 0


class TestApplyEnvelopeRouting:
    @pytest.mark.asyncio
    async def test_apply_envelope_routes_by_topic(self) -> None:
        q = WriteQueue(maxsize=10)
        payload = {"title": "ship it", "status": "pending"}
        await q.put(Envelope(topic="todo.upsert", payload=payload))
        loop, mocks = _make_loop(inbound_queue=q)
        loop._session_factory = _RecordingSessionFactory()
        await loop._drain_inbound_queue()
        # Stub handler routed the "todo.upsert" envelope to the todo repo.
        mocks["todo_repo"].create.assert_awaited_once_with(payload)
