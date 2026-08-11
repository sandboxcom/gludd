"""Deep tests for MemoryRepository — agent-memory key-value persistence (G1).

Covers: CRUD, namespace isolation, project_id scoping, TTL expiry, upsert
behavior, purge_expired, _is_expired edge cases, and session/resolution errors.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base, MemoryRecordModel
from general_ludd.db.repository import MemoryRepository


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def async_session(async_engine) -> AsyncSession:
    sf = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as session:
        yield session


@pytest.fixture
def repo(async_session: AsyncSession) -> MemoryRepository:
    return MemoryRepository(session=async_session)


# ── CRUD basics ────────────────────────────────────────────────────────


class TestMemoryCrud:
    async def test_set_and_get(self, repo: MemoryRepository):
        await repo.set("agent-1", "greeting", "hello")
        row = await repo.get("agent-1", "greeting")
        assert row is not None
        assert row.value == "hello"
        assert row.agent_id == "agent-1"
        assert row.key == "greeting"
        assert row.namespace == "default"

    async def test_get_nonexistent_returns_none(self, repo: MemoryRepository):
        assert await repo.get("agent-1", "nope") is None

    async def test_set_existing_key_updates_value(self, repo: MemoryRepository):
        await repo.set("agent-1", "count", "1")
        await repo.set("agent-1", "count", "2")
        row = await repo.get("agent-1", "count")
        assert row is not None
        assert row.value == "2"

    async def test_delete_existing_returns_true(self, repo: MemoryRepository):
        await repo.set("agent-1", "temp", "x")
        assert await repo.delete("agent-1", "temp") is True
        assert await repo.get("agent-1", "temp") is None

    async def test_delete_nonexistent_returns_false(self, repo: MemoryRepository):
        assert await repo.delete("agent-1", "nope") is False

    async def test_delete_then_set_is_fresh_insert(self, repo: MemoryRepository):
        await repo.set("agent-1", "key", "v1")
        assert await repo.delete("agent-1", "key") is True
        await repo.set("agent-1", "key", "v2")
        row = await repo.get("agent-1", "key")
        assert row is not None
        assert row.value == "v2"


# ── Namespace isolation ────────────────────────────────────────────────


class TestMemoryNamespace:
    async def test_different_namespace_same_key_independent(self, repo: MemoryRepository):
        await repo.set("agent-1", "key", "ns1-val", namespace="ns1")
        await repo.set("agent-1", "key", "ns2-val", namespace="ns2")
        r1 = await repo.get("agent-1", "key", namespace="ns1")
        r2 = await repo.get("agent-1", "key", namespace="ns2")
        assert r1 is not None and r1.value == "ns1-val"
        assert r2 is not None and r2.value == "ns2-val"

    async def test_get_default_namespace_if_not_specified(self, repo: MemoryRepository):
        await repo.set("agent-1", "key", "default-val")
        row = await repo.get("agent-1", "key")
        assert row is not None
        assert row.namespace == "default"

    async def test_delete_scoped_to_namespace(self, repo: MemoryRepository):
        await repo.set("agent-1", "key", "v1", namespace="ns1")
        await repo.set("agent-1", "key", "v2", namespace="ns2")
        assert await repo.delete("agent-1", "key", namespace="ns1") is True
        assert await repo.get("agent-1", "key", namespace="ns1") is None
        assert await repo.get("agent-1", "key", namespace="ns2") is not None


# ── project_id scoping ─────────────────────────────────────────────────


class TestMemoryProjectScoping:
    async def test_set_with_project_id(self, repo: MemoryRepository):
        await repo.set("agent-1", "key", "pval", project_id="proj-A")
        row = await repo.get("agent-1", "key", project_id="proj-A")
        assert row is not None
        assert row.project_id == "proj-A"

    async def test_get_without_project_id_misses_scoped_row(self, repo: MemoryRepository):
        await repo.set("agent-1", "key", "pval", project_id="proj-A")
        assert await repo.get("agent-1", "key") is None

    async def test_set_null_project_id_matches_null_lookup(self, repo: MemoryRepository):
        await repo.set("agent-1", "key", "val", project_id=None)
        row = await repo.get("agent-1", "key")
        assert row is not None
        assert row.project_id is None

    async def test_same_key_different_project_ids_independent(self, repo: MemoryRepository):
        await repo.set("agent-1", "key", "a", project_id="proj-A")
        await repo.set("agent-1", "key", "b", project_id="proj-B")
        ra = await repo.get("agent-1", "key", project_id="proj-A")
        rb = await repo.get("agent-1", "key", project_id="proj-B")
        assert ra is not None and ra.value == "a"
        assert rb is not None and rb.value == "b"


# ── TTL / expiry ───────────────────────────────────────────────────────


class TestMemoryTtl:
    async def test_expired_record_returns_none_on_get(self, repo: MemoryRepository, async_session: AsyncSession):
        await repo.set("agent-1", "transient", "gone", ttl_seconds=1)
        import asyncio

        await asyncio.sleep(1.1)
        assert await repo.get("agent-1", "transient") is None

    async def test_live_record_with_ttl_still_returns(self, repo: MemoryRepository):
        await repo.set("agent-1", "live", "here", ttl_seconds=3600)
        row = await repo.get("agent-1", "live")
        assert row is not None
        assert row.value == "here"

    async def test_delete_expired_cleanup_removes_row(self, repo: MemoryRepository, async_session: AsyncSession):
        await repo.set("agent-1", "stale", "old", ttl_seconds=1)
        import asyncio

        await asyncio.sleep(1.1)
        await repo.get("agent-1", "stale")
        await async_session.commit()

        from sqlalchemy import select

        remaining = (
            (
                await async_session.execute(
                    select(MemoryRecordModel).where(
                        MemoryRecordModel.agent_id == "agent-1",
                        MemoryRecordModel.key == "stale",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(remaining) == 0

    async def test_set_with_ttl_updates_ttl_on_existing(self, repo: MemoryRepository):
        await repo.set("agent-1", "flex", "v1")
        row1 = await repo.get("agent-1", "flex")
        assert row1 is not None
        assert row1.ttl_seconds is None

        await repo.set("agent-1", "flex", "v2", ttl_seconds=60)
        row2 = await repo.get("agent-1", "flex")
        assert row2 is not None
        assert row2.ttl_seconds == 60
        assert row2.value == "v2"


# ── _is_expired edge cases ─────────────────────────────────────────────


class TestIsExpired:
    def test_none_row_returns_false(self):
        assert MemoryRepository._is_expired(None) is False

    def test_no_ttl_returns_false(self):
        row = MemoryRecordModel(agent_id="a", key="k", value="v")
        assert MemoryRepository._is_expired(row) is False

    def test_ttl_but_no_created_at_returns_false(self):
        row = MemoryRecordModel(agent_id="a", key="k", value="v", ttl_seconds=10)
        row.created_at = None
        assert MemoryRepository._is_expired(row) is False

    def test_expired_returns_true(self):
        row = MemoryRecordModel(agent_id="a", key="k", value="v", ttl_seconds=1)
        row.created_at = datetime.now(UTC) - timedelta(seconds=2)
        assert MemoryRepository._is_expired(row) is True

    def test_not_expired_returns_false(self):
        row = MemoryRecordModel(agent_id="a", key="k", value="v", ttl_seconds=3600)
        row.created_at = datetime.now(UTC)
        assert MemoryRepository._is_expired(row) is False

    def test_naive_created_at_treated_as_utc(self):
        row = MemoryRecordModel(agent_id="a", key="k", value="v", ttl_seconds=1)
        row.created_at = (datetime.now(UTC) - timedelta(seconds=2)).replace(tzinfo=None)
        assert MemoryRepository._is_expired(row) is True


# ── list_by_namespace ──────────────────────────────────────────────────


class TestMemoryListByNamespace:
    async def test_list_returns_matching_namespace(self, repo: MemoryRepository):
        await repo.set("agent-1", "k1", "v1", namespace="ns1")
        await repo.set("agent-1", "k2", "v2", namespace="ns1")
        await repo.set("agent-1", "k3", "v3", namespace="ns2")

        rows = await repo.list_by_namespace("agent-1", namespace="ns1")
        assert len(rows) == 2
        keys = {r.key for r in rows}
        assert keys == {"k1", "k2"}

    async def test_list_wildcard_namespace_returns_all(self, repo: MemoryRepository):
        await repo.set("agent-1", "k1", "v1", namespace="ns1")
        await repo.set("agent-1", "k2", "v2", namespace="ns2")
        rows = await repo.list_by_namespace("agent-1", namespace="*")
        assert len(rows) == 2

    async def test_list_scoped_by_project(self, repo: MemoryRepository):
        await repo.set("agent-1", "k1", "v1", project_id="proj-A")
        await repo.set("agent-1", "k2", "v2", project_id="proj-B")
        rows = await repo.list_by_namespace("agent-1", project_id="proj-A")
        assert len(rows) == 1
        assert rows[0].key == "k1"

    async def test_list_excludes_expired_records(self, repo: MemoryRepository):
        await repo.set("agent-1", "live", "y", ttl_seconds=3600)
        await repo.set("agent-1", "dead", "n", ttl_seconds=1)
        import asyncio

        await asyncio.sleep(1.1)
        rows = await repo.list_by_namespace("agent-1")
        assert len(rows) == 1
        assert rows[0].key == "live"

    async def test_list_respects_limit_clamp(self, repo: MemoryRepository):
        for i in range(5):
            await repo.set("agent-1", f"k{i}", f"v{i}")
        rows = await repo.list_by_namespace("agent-1", limit=2)
        assert len(rows) <= 2

    async def test_list_wildcard_with_project_scoping(self, repo: MemoryRepository):
        await repo.set("agent-1", "k1", "v1", namespace="ns1", project_id="proj-A")
        await repo.set("agent-1", "k2", "v2", namespace="ns2", project_id="proj-A")
        await repo.set("agent-1", "k3", "v3", namespace="ns1", project_id="proj-B")
        rows = await repo.list_by_namespace("agent-1", namespace="*", project_id="proj-A")
        assert len(rows) == 2
        assert {r.key for r in rows} == {"k1", "k2"}

    async def test_list_empty_namespace_returns_empty(self, repo: MemoryRepository):
        rows = await repo.list_by_namespace("agent-1", namespace="empty")
        assert rows == []


# ── purge_expired ──────────────────────────────────────────────────────


class TestMemoryPurgeExpired:
    async def test_purge_removes_only_expired(self, repo: MemoryRepository):
        await repo.set("agent-1", "live", "y", ttl_seconds=3600)
        await repo.set("agent-1", "dead", "n", ttl_seconds=1)
        import asyncio

        await asyncio.sleep(1.1)
        purged = await repo.purge_expired()
        assert purged == 1
        assert await repo.get("agent-1", "live") is not None

    async def test_purge_no_expired_returns_zero(self, repo: MemoryRepository):
        await repo.set("agent-1", "live", "y", ttl_seconds=3600)
        purged = await repo.purge_expired()
        assert purged == 0

    async def test_purge_no_ttl_rows_untouched(self, repo: MemoryRepository):
        await repo.set("agent-1", "forever", "x")
        purged = await repo.purge_expired()
        assert purged == 0
        assert await repo.get("agent-1", "forever") is not None


# ── session resolution errors ──────────────────────────────────────────


class TestMemorySessionResolution:
    async def test_no_session_or_factory_raises(self):
        repo = MemoryRepository(session=None, session_factory=None)
        with pytest.raises(RuntimeError, match="no session or session_factory"):
            await repo.get("agent-1", "key")

    async def test_no_session_or_factory_raises_on_set(self):
        repo = MemoryRepository(session=None, session_factory=None)
        with pytest.raises(RuntimeError, match="no session or session_factory"):
            await repo.set("agent-1", "key", "val")

    async def test_no_session_or_factory_raises_on_delete(self):
        repo = MemoryRepository(session=None, session_factory=None)
        with pytest.raises(RuntimeError, match="no session or session_factory"):
            await repo.delete("agent-1", "key")

    async def test_no_session_or_factory_raises_on_list(self):
        repo = MemoryRepository(session=None, session_factory=None)
        with pytest.raises(RuntimeError, match="no session or session_factory"):
            await repo.list_by_namespace("agent-1")

    async def test_no_session_or_factory_raises_on_purge(self):
        repo = MemoryRepository(session=None, session_factory=None)
        with pytest.raises(RuntimeError, match="no session or session_factory"):
            await repo.purge_expired()


# ── upsert / idempotency ───────────────────────────────────────────────


class TestMemoryUpsert:
    async def test_repeated_sets_same_id(self, repo: MemoryRepository, async_session: AsyncSession):
        r1 = await repo.set("agent-1", "idem", "v1")
        r2 = await repo.set("agent-1", "idem", "v2")
        assert r1.id == r2.id
        assert r2.value == "v2"

    async def test_set_with_different_namespace_creates_new(self, repo: MemoryRepository):
        r1 = await repo.set("agent-1", "key", "v1", namespace="ns1")
        r2 = await repo.set("agent-1", "key", "v2", namespace="ns2")
        assert r1.id != r2.id

    async def test_set_with_different_project_creates_new(self, repo: MemoryRepository):
        r1 = await repo.set("agent-1", "key", "v1", project_id="proj-A")
        r2 = await repo.set("agent-1", "key", "v2", project_id="proj-B")
        assert r1.id != r2.id
