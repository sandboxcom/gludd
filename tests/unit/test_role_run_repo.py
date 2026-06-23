"""Unit tests for RoleRunRepository using in-memory aiosqlite.

Verifies record() persists a row and count_by_role() returns correct counts.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, ProjectModel
from general_ludd.db.repository import RoleRunRepository


def _seed_projects(session: AsyncSession, *project_ids: str) -> list[ProjectModel]:
    """Build parent ProjectModel rows so RoleRunModel.project_id FK is satisfied."""
    rows = [ProjectModel(project_id=pid, name=f"project-{pid}") for pid in project_ids]
    for row in rows:
        session.add(row)
    return rows


@pytest_asyncio.fixture
async def session():
    """In-memory SQLite engine with all tables created; disposed in finally.

    StaticPool + check_same_thread=False keeps the single in-memory connection
    alive across the engine's connections so the schema persists for the test.
    Parent ProjectModel rows for p1/p2 are seeded to satisfy the
    RoleRunModel.project_id foreign key.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            for row in _seed_projects(s, "p1", "p2"):
                assert row.project_id in ("p1", "p2")
            await s.flush()
            yield s
    finally:
        await engine.dispose()


class TestRoleRunRepositoryRecord:
    async def test_record_inserts_row(self, session):
        repo = RoleRunRepository(session)
        row = await repo.record(project_id="p1", role="coder")
        await session.flush()
        assert row.id is not None
        assert row.project_id == "p1"
        assert row.role == "coder"

    async def test_record_multiple_rows(self, session):
        repo = RoleRunRepository(session)
        await repo.record(project_id="p1", role="coder")
        await repo.record(project_id="p1", role="coder")
        await repo.record(project_id="p1", role="reviewer")
        rows = await repo.list_all(project_id="p1")
        assert len(rows) == 3

    async def test_record_null_project_id(self, session):
        repo = RoleRunRepository(session)
        row = await repo.record(project_id=None, role="tester")
        await session.flush()
        assert row.project_id is None


class TestRoleRunRepositoryCountByRole:
    async def test_count_by_role_correct_counts(self, session):
        repo = RoleRunRepository(session)
        await repo.record("p1", "coder")
        await repo.record("p1", "coder")
        await repo.record("p1", "reviewer")
        counts = await repo.count_by_role("p1")
        assert counts == {"coder": 2, "reviewer": 1}

    async def test_count_by_role_empty_project(self, session):
        repo = RoleRunRepository(session)
        counts = await repo.count_by_role("nonexistent")
        assert counts == {}

    async def test_count_by_role_filters_by_project(self, session):
        repo = RoleRunRepository(session)
        await repo.record("p1", "alpha")
        await repo.record("p2", "beta")
        counts_p1 = await repo.count_by_role("p1")
        counts_p2 = await repo.count_by_role("p2")
        assert counts_p1 == {"alpha": 1}
        assert counts_p2 == {"beta": 1}

    async def test_count_by_role_all_projects_when_none(self, session):
        repo = RoleRunRepository(session)
        await repo.record("p1", "alpha")
        await repo.record("p2", "alpha")
        await repo.record("p2", "beta")
        counts = await repo.count_by_role(None)
        assert counts["alpha"] == 2
        assert counts["beta"] == 1
