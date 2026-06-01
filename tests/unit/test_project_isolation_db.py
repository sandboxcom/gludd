"""Unit tests for multi-project database isolation.

Tests that project_id properly scopes all database queries:
- Todos, task returns, decisions, queues, audit events, variables, bucket leases
- are all isolated by project_id with no cross-project leakage.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import (
    AuditEventModel,
    AuditEventType,
    Base,
    BucketLeaseModel,
    ProjectModel,
    TaskReturnModel,
    TodoModel,
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.db.repository import (
    ProjectRepository,
    TaskReturnRepository,
    TodoRepository,
)
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
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def two_projects(async_session: AsyncSession):
    p1 = ProjectModel(name="alpha", workspace_path="/tmp/alpha")
    p2 = ProjectModel(name="beta", workspace_path="/tmp/beta")
    async_session.add(p1)
    async_session.add(p2)
    await async_session.flush()
    return p1, p2


class TestProjectModel:
    async def test_create_project(self, async_session: AsyncSession):
        p = ProjectModel(name="test-project", workspace_path="/tmp/test")
        async_session.add(p)
        await async_session.flush()
        assert p.project_id.startswith("proj-")
        assert p.name == "test-project"
        assert p.workspace_path == "/tmp/test"
        assert p.active is True
        assert p.created_at is not None

    async def test_project_defaults(self, async_session: AsyncSession):
        p = ProjectModel(name="minimal")
        async_session.add(p)
        await async_session.flush()
        assert p.workspace_path == ""
        assert p.config == "{}"
        assert p.active is True
        assert p.description == ""


class TestTodoProjectIsolation:
    async def test_todo_has_project_id(self, async_session: AsyncSession, two_projects):
        p1, _ = two_projects
        todo = TodoModel(
            todo_id="TODO-AAAA1111",
            title="Alpha task",
            project_id=p1.project_id,
        )
        async_session.add(todo)
        await async_session.flush()
        assert todo.project_id == p1.project_id

    async def test_list_by_status_filters_by_project(
        self, async_session: AsyncSession, two_projects
    ):
        p1, p2 = two_projects
        t1 = TodoModel(
            todo_id="TODO-AAAA1111",
            title="Alpha task",
            status=TodoStatus.QUEUED,
            project_id=p1.project_id,
        )
        t2 = TodoModel(
            todo_id="TODO-BBBB2222",
            title="Beta task",
            status=TodoStatus.QUEUED,
            project_id=p2.project_id,
        )
        async_session.add_all([t1, t2])
        await async_session.flush()

        repo = TodoRepository(async_session)
        alpha_todos = await repo.list_by_status(
            TodoStatus.QUEUED, project_id=p1.project_id
        )
        beta_todos = await repo.list_by_status(
            TodoStatus.QUEUED, project_id=p2.project_id
        )

        assert len(alpha_todos) == 1
        assert alpha_todos[0].todo_id == "TODO-AAAA1111"
        assert len(beta_todos) == 1
        assert beta_todos[0].todo_id == "TODO-BBBB2222"

    async def test_claim_runnable_filters_by_project(
        self, async_session: AsyncSession, two_projects
    ):
        p1, p2 = two_projects
        t1 = TodoModel(
            todo_id="TODO-AAAA1111",
            title="Alpha task",
            status=TodoStatus.QUEUED,
            project_id=p1.project_id,
        )
        t2 = TodoModel(
            todo_id="TODO-BBBB2222",
            title="Beta task",
            status=TodoStatus.QUEUED,
            project_id=p2.project_id,
        )
        async_session.add_all([t1, t2])
        await async_session.flush()

        repo = TodoRepository(async_session)
        claimed_alpha = await repo.claim_runnable(
            project_id=p1.project_id, limit=10
        )

        assert len(claimed_alpha) == 1
        assert claimed_alpha[0].todo_id == "TODO-AAAA1111"
        assert claimed_alpha[0].status == TodoStatus.ACTIVE

        beta_repo = TodoRepository(async_session)
        beta_remaining = await beta_repo.list_by_status(
            TodoStatus.QUEUED, project_id=p2.project_id
        )
        assert len(beta_remaining) == 1
        assert beta_remaining[0].todo_id == "TODO-BBBB2222"

    async def test_claim_runnable_no_project_gets_all(
        self, async_session: AsyncSession, two_projects
    ):
        p1, p2 = two_projects
        t1 = TodoModel(
            todo_id="TODO-AAAA1111",
            title="Alpha task",
            status=TodoStatus.QUEUED,
            project_id=p1.project_id,
        )
        t2 = TodoModel(
            todo_id="TODO-BBBB2222",
            title="Beta task",
            status=TodoStatus.QUEUED,
            project_id=p2.project_id,
        )
        async_session.add_all([t1, t2])
        await async_session.flush()

        repo = TodoRepository(async_session)
        claimed = await repo.claim_runnable(limit=10)
        assert len(claimed) == 2

    async def test_create_todo_with_project_id(
        self, async_session: AsyncSession, two_projects
    ):
        p1, _ = two_projects
        repo = TodoRepository(async_session)
        todo = await repo.create(
            {
                "todo_id": "TODO-CCCC3333",
                "title": "Created via repo",
                "project_id": p1.project_id,
            }
        )
        assert todo.project_id == p1.project_id


class TestTaskReturnProjectIsolation:
    async def test_task_return_has_project_id(
        self, async_session: AsyncSession, two_projects
    ):
        p1, _ = two_projects
        tr = TaskReturnModel(
            return_id="RET-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            project_id=p1.project_id,
        )
        async_session.add(tr)
        await async_session.flush()
        assert tr.project_id == p1.project_id

    async def test_claim_unreviewed_filters_by_project(
        self, async_session: AsyncSession, two_projects
    ):
        p1, p2 = two_projects
        tr1 = TaskReturnModel(
            return_id="RET-ALPHA",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            status="created",
            project_id=p1.project_id,
        )
        tr2 = TaskReturnModel(
            return_id="RET-BETA",
            job_id="JOB-002",
            playbook="noop.yml",
            queue="core",
            status="created",
            project_id=p2.project_id,
        )
        async_session.add_all([tr1, tr2])
        await async_session.flush()

        repo = TaskReturnRepository(async_session)
        alpha_returns = await repo.claim_unreviewed(project_id=p1.project_id)
        assert len(alpha_returns) == 1
        assert alpha_returns[0].return_id == "RET-ALPHA"

        beta_repo = TaskReturnRepository(async_session)
        beta_remaining = await beta_repo.claim_unreviewed(project_id=p2.project_id)
        assert len(beta_remaining) == 1
        assert beta_remaining[0].return_id == "RET-BETA"

    async def test_claim_unreviewed_no_project_gets_all(
        self, async_session: AsyncSession, two_projects
    ):
        p1, p2 = two_projects
        tr1 = TaskReturnModel(
            return_id="RET-ALPHA",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            status="created",
            project_id=p1.project_id,
        )
        tr2 = TaskReturnModel(
            return_id="RET-BETA",
            job_id="JOB-002",
            playbook="noop.yml",
            queue="core",
            status="created",
            project_id=p2.project_id,
        )
        async_session.add_all([tr1, tr2])
        await async_session.flush()

        repo = TaskReturnRepository(async_session)
        all_returns = await repo.claim_unreviewed()
        assert len(all_returns) == 2


class TestAuditEventProjectIsolation:
    async def test_audit_event_has_project_id(
        self, async_session: AsyncSession, two_projects
    ):
        p1, _ = two_projects
        audit = AuditEventModel(
            event_type=AuditEventType.TODO_CREATED,
            actor="agent",
            entity_type="todo",
            entity_id="TODO-AAAA1111",
            project_id=p1.project_id,
        )
        async_session.add(audit)
        await async_session.flush()
        assert audit.project_id == p1.project_id


class TestVariableNamespaceProjectIsolation:
    async def test_variable_namespace_has_project_id(
        self, async_session: AsyncSession, two_projects
    ):
        p1, p2 = two_projects
        ns1 = VariableNamespaceModel(
            namespace="db-config",
            project_id=p1.project_id,
        )
        ns2 = VariableNamespaceModel(
            namespace="db-config",
            project_id=p2.project_id,
        )
        async_session.add_all([ns1, ns2])
        await async_session.flush()
        assert ns1.project_id == p1.project_id
        assert ns2.project_id == p2.project_id
        assert ns1.namespace == ns2.namespace

    async def test_same_namespace_name_different_projects(
        self, async_session: AsyncSession, two_projects
    ):
        p1, p2 = two_projects
        ns1 = VariableNamespaceModel(
            namespace="shared-name",
            project_id=p1.project_id,
        )
        ns2 = VariableNamespaceModel(
            namespace="shared-name",
            project_id=p2.project_id,
        )
        async_session.add_all([ns1, ns2])
        await async_session.flush()

        val1 = VariableValueModel(
            namespace_id=ns1.id,
            key="host",
            value="alpha-db.internal",
        )
        val2 = VariableValueModel(
            namespace_id=ns2.id,
            key="host",
            value="beta-db.internal",
        )
        async_session.add_all([val1, val2])
        await async_session.flush()
        assert val1.value != val2.value


class TestBucketLeaseProjectIsolation:
    async def test_bucket_lease_has_project_id(
        self, async_session: AsyncSession, two_projects
    ):
        p1, _ = two_projects
        from datetime import timedelta

        lease = BucketLeaseModel(
            bucket_key="todo:core:active",
            holder_id="worker-1",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            project_id=p1.project_id,
        )
        async_session.add(lease)
        await async_session.flush()
        assert lease.project_id == p1.project_id


class TestProjectRepository:
    async def test_create_and_get_project(self, async_session: AsyncSession):
        repo = ProjectRepository(async_session)
        p = await repo.create(
            {"name": "test-project", "workspace_path": "/tmp/test"}
        )
        assert p.project_id.startswith("proj-")
        fetched = await repo.get_by_id(p.project_id)
        assert fetched is not None
        assert fetched.name == "test-project"

    async def test_list_active_projects(self, async_session: AsyncSession):
        repo = ProjectRepository(async_session)
        await repo.create({"name": "active1", "workspace_path": "/tmp/a1"})
        p2 = await repo.create({"name": "active2", "workspace_path": "/tmp/a2"})
        await repo.deactivate(p2.project_id)

        active = await repo.list_active()
        assert len(active) == 1
        assert active[0].name == "active1"

    async def test_deactivate_project(self, async_session: AsyncSession):
        repo = ProjectRepository(async_session)
        p = await repo.create({"name": "to-deactivate", "workspace_path": "/tmp/d"})
        await repo.deactivate(p.project_id)
        fetched = await repo.get_by_id(p.project_id)
        assert fetched is not None
        assert fetched.active is False


class TestCrossProjectDataLeakage:
    async def test_two_projects_todos_dont_mix(
        self, async_session: AsyncSession, two_projects
    ):
        p1, p2 = two_projects
        repo = TodoRepository(async_session)
        await repo.create(
            {
                "todo_id": "TODO-ALPHA1",
                "title": "Alpha todo 1",
                "status": TodoStatus.QUEUED,
                "project_id": p1.project_id,
            }
        )
        await repo.create(
            {
                "todo_id": "TODO-ALPHA2",
                "title": "Alpha todo 2",
                "status": TodoStatus.QUEUED,
                "project_id": p1.project_id,
            }
        )
        await repo.create(
            {
                "todo_id": "TODO-BETA1",
                "title": "Beta todo 1",
                "status": TodoStatus.QUEUED,
                "project_id": p2.project_id,
            }
        )

        alpha = await repo.list_by_status(
            TodoStatus.QUEUED, project_id=p1.project_id
        )
        beta = await repo.list_by_status(
            TodoStatus.QUEUED, project_id=p2.project_id
        )

        assert len(alpha) == 2
        assert len(beta) == 1
        assert all(t.project_id == p1.project_id for t in alpha)
        assert all(t.project_id == p2.project_id for t in beta)

    async def test_claim_does_not_steal_from_other_project(
        self, async_session: AsyncSession, two_projects
    ):
        p1, p2 = two_projects
        t1 = TodoModel(
            todo_id="TODO-AAAA1111",
            title="Alpha only",
            status=TodoStatus.QUEUED,
            project_id=p1.project_id,
        )
        t2 = TodoModel(
            todo_id="TODO-BBBB2222",
            title="Beta only",
            status=TodoStatus.QUEUED,
            project_id=p2.project_id,
        )
        async_session.add_all([t1, t2])
        await async_session.flush()

        repo = TodoRepository(async_session)
        claimed = await repo.claim_runnable(project_id=p2.project_id)

        assert len(claimed) == 1
        assert claimed[0].todo_id == "TODO-BBBB2222"

        alpha_remaining = await repo.list_by_status(
            TodoStatus.QUEUED, project_id=p1.project_id
        )
        assert len(alpha_remaining) == 1
        assert alpha_remaining[0].todo_id == "TODO-AAAA1111"
