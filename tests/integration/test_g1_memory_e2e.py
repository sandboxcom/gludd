"""Integration/e2e tests for G1 persistent agent memory.

Proves end-to-end: MemoryRecordModel → MemoryRepository → retrieval →
injection into agent prompts via EventLoop._build_memory_section.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
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
async def engine():
    e = _make_async_engine()
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await e.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    sf = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as s:
        yield s


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf


class TestG1MemoryE2E:
    @pytest.mark.asyncio
    async def test_full_crud_cycle(self, session: AsyncSession):
        repo = MemoryRepository(session)

        rec = await repo.set(
            agent_id="agent-1", key="favorite_color", value="blue", namespace="prefs"
        )
        assert rec.agent_id == "agent-1"
        assert rec.key == "favorite_color"
        assert rec.value == "blue"
        assert rec.namespace == "prefs"
        assert isinstance(rec.id, str) and len(rec.id) > 0

        fetched = await repo.get("agent-1", "favorite_color", "prefs")
        assert fetched is not None
        assert fetched.value == "blue"
        assert fetched.id == rec.id

        updated = await repo.set(
            agent_id="agent-1", key="favorite_color", value="green", namespace="prefs"
        )
        assert updated.value == "green"

        assert await repo.delete("agent-1", "favorite_color", "prefs") is True
        assert await repo.get("agent-1", "favorite_color", "prefs") is None
        assert await repo.delete("agent-1", "favorite_color", "prefs") is False

    @pytest.mark.asyncio
    async def test_namespace_isolation(self, session: AsyncSession):
        repo = MemoryRepository(session)

        await repo.set(agent_id="agent-1", key="setting", value="ns-a", namespace="a")
        await repo.set(agent_id="agent-1", key="setting", value="ns-b", namespace="b")

        a = await repo.get("agent-1", "setting", "a")
        b = await repo.get("agent-1", "setting", "b")
        assert a is not None and a.value == "ns-a"
        assert b is not None and b.value == "ns-b"

    @pytest.mark.asyncio
    async def test_list_by_namespace(self, session: AsyncSession):
        repo = MemoryRepository(session)

        await repo.set(agent_id="agent-1", key="k1", value="v1", namespace="default")
        await repo.set(agent_id="agent-1", key="k2", value="v2", namespace="default")
        await repo.set(agent_id="agent-1", key="k3", value="v3", namespace="other")

        default = await repo.list_by_namespace("agent-1", "default")
        assert len(default) == 2
        keys = {r.key for r in default}
        assert keys == {"k1", "k2"}

        other = await repo.list_by_namespace("agent-1", "other")
        assert len(other) == 1
        assert other[0].value == "v3"

    @pytest.mark.asyncio
    async def test_ttl_expiry_and_purge(self, session: AsyncSession):
        repo = MemoryRepository(session)

        rec = await repo.set(
            agent_id="agent-1", key="temp", value="data", ttl_seconds=1
        )
        rec.created_at = datetime.now(UTC) - timedelta(seconds=10)
        await session.flush()

        expired = await repo.get("agent-1", "temp")
        assert expired is None

        await repo.set(
            agent_id="agent-1", key="expiring", value="x", ttl_seconds=1
        )
        await repo.set(agent_id="agent-1", key="permanent", value="y")
        row = await repo.get("agent-1", "expiring")
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(seconds=10)
        await session.flush()

        purged = await repo.purge_expired()
        assert purged == 1

        perm = await repo.get("agent-1", "permanent")
        assert perm is not None
        assert perm.value == "y"

    @pytest.mark.asyncio
    async def test_set_inserts_new_record_id(self, session: AsyncSession):
        repo = MemoryRepository(session)

        r1 = await repo.set(agent_id="a1", key="k1", value="v1")
        r2 = await repo.set(agent_id="a2", key="k1", value="v1")
        assert r1.id != r2.id
        assert isinstance(r1.id, str) and len(r1.id) > 0
        assert isinstance(r2.id, str) and len(r2.id) > 0

    @pytest.mark.asyncio
    async def test_set_returns_instance_with_created_at(self, session: AsyncSession):
        repo = MemoryRepository(session)
        rec = await repo.set(agent_id="ag", key="k", value="val")
        assert isinstance(rec.created_at, datetime)
        assert isinstance(rec.updated_at, datetime)

    @pytest.mark.asyncio
    async def test_repository_with_session_factory(self, session_factory: async_sessionmaker[AsyncSession]):
        repo = MemoryRepository(session_factory=session_factory)
        rec = await repo.set(agent_id="a", key="k", value="v")
        assert rec.value == "v"
        fetched = await repo.get("a", "k")
        assert fetched is not None
        assert fetched.value == "v"

    @pytest.mark.asyncio
    async def test_build_memory_section_injects_into_prompt(self, session: AsyncSession):
        repo = MemoryRepository(session)
        await repo.set(agent_id="agent-1", key="preferred_language", value="Python")
        await repo.set(agent_id="agent-1", key="last_task", value="bug fix #42")
        await repo.set(agent_id="agent-1", key="note", value="be concise")

        memories = await repo.list_by_namespace("agent-1", "default")

        records = []
        for m in memories:
            records.append({"key": m.key, "value": m.value})
        memory_text = "\n".join(
            f"- {r['key']}: {r['value']}" for r in sorted(records, key=lambda x: x["key"])
        )

        section = f"\n\n## Agent Memory\n\nThe following are stored agent memories:\n\n{memory_text}\n"
        prompt = f"ORIGINAL PROMPT{section}"

        assert "## Agent Memory" in prompt
        assert "preferred_language: Python" in prompt
        assert "last_task: bug fix #42" in prompt
        assert "note: be concise" in prompt

    @pytest.mark.asyncio
    async def test_list_by_namespace_respects_limit(self, session: AsyncSession):
        repo = MemoryRepository(session)
        for i in range(150):
            await repo.set(agent_id="a", key=f"k{i:03d}", value=f"v{i}")

        results = await repo.list_by_namespace("a", "default", limit=50)
        assert len(results) <= 100
        assert len(results) >= 50
