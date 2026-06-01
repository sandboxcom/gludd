"""Integration tests for multi-project isolation.

Tests that exercise multiple subsystems together:
- EventLoop + DB + ProjectManager for project-scoped processing
- Worker + JobSpec project_id propagation
- Daemon + DB for project-scoped API
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base, ProjectModel, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import EventLoop
from general_ludd.projects.manager import ProjectManager
from general_ludd.schemas.todo import TodoStatus


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
async def db_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


class TestEventLoopProjectScopedIntegration:
    async def test_event_loop_claims_only_project_todos(
        self, db_session: AsyncSession
    ):
        p1 = ProjectModel(name="alpha", workspace_path="/tmp/alpha")
        p2 = ProjectModel(name="beta", workspace_path="/tmp/beta")
        db_session.add_all([p1, p2])
        await db_session.flush()

        t1 = TodoModel(
            todo_id="TODO-ALPHA1",
            title="Alpha work",
            status=TodoStatus.QUEUED,
            project_id=p1.project_id,
        )
        t2 = TodoModel(
            todo_id="TODO-BETA1",
            title="Beta work",
            status=TodoStatus.QUEUED,
            project_id=p2.project_id,
        )
        db_session.add_all([t1, t2])
        await db_session.flush()

        pm = ProjectManager()
        alpha_project = pm.add_project("alpha", 100.0)
        alpha_project.project_id = p1.project_id

        repo = TodoRepository(db_session)
        loop = EventLoop(
            session=db_session,
            todo_repo=repo,
            project_manager=pm,
        )
        await loop.tick()

        alpha_active = await repo.list_by_status(
            TodoStatus.ACTIVE, project_id=p1.project_id
        )
        beta_queued = await repo.list_by_status(
            TodoStatus.QUEUED, project_id=p2.project_id
        )
        assert len(alpha_active) == 1
        assert len(beta_queued) == 1

    async def test_event_loop_dispatch_includes_project_id(
        self, db_session: AsyncSession
    ):
        p1 = ProjectModel(name="alpha", workspace_path="/tmp/alpha")
        db_session.add(p1)
        await db_session.flush()

        t1 = TodoModel(
            todo_id="TODO-ALPHA1",
            title="Alpha work",
            status=TodoStatus.QUEUED,
            project_id=p1.project_id,
        )
        db_session.add(t1)
        await db_session.flush()

        pm = ProjectManager()
        alpha_project = pm.add_project("alpha", 100.0)
        alpha_project.project_id = p1.project_id

        dispatched: list[dict] = []

        class MockHTTP:
            async def post(self, url, json=None):
                dispatched.append(json)

        repo = TodoRepository(db_session)
        loop = EventLoop(
            session=db_session,
            todo_repo=repo,
            project_manager=pm,
            http_client=MockHTTP(),
        )
        await loop.tick()

        assert len(dispatched) == 1
        assert dispatched[0]["project_id"] == p1.project_id


class TestMigration002Smoke:
    def test_migration_file_exists(self):
        from pathlib import Path

        migration_file = (
            Path(__file__).parent.parent.parent
            / "alembic"
            / "versions"
            / "002_add_projects_and_project_id.py"
        )
        assert migration_file.exists(), f"Migration file not found: {migration_file}"
        content = migration_file.read_text()
        assert "projects" in content
        assert "project_id" in content
        assert 'revision: str = "002"' in content
        assert 'down_revision: Union[str, None] = "001"' in content
