"""Unit tests for the AgentMessageModel + AgentMessageRepository (message queue).

Uses SQLite in-memory with async sessions via aiosqlite so tests run without a
PostgreSQL instance, mirroring tests/unit/test_db_models.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import AgentMessageModel, Base
from general_ludd.db.repository import AgentMessageRepository


def _make_async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class TestAgentMessageModel:
    @pytest.mark.asyncio
    async def test_create_message_defaults(self, async_session: AsyncSession):
        msg = AgentMessageModel(sender="planner", recipient="coder", topic="hello")
        async_session.add(msg)
        await async_session.flush()
        assert msg.id.startswith("MSG-")
        assert msg.priority == "normal"
        assert msg.read_at is None
        assert msg.ttl_seconds is None
        assert isinstance(msg.created_at, datetime)


class TestAgentMessageRepository:
    @pytest.mark.asyncio
    async def test_send_inbox_ack_roundtrip(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        sent = await repo.send(
            {"sender": "planner", "recipient": "coder", "topic": "task", "body": "do x"}
        )
        inbox = await repo.inbox("coder")
        assert len(inbox) == 1
        assert inbox[0].id == sent.id

        acked = await repo.ack(sent.id)
        assert acked is not None
        assert acked.read_at is not None

        # After ack, unread inbox is empty but include-read shows it.
        assert await repo.inbox("coder", unread_only=True) == []
        all_msgs = await repo.inbox("coder", unread_only=False)
        assert len(all_msgs) == 1

    @pytest.mark.asyncio
    async def test_broadcast_visible_to_all_recipients(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        await repo.send({"sender": "boss", "recipient": "broadcast", "topic": "all-hands"})
        for who in ("coder", "reviewer", "planner"):
            inbox = await repo.inbox(who)
            assert len(inbox) == 1
            assert inbox[0].recipient == "broadcast"

    @pytest.mark.asyncio
    async def test_include_broadcast_false_hides_broadcast(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        await repo.send({"sender": "boss", "recipient": "broadcast", "topic": "all-hands"})
        await repo.send({"sender": "boss", "recipient": "coder", "topic": "direct"})
        inbox = await repo.inbox("coder", include_broadcast=False)
        assert len(inbox) == 1
        assert inbox[0].topic == "direct"

    @pytest.mark.asyncio
    async def test_expired_not_in_inbox_and_purged(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        msg = await repo.send(
            {"sender": "a", "recipient": "coder", "topic": "old", "ttl_seconds": 10}
        )
        # Force the row to look 60s old (ttl 10s) -> expired.
        msg.created_at = datetime.now(UTC) - timedelta(seconds=60)
        await async_session.flush()

        assert await repo.inbox("coder") == []
        purged = await repo.purge_expired()
        assert purged == 1
        # Second purge is a no-op.
        assert await repo.purge_expired() == 0

    @pytest.mark.asyncio
    async def test_non_expired_with_ttl_survives_purge(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        await repo.send(
            {"sender": "a", "recipient": "coder", "topic": "fresh", "ttl_seconds": 3600}
        )
        assert await repo.purge_expired() == 0
        assert len(await repo.inbox("coder")) == 1

    @pytest.mark.asyncio
    async def test_ack_unknown_returns_none(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        assert await repo.ack("MSG-DOESNOTEXIST") is None

    @pytest.mark.asyncio
    async def test_unread_counts_per_recipient(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        await repo.send({"sender": "a", "recipient": "coder", "topic": "1"})
        await repo.send({"sender": "a", "recipient": "coder", "topic": "2"})
        await repo.send({"sender": "a", "recipient": "reviewer", "topic": "3"})
        counts = await repo.unread_counts()
        assert counts["coder"] == 2
        assert counts["reviewer"] == 1
