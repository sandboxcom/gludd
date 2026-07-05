"""Unit tests for G1 persistent agent memory (MemoryRecordModel + MemoryRepository).

Uses SQLite in-memory with async sessions via aiosqlite, mirroring the
patterns in tests/unit/test_agent_message_repo.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import MemoryRepository


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

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
    session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


class TestMemoryRepository:
    @pytest.mark.asyncio
    async def test_set_and_get_roundtrip(self, async_session: AsyncSession):
        repo = MemoryRepository(async_session)
        record = await repo.set(
            agent_id="agent-1", key="preferred_model", value="gpt-4", namespace="default"
        )
        assert record.agent_id == "agent-1"
        assert record.key == "preferred_model"
        assert record.value == "gpt-4"
        assert record.namespace == "default"
        assert isinstance(record.created_at, datetime)
        assert isinstance(record.updated_at, datetime)

        fetched = await repo.get("agent-1", "preferred_model", "default")
        assert fetched is not None
        assert fetched.value == "gpt-4"

    @pytest.mark.asyncio
    async def test_set_upsert_overwrites_existing(self, async_session: AsyncSession):
        repo = MemoryRepository(async_session)
        await repo.set(agent_id="agent-1", key="counter", value="1")
        updated = await repo.set(agent_id="agent-1", key="counter", value="2")
        assert updated.value == "2"

        fetched = await repo.get("agent-1", "counter")
        assert fetched is not None
        assert fetched.value == "2"

    @pytest.mark.asyncio
    async def test_ttl_expiry_and_purge(self, async_session: AsyncSession):
        repo = MemoryRepository(async_session)
        record = await repo.set(
            agent_id="agent-1", key="temp", value="data", ttl_seconds=1
        )
        record.created_at = datetime.now(UTC) - timedelta(seconds=10)
        await async_session.flush()

        expired = await repo.get("agent-1", "temp")
        assert expired is None

        await repo.set(
            agent_id="agent-1", key="expiring", value="x", ttl_seconds=1
        )
        await repo.set(agent_id="agent-1", key="permanent", value="y")
        row = await repo.get("agent-1", "expiring")
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(seconds=10)
        await async_session.flush()

        purged = await repo.purge_expired()
        assert purged == 1

        perm = await repo.get("agent-1", "permanent")
        assert perm is not None
        assert perm.value == "y"

    @pytest.mark.asyncio
    async def test_delete_returns_true_when_record_exists(self, async_session):
        repo = MemoryRepository(async_session)
        await repo.set(agent_id="agent-1", key="del-key", value="val")
        deleted = await repo.delete("agent-1", "del-key")
        assert deleted is True
        fetched = await repo.get("agent-1", "del-key")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_record_missing(self, async_session):
        repo = MemoryRepository(async_session)
        deleted = await repo.delete("agent-1", "no-such-key")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_list_by_namespace(self, async_session):
        repo = MemoryRepository(async_session)
        await repo.set(agent_id="agent-1", key="a", value="1", namespace="ns1")
        await repo.set(agent_id="agent-1", key="b", value="2", namespace="ns1")
        await repo.set(agent_id="agent-1", key="c", value="3", namespace="ns2")
        results = await repo.list_by_namespace("agent-1", namespace="ns1")
        assert len(results) == 2
        keys = {r.key for r in results}
        assert keys == {"a", "b"}

    @pytest.mark.asyncio
    async def test_list_by_namespace_filters_expired(self, async_session):
        repo = MemoryRepository(async_session)
        from datetime import UTC, datetime, timedelta

        await repo.set(agent_id="agent-1", key="active", value="1", namespace="ns")
        expired_rec = await repo.set(agent_id="agent-1", key="expired", value="2", namespace="ns", ttl_seconds=60)
        expired_rec.created_at = datetime.now(UTC) - timedelta(seconds=120)
        await async_session.flush()

        results = await repo.list_by_namespace("agent-1", namespace="ns")
        assert len(results) == 1
        assert results[0].key == "active"

    @pytest.mark.asyncio
    async def test_concurrent_set_same_key(self, async_session):

        repo1 = MemoryRepository(async_session)
        repo2 = MemoryRepository(async_session)

        await repo1.set(agent_id="agent-1", key="shared", value="first")
        await repo2.set(agent_id="agent-1", key="shared", value="second")

        fetched = await repo1.get("agent-1", "shared")
        assert fetched is not None
        assert fetched.value == "second"
