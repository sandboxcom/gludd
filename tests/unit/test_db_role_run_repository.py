"""Deep behavioral tests for db/repository.py RoleRunRepository.

Proves record / count_by_role / list_all with project scoping,
pagination clamping, and empty-result contracts — 0 prior unit test coverage
beyond a single list_all call in test_db_models.py.

Mirrors the async-session fixture pattern from test_db_repository_coverage.py.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base
from general_ludd.db.repository import RoleRunRepository


@pytest_asyncio.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    sf = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as session:
        yield session


class TestRoleRunRecord:
    async def test_record_basic_insert(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        row = await repo.record(project_id="proj-A", role="coder")
        assert row.project_id == "proj-A"
        assert row.role == "coder"
        assert row.id is not None

    async def test_record_null_project(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        row = await repo.record(project_id=None, role="planner")
        assert row.project_id is None
        assert row.role == "planner"

    async def test_record_multiple_roles_same_project(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record(project_id="proj-X", role="coder")
        await repo.record(project_id="proj-X", role="reviewer")
        await repo.record(project_id="proj-X", role="coder")
        counts = await repo.count_by_role(project_id="proj-X")
        assert counts == {"coder": 2, "reviewer": 1}


class TestRoleRunCountByRole:
    async def test_count_by_role_empty(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        counts = await repo.count_by_role()
        assert counts == {}

    async def test_count_by_role_empty_for_nonexistent_project(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record(project_id="proj-A", role="coder")
        counts = await repo.count_by_role(project_id="proj-NOPE")
        assert counts == {}

    async def test_count_by_role_scoped_to_project(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record(project_id="proj-A", role="coder")
        await repo.record(project_id="proj-A", role="coder")
        await repo.record(project_id="proj-B", role="reviewer")
        a_counts = await repo.count_by_role(project_id="proj-A")
        assert a_counts == {"coder": 2}
        b_counts = await repo.count_by_role(project_id="proj-B")
        assert b_counts == {"reviewer": 1}

    async def test_count_by_role_global_includes_all(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record(project_id="proj-A", role="coder")
        await repo.record(project_id="proj-B", role="reviewer")
        await repo.record(project_id=None, role="planner")
        counts = await repo.count_by_role()
        assert counts == {"coder": 1, "reviewer": 1, "planner": 1}

    async def test_count_by_role_global_with_null_project(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record(project_id=None, role="planner")
        await repo.record(project_id="proj-A", role="coder")
        counts = await repo.count_by_role()
        assert counts == {"planner": 1, "coder": 1}


class TestRoleRunListAll:
    async def test_list_all_empty(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        rows = await repo.list_all()
        assert rows == []

    async def test_list_all_basic(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        r1 = await repo.record(project_id="proj-A", role="coder")
        r2 = await repo.record(project_id="proj-A", role="reviewer")
        rows = await repo.list_all()
        assert len(rows) == 2
        assert {r.id for r in rows} == {r1.id, r2.id}

    async def test_list_all_project_scoped(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record(project_id="proj-A", role="coder")
        await repo.record(project_id="proj-A", role="reviewer")
        await repo.record(project_id="proj-B", role="coder")
        rows = await repo.list_all(project_id="proj-A")
        assert len(rows) == 2
        assert all(r.project_id == "proj-A" for r in rows)

    async def test_list_all_limit(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        for i in range(5):
            await repo.record(project_id="proj-X", role=f"role-{i}")
        rows = await repo.list_all(limit=2)
        assert len(rows) == 2

    async def test_list_all_offset(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        for i in range(3):
            await repo.record(project_id="proj-X", role=f"role-{i}")
        rows = await repo.list_all(limit=10, offset=1)
        assert len(rows) == 2

    async def test_list_all_nonexistent_project_returns_empty(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record(project_id="proj-REAL", role="coder")
        rows = await repo.list_all(project_id="proj-NOPE")
        assert rows == []

    async def test_list_all_global_includes_null_project(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record(project_id=None, role="planner")
        await repo.record(project_id="proj-A", role="coder")
        rows = await repo.list_all()
        assert len(rows) == 2
        project_ids = {r.project_id for r in rows}
        assert project_ids == {None, "proj-A"}


class TestRoleRunIntegration:
    """Record + query round-trip: record writes, count/list read back."""

    async def test_record_and_read_back(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        row = await repo.record(project_id="p1", role="coder")
        rows = await repo.list_all(project_id="p1")
        assert len(rows) == 1
        assert rows[0].id == row.id
        assert rows[0].role == "coder"

    async def test_many_roles_round_trip(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        roles = ["coder", "reviewer", "planner", "editor", "compactor", "enumerator"]
        for role in roles:
            await repo.record(project_id="p1", role=role)
        for role in roles:
            await repo.record(project_id="p1", role=role)
        counts = await repo.count_by_role(project_id="p1")
        assert len(counts) == 6
        assert all(counts[r] == 2 for r in roles)
        rows = await repo.list_all(project_id="p1")
        assert len(rows) == 12

    async def test_null_mixed_with_scoped(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record(project_id="A", role="coder")
        await repo.record(project_id="A", role="reviewer")
        await repo.record(project_id=None, role="planner")
        await repo.record(project_id=None, role="planner")
        global_counts = await repo.count_by_role()
        assert global_counts == {"coder": 1, "reviewer": 1, "planner": 2}
        a_counts = await repo.count_by_role(project_id="A")
        assert a_counts == {"coder": 1, "reviewer": 1}
