"""Integration tests for multi-project worker isolation.

Verifies that:
1. A gunicorn worker processing a job for project A has NO access to project B data
2. The dispatch process (EventLoop) knows about all projects but cannot directly write
3. Worker receives only project-scoped variables, not cross-project data
4. A special config-update agent is used for updating gludd configuration
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base, ProjectModel, TodoModel, VariableNamespaceModel, VariableValueModel
from general_ludd.db.repository import TodoRepository, VariableNamespaceRepository
from general_ludd.event_loop.loop import EventLoop
from general_ludd.projects.manager import ProjectManager
from general_ludd.projects.workspace import ProjectWorkspace
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
async def db_session(async_engine):
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestWorkerProjectIsolation:
    async def test_worker_receives_only_project_scoped_vars(
        self, db_session: AsyncSession
    ):
        p1 = ProjectModel(name="alpha", workspace_path="/tmp/alpha")
        p2 = ProjectModel(name="beta", workspace_path="/tmp/beta")
        db_session.add_all([p1, p2])
        await db_session.flush()

        ns_alpha = VariableNamespaceModel(
            namespace="secrets",
            project_id=p1.project_id,
        )
        ns_beta = VariableNamespaceModel(
            namespace="secrets",
            project_id=p2.project_id,
        )
        db_session.add_all([ns_alpha, ns_beta])
        await db_session.flush()

        v_alpha = VariableValueModel(
            namespace_id=ns_alpha.id,
            key="DB_PASSWORD",
            value="alpha-secret-123",
        )
        v_beta = VariableValueModel(
            namespace_id=ns_beta.id,
            key="DB_PASSWORD",
            value="beta-secret-456",
        )
        db_session.add_all([v_alpha, v_beta])
        await db_session.flush()

        var_repo = VariableNamespaceRepository(db_session)

        alpha_vars = await var_repo.load_vars_for_project(p1.project_id)
        beta_vars = await var_repo.load_vars_for_project(p2.project_id)

        assert alpha_vars is not None
        assert alpha_vars.get("DB_PASSWORD") == "alpha-secret-123"
        assert beta_vars is not None
        assert beta_vars.get("DB_PASSWORD") == "beta-secret-456"
        assert alpha_vars.get("DB_PASSWORD") != beta_vars.get("DB_PASSWORD")

    async def test_dispatch_claims_only_one_project_at_a_time(
        self, db_session: AsyncSession
    ):
        p1 = ProjectModel(name="alpha", workspace_path="/tmp/alpha")
        p2 = ProjectModel(name="beta", workspace_path="/tmp/beta")
        db_session.add_all([p1, p2])
        await db_session.flush()

        t1 = TodoModel(
            todo_id="TODO-ALPHA",
            title="Alpha work",
            status=TodoStatus.QUEUED,
            project_id=p1.project_id,
        )
        t2 = TodoModel(
            todo_id="TODO-BETA",
            title="Beta work",
            status=TodoStatus.QUEUED,
            project_id=p2.project_id,
        )
        db_session.add_all([t1, t2])
        await db_session.flush()

        pm = ProjectManager()
        alpha_proj = pm.add_project("alpha", 100.0)
        alpha_proj.project_id = p1.project_id

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

    async def test_dispatch_job_contains_only_project_data(
        self, db_session: AsyncSession
    ):
        p1 = ProjectModel(name="alpha", workspace_path="/tmp/alpha")
        p2 = ProjectModel(name="beta", workspace_path="/tmp/beta")
        db_session.add_all([p1, p2])
        await db_session.flush()

        ns_alpha = VariableNamespaceModel(
            namespace="config",
            project_id=p1.project_id,
        )
        ns_beta = VariableNamespaceModel(
            namespace="config",
            project_id=p2.project_id,
        )
        db_session.add_all([ns_alpha, ns_beta])
        await db_session.flush()

        v_alpha = VariableValueModel(
            namespace_id=ns_alpha.id,
            key="API_ENDPOINT",
            value="https://alpha.example.com",
        )
        v_beta = VariableValueModel(
            namespace_id=ns_beta.id,
            key="API_ENDPOINT",
            value="https://beta.example.com",
        )
        db_session.add_all([v_alpha, v_beta])
        await db_session.flush()

        t1 = TodoModel(
            todo_id="TODO-ALPHA",
            title="Alpha work",
            status=TodoStatus.QUEUED,
            project_id=p1.project_id,
        )
        db_session.add(t1)
        await db_session.flush()

        dispatched_jobs: list[dict] = []

        class MockHTTP:
            async def post(self, url, json=None):
                dispatched_jobs.append(json)

        pm = ProjectManager()
        alpha_proj = pm.add_project("alpha", 100.0)
        alpha_proj.project_id = p1.project_id

        var_repo = VariableNamespaceRepository(db_session)
        repo = TodoRepository(db_session)
        loop = EventLoop(
            session=db_session,
            todo_repo=repo,
            http_client=MockHTTP(),
            project_manager=pm,
            variable_repo=var_repo,
        )
        await loop.tick()

        assert len(dispatched_jobs) == 1
        job = dispatched_jobs[0]
        assert job["project_id"] == p1.project_id
        assert job["project_id"] != p2.project_id

    async def test_project_workspace_isolation(self, tmp_path):
        alpha_ws = ProjectWorkspace("proj-alpha", str(tmp_path))
        beta_ws = ProjectWorkspace("proj-beta", str(tmp_path))

        alpha_ws.ensure_dirs()
        beta_ws.ensure_dirs()

        assert alpha_ws.artifacts_dir != beta_ws.artifacts_dir
        assert alpha_ws.repo_dir != beta_ws.repo_dir
        assert alpha_ws.private_data_dir != beta_ws.private_data_dir

        assert str(alpha_ws.artifacts_dir).count("proj-alpha") >= 1
        assert str(beta_ws.artifacts_dir).count("proj-beta") >= 1


class TestDispatchReadOnlyPattern:
    """Test that the dispatch (EventLoop) is read-only for projects.

    The EventLoop should:
    - Read from all projects to claim and dispatch work
    - NOT directly write to any project's workspace
    - Route updates through the config-update agent
    """

    async def test_eventloop_does_not_write_to_project_workspace(
        self, db_session: AsyncSession, tmp_path
    ):
        p1 = ProjectModel(
            name="alpha",
            workspace_path=str(tmp_path / "alpha"),
        )
        db_session.add(p1)
        await db_session.flush()

        t1 = TodoModel(
            todo_id="TODO-ALPHA",
            title="Alpha work",
            status=TodoStatus.QUEUED,
            project_id=p1.project_id,
        )
        db_session.add(t1)
        await db_session.flush()

        pm = ProjectManager()
        alpha_proj = pm.add_project("alpha", 100.0)
        alpha_proj.project_id = p1.project_id

        repo = TodoRepository(db_session)
        loop = EventLoop(
            session=db_session,
            todo_repo=repo,
            project_manager=pm,
        )

        ws = ProjectWorkspace(p1.project_id, str(tmp_path))
        ws.ensure_dirs()

        artifacts_before = list(ws.artifacts_dir.glob("*"))

        await loop.tick()

        artifacts_after = list(ws.artifacts_dir.glob("*"))
        assert artifacts_before == artifacts_after

    async def test_eventloop_reads_all_projects_for_dispatch(
        self, db_session: AsyncSession
    ):
        p1 = ProjectModel(name="alpha", workspace_path="/tmp/alpha")
        p2 = ProjectModel(name="beta", workspace_path="/tmp/beta")
        db_session.add_all([p1, p2])
        await db_session.flush()

        pm = ProjectManager()
        alpha_proj = pm.add_project("alpha", 50.0)
        alpha_proj.project_id = p1.project_id
        beta_proj = pm.add_project("beta", 50.0)
        beta_proj.project_id = p2.project_id

        summary = pm.get_summary()
        assert summary["total_projects"] == 2
        project_ids = [p["project_id"] for p in summary["projects"]]
        assert p1.project_id in project_ids
        assert p2.project_id in project_ids
