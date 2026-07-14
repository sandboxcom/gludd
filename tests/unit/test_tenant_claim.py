"""H.12 — H-TENANT-CLAIM-FALLBACK: unscoped cross-tenant claim_runnable fallback."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base, ProjectModel, TodoModel, TodoStatus
from general_ludd.db.repository import TodoRepository


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
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestEventLoopClaimFailsWithoutProject:
    @pytest.mark.asyncio
    async def test_claim_runnable_fails_when_no_project_selected(self):
        todo_repo = AsyncMock()
        todo_repo.claim_runnable = AsyncMock()
        pm = MagicMock()
        pm.select_project.return_value = None
        from general_ludd.event_loop.loop import EventLoop
        loop = EventLoop.__new__(EventLoop)
        loop._todo_repo = todo_repo
        loop._project_manager = pm
        loop._tick_project_id = None
        loop._tick_state = {}
        loop._active_session = None
        loop._pause_controller = None
        loop._floor_controller = None
        loop._total_ticks = 1
        await loop._phase_claim_runnable_todos()
        todo_repo.claim_runnable.assert_not_called()
        assert loop._tick_state.get("claimed_todos") == []

    @pytest.mark.asyncio
    async def test_claim_runnable_scoped_when_project_selected(self):
        todo_repo = AsyncMock()
        todo_repo.claim_runnable = AsyncMock(return_value=[])
        pm = MagicMock()
        pm.select_project.return_value = None
        from general_ludd.event_loop.loop import EventLoop
        loop = EventLoop.__new__(EventLoop)
        loop._todo_repo = todo_repo
        loop._project_manager = pm
        loop._tick_project_id = "proj-abc"
        loop._tick_state = {}
        loop._active_session = None
        loop._pause_controller = None
        loop._floor_controller = None
        loop._total_ticks = 1
        await loop._phase_claim_runnable_todos()
        todo_repo.claim_runnable.assert_called_once_with(
            limit=10, project_id="proj-abc"
        )


class TestRepositoryClaimFailsWithoutProject:
    @pytest.mark.asyncio
    async def test_claim_runnable_returns_empty_when_no_project_scope(self, async_session):
        async_session.add(ProjectModel(project_id="proj-1", name="Project 1"))
        await async_session.flush()
        t1 = TodoModel(
            todo_id="T1",
            title="Task 1",
            status=TodoStatus.QUEUED,
            project_id="proj-1",
        )
        async_session.add(t1)
        await async_session.flush()
        repo = TodoRepository(async_session)
        claimed = await repo.claim_runnable(limit=10)
        assert len(claimed) == 0

    @pytest.mark.asyncio
    async def test_claim_runnable_scoped_still_works(self, async_session):
        async_session.add_all([
            ProjectModel(project_id="proj-1", name="Project 1"),
            ProjectModel(project_id="proj-2", name="Project 2"),
        ])
        await async_session.flush()
        t1 = TodoModel(
            todo_id="T1",
            title="Task 1",
            status=TodoStatus.QUEUED,
            project_id="proj-1",
        )
        t2 = TodoModel(
            todo_id="T2",
            title="Task 2",
            status=TodoStatus.QUEUED,
            project_id="proj-2",
        )
        async_session.add_all([t1, t2])
        await async_session.flush()
        repo = TodoRepository(async_session)
        claimed = await repo.claim_runnable(limit=10, project_id="proj-1")
        assert len(claimed) == 1
        assert claimed[0].todo_id == "T1"

    @pytest.mark.asyncio
    async def test_claim_runnable_scoped_instance_works(self, async_session):
        async_session.add_all([
            ProjectModel(project_id="proj-1", name="Project 1"),
            ProjectModel(project_id="proj-2", name="Project 2"),
        ])
        await async_session.flush()
        t1 = TodoModel(
            todo_id="T1",
            title="Task 1",
            status=TodoStatus.QUEUED,
            project_id="proj-1",
        )
        t2 = TodoModel(
            todo_id="T2",
            title="Task 2",
            status=TodoStatus.QUEUED,
            project_id="proj-2",
        )
        async_session.add_all([t1, t2])
        await async_session.flush()
        repo = TodoRepository(async_session, project_id="proj-2")
        claimed = await repo.claim_runnable(limit=10)
        assert len(claimed) == 1
        assert claimed[0].todo_id == "T2"
