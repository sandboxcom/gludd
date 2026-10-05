"""Integration tests for G1 persistent agent memory wiring into the daemon.

Proves MemoryRepository is wired at daemon.py:1432, reachable via
app.state._memory_repo, and supports the full set/get/delete/list CRUD
cycle with namespace isolation and TTL expiry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import MemoryRepository

PSK = "test-memory-psk"
AUTH = {"Authorization": f"Bearer {PSK}"}


async def _make_app(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory

    memory_repo = MemoryRepository(session_factory=factory)
    app.state._memory_repo = memory_repo

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestMemoryDaemonWiring:
    @pytest.mark.asyncio
    async def test_memory_repo_is_on_app_state(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            assert hasattr(app.state, "_memory_repo")
            assert app.state._memory_repo is not None
            assert isinstance(app.state._memory_repo, MemoryRepository)
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_memory_repo_set_get_delete_cycle(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            repo: MemoryRepository = app.state._memory_repo

            rec = await repo.set(
                agent_id="planner", key="last_task", value="fixed bug #99"
            )
            assert rec.agent_id == "planner"
            assert rec.key == "last_task"
            assert rec.value == "fixed bug #99"
            assert rec.namespace == "default"

            fetched = await repo.get("planner", "last_task")
            assert fetched is not None
            assert fetched.value == "fixed bug #99"
            assert fetched.id == rec.id

            assert await repo.delete("planner", "last_task") is True
            assert await repo.get("planner", "last_task") is None
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_namespace_scoped_reads(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            repo: MemoryRepository = app.state._memory_repo

            await repo.set(agent_id="agent-1", key="conf", value="a-val", namespace="ns_a")
            await repo.set(agent_id="agent-1", key="conf", value="b-val", namespace="ns_b")

            a = await repo.get("agent-1", "conf", namespace="ns_a")
            b = await repo.get("agent-1", "conf", namespace="ns_b")
            assert a is not None and a.value == "a-val"
            assert b is not None and b.value == "b-val"

            default_list = await repo.list_by_namespace("agent-1", "default")
            assert len(default_list) == 0

            both = await repo.list_by_namespace("agent-1", "*")
            assert len(both) == 2
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ttl_expiry(self, monkeypatch):
        engine, factory, client, app = await _make_app(monkeypatch)
        try:
            repo: MemoryRepository = app.state._memory_repo

            rec = await repo.set(
                agent_id="agent-x", key="expires_soon", value="temp", ttl_seconds=1
            )
            async with factory() as session, session.begin():
                merged = await session.merge(rec)
                merged.created_at = datetime.now(UTC) - timedelta(seconds=10)

            expired = await repo.get("agent-x", "expires_soon")
            assert expired is None

            await repo.set(
                agent_id="agent-y", key="stays", value="permanent"
            )
            purged = await repo.purge_expired()
            assert purged == 0  # The expired row was already auto-cleaned on get

            stay = await repo.get("agent-y", "stays")
            assert stay is not None
            assert stay.value == "permanent"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_memory_list_by_namespace_returns_records(self, monkeypatch):
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            repo: MemoryRepository = app.state._memory_repo

            await repo.set(agent_id="a1", key="k1", value="v1", namespace="default")
            await repo.set(agent_id="a1", key="k2", value="v2", namespace="default")
            await repo.set(agent_id="a1", key="k3", value="v3", namespace="other")

            default_entries = await repo.list_by_namespace("a1", "default")
            assert len(default_entries) == 2
            assert {r.key for r in default_entries} == {"k1", "k2"}

            other_entries = await repo.list_by_namespace("a1", "other")
            assert len(other_entries) == 1
            assert other_entries[0].value == "v3"
        finally:
            await client.aclose()
            await engine.dispose()
