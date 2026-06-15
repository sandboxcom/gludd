"""Tests for SpendRepository using in-memory aiosqlite.

Mirrors the pattern of other async DB tests: create an in-memory aiosqlite
engine, run create_all, test through the repository, dispose the engine in
a try/finally to avoid asyncio teardown warnings.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import SpendRepository


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def session(session_factory):
    async with session_factory() as sess:
        yield sess


class TestSpendRepositoryAdd:
    async def test_add_returns_model(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        row = await repo.add(ts=1000.0, cost_usd=1.5, kind="token")
        assert row.id is not None
        assert row.cost_usd == pytest.approx(1.5)
        assert row.kind == "token"

    async def test_add_with_project_id(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        row = await repo.add(ts=1000.0, cost_usd=0.5, kind="infra", project_id="proj-abc")
        assert row.project_id == "proj-abc"

    async def test_add_with_model(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        row = await repo.add(ts=1000.0, cost_usd=0.1, kind="token", model="claude-3-haiku-20240307")
        assert row.model == "claude-3-haiku-20240307"

    async def test_add_multiple_rows(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        await repo.add(ts=100.0, cost_usd=1.0, kind="token")
        await repo.add(ts=200.0, cost_usd=2.0, kind="infra")
        rows = await repo.list_since(0.0)
        assert len(rows) == 2


class TestSpendRepositoryListSince:
    async def test_list_since_returns_all_after_epoch(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        await repo.add(ts=100.0, cost_usd=1.0, kind="token")
        await repo.add(ts=200.0, cost_usd=2.0, kind="infra")
        await repo.add(ts=300.0, cost_usd=3.0, kind="token")
        rows = await repo.list_since(50.0)
        assert len(rows) == 3

    async def test_list_since_filters_old_records(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        await repo.add(ts=100.0, cost_usd=1.0, kind="token")
        await repo.add(ts=200.0, cost_usd=2.0, kind="infra")
        rows = await repo.list_since(150.0)
        assert len(rows) == 1
        assert rows[0].cost_usd == pytest.approx(2.0)

    async def test_list_since_boundary_inclusive(self, session: AsyncSession) -> None:
        """Records with ts == since_epoch are included."""
        repo = SpendRepository(session)
        await repo.add(ts=100.0, cost_usd=5.0, kind="token")
        rows = await repo.list_since(100.0)
        assert len(rows) == 1

    async def test_list_since_empty_when_all_old(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        await repo.add(ts=10.0, cost_usd=1.0, kind="token")
        rows = await repo.list_since(500.0)
        assert rows == []


class TestSpendRepositoryTotalSince:
    async def test_total_since_sums_all(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        await repo.add(ts=100.0, cost_usd=1.0, kind="token")
        await repo.add(ts=200.0, cost_usd=2.5, kind="infra")
        total = await repo.total_since(50.0)
        assert total == pytest.approx(3.5)

    async def test_total_since_zero_when_empty(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        total = await repo.total_since(0.0)
        assert total == pytest.approx(0.0)

    async def test_total_since_with_project_filter(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        await repo.add(ts=100.0, cost_usd=3.0, kind="token", project_id="proj-a")
        await repo.add(ts=100.0, cost_usd=7.0, kind="token", project_id="proj-b")
        total_a = await repo.total_since(0.0, project_id="proj-a")
        assert total_a == pytest.approx(3.0)

    async def test_total_since_no_project_filter_sums_all_projects(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        await repo.add(ts=100.0, cost_usd=3.0, kind="token", project_id="proj-a")
        await repo.add(ts=100.0, cost_usd=7.0, kind="token", project_id="proj-b")
        total = await repo.total_since(0.0)
        assert total == pytest.approx(10.0)

    async def test_total_since_excludes_old_records(self, session: AsyncSession) -> None:
        repo = SpendRepository(session)
        await repo.add(ts=50.0, cost_usd=10.0, kind="token")
        await repo.add(ts=200.0, cost_usd=4.0, kind="infra")
        total = await repo.total_since(100.0)
        assert total == pytest.approx(4.0)
