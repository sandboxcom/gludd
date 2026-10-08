"""Durable operator escalation for watchdog-detected agent stalls."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import HumanTodoRepository
from general_ludd.events import EventBus
from general_ludd.events.types import StallDetectedEvent
from general_ludd.observability.stall_escalation import (
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    StallEscalationSubscriber,
)


@pytest_asyncio.fixture()
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a disposable durable store for human-todo assertions."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _stall_event(*, event_id: str = "stall-event-1") -> StallDetectedEvent:
    return StallDetectedEvent(
        operation="agent\nworker:" + ("x" * 500),
        elapsed_s=91.23456,
        deadline_s=30.0,
        thread_stacks={"worker-1": "SECRET STACK CONTENT"},
        correlation_id="todo-123",
        source="stall-watchdog",
        event_id=event_id,
    )


@pytest.mark.asyncio
async def test_synthetic_stall_creates_one_bounded_stack_free_blocker(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    bus = EventBus()
    subscriber = StallEscalationSubscriber(
        event_bus=bus,
        session_factory=sessions,
        loop=asyncio.get_running_loop(),
    )
    subscriber.start()
    event = _stall_event()

    assert bus.publish(event) == 1
    assert bus.publish(event) == 1
    await subscriber.drain()

    async with sessions() as session:
        rows = await HumanTodoRepository(session).list_all(agent_id="stall-watchdog")
    assert len(rows) == 1
    row = rows[0]
    assert row.category == "blocker"
    assert row.priority == "urgent"
    assert row.status == "open"
    assert row.session_id == "stall:stall-event-1"
    assert len(row.title) <= MAX_TITLE_CHARS
    assert len(row.body) <= MAX_BODY_CHARS
    assert "SECRET STACK CONTENT" not in row.title
    assert "SECRET STACK CONTENT" not in row.body
    assert "todo-123" in row.body
    assert "91.235s" in row.body
    assert "30.000s" in row.body
    assert set(json.loads(row.tags)) == {"agent-stall", "operator-action"}

    await subscriber.aclose()


@pytest.mark.asyncio
async def test_worker_thread_publish_uses_existing_event_loop(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    bus = EventBus()
    subscriber = StallEscalationSubscriber(
        event_bus=bus,
        session_factory=sessions,
        loop=asyncio.get_running_loop(),
    )
    subscriber.start()

    delivered = await asyncio.to_thread(bus.publish, _stall_event(event_id="thread-event"))
    assert delivered == 1
    await subscriber.drain()

    async with sessions() as session:
        rows = await HumanTodoRepository(session).list_all(agent_id="stall-watchdog")
    assert [row.session_id for row in rows] == ["stall:thread-event"]
    await subscriber.aclose()


@pytest.mark.asyncio
async def test_database_failure_is_logged_once_without_retry_or_watchdog_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    class BrokenFactory:
        def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
            nonlocal calls
            calls += 1
            raise RuntimeError("database unavailable")

    bus = EventBus()
    subscriber = StallEscalationSubscriber(
        event_bus=bus,
        session_factory=BrokenFactory(),
        loop=asyncio.get_running_loop(),
    )
    subscriber.start()
    event = _stall_event(event_id="db-failure")

    with caplog.at_level(logging.ERROR):
        assert bus.publish(event) == 1
        await subscriber.drain()
        assert bus.publish(event) == 1
        await subscriber.drain()

    assert calls == 1
    assert "stall escalation persistence failed" in caplog.text
    await subscriber.aclose()


@pytest.mark.asyncio
async def test_close_unsubscribes_without_cancelling_pending_writes(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    bus = EventBus()
    subscriber = StallEscalationSubscriber(
        event_bus=bus,
        session_factory=sessions,
        loop=asyncio.get_running_loop(),
    )
    subscriber.start()
    subscriber.start()

    assert bus.publish(_stall_event(event_id="before-close")) == 1
    await subscriber.aclose()
    assert bus.publish(_stall_event(event_id="after-close")) == 0

    async with sessions() as session:
        rows = await HumanTodoRepository(session).list_all(agent_id="stall-watchdog")
    assert [row.session_id for row in rows] == ["stall:before-close"]


@pytest.mark.asyncio
async def test_invalid_evidence_uses_safe_fallbacks(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    bus = EventBus()
    subscriber = StallEscalationSubscriber(
        event_bus=bus,
        session_factory=sessions,
        loop=asyncio.get_running_loop(),
    )
    subscriber.start()
    event = StallDetectedEvent(
        operation="\n\t",
        elapsed_s=cast(float, "not-a-number"),
        deadline_s=float("inf"),
        correlation_id=None,
        event_id="",
    )

    bus.publish(event)
    await subscriber.drain()

    async with sessions() as session:
        rows = await HumanTodoRepository(session).list_all(agent_id="stall-watchdog")
    assert len(rows) == 1
    assert rows[0].session_id == "stall:unknown-event"
    assert "unknown operation" in rows[0].title
    assert "unknown-task" in rows[0].body
    assert "elapsed=0.000s" in rows[0].body
    assert "deadline=0.000s" in rows[0].body
    await subscriber.aclose()


@pytest.mark.asyncio
async def test_durable_identity_prevents_replay_after_restart(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        await HumanTodoRepository(session).create(
            agent_id="stall-watchdog",
            title="existing blocker",
            body="already durable",
            category="blocker",
            priority="urgent",
            session_id="stall:durable-replay",
        )
        await session.commit()

    bus = EventBus()
    subscriber = StallEscalationSubscriber(
        event_bus=bus,
        session_factory=sessions,
        loop=asyncio.get_running_loop(),
    )
    subscriber.start()
    bus.publish(_stall_event(event_id="durable-replay"))
    await subscriber.drain()

    async with sessions() as session:
        rows = await HumanTodoRepository(session).list_all(agent_id="stall-watchdog")
    assert len(rows) == 1
    assert rows[0].title == "existing blocker"
    await subscriber.aclose()


@pytest.mark.asyncio
async def test_capacity_is_bounded_without_retry(
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("general_ludd.observability.stall_escalation.MAX_PENDING_WRITES", 0)
    bus = EventBus()
    subscriber = StallEscalationSubscriber(
        event_bus=bus,
        session_factory=sessions,
        loop=asyncio.get_running_loop(),
    )
    subscriber.start()

    with caplog.at_level(logging.ERROR):
        bus.publish(_stall_event(event_id="at-capacity"))
        bus.publish(_stall_event(event_id="at-capacity"))
        await subscriber.drain()

    assert caplog.text.count("stall escalation capacity exhausted") == 1
    async with sessions() as session:
        assert await HumanTodoRepository(session).list_all(agent_id="stall-watchdog") == []
    await subscriber.aclose()


@pytest.mark.asyncio
async def test_identity_memory_evicts_oldest_entry(
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("general_ludd.observability.stall_escalation.MAX_TRACKED_IDENTITIES", 1)
    subscriber = StallEscalationSubscriber(
        event_bus=EventBus(),
        session_factory=sessions,
        loop=asyncio.get_running_loop(),
    )

    subscriber._remember("first")
    subscriber._remember("second")

    assert subscriber._seen == {"second"}
    await subscriber.aclose()


@pytest.mark.asyncio
async def test_closed_loop_scheduling_failure_is_contained(
    sessions: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    bus = EventBus()
    subscriber = StallEscalationSubscriber(
        event_bus=bus,
        session_factory=sessions,
        loop=closed_loop,
    )
    subscriber.start()

    with caplog.at_level(logging.ERROR):
        assert bus.publish(_stall_event(event_id="closed-loop")) == 1

    assert "stall escalation scheduling failed" in caplog.text
    await subscriber.aclose()


@pytest.mark.asyncio
async def test_external_task_failures_are_observable(
    sessions: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    subscriber = StallEscalationSubscriber(
        event_bus=EventBus(),
        session_factory=sessions,
        loop=asyncio.get_running_loop(),
    )

    async def fail() -> None:
        raise RuntimeError("unexpected task failure")

    failed = asyncio.create_task(fail())
    cancelled = asyncio.create_task(asyncio.sleep(60))
    cancelled.cancel()
    await asyncio.gather(failed, cancelled, return_exceptions=True)
    with caplog.at_level(logging.ERROR):
        subscriber._on_task_done(failed)
        subscriber._on_task_done(cancelled)

    assert "stall escalation task failed" in caplog.text
    assert "externally cancelled" in caplog.text
