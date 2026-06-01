"""Unit tests for SQLAlchemy ORM models and repository pattern.

Uses SQLite in-memory with async sessions via aiosqlite so tests
run without a PostgreSQL instance.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import (
    AuditEventModel,
    AuditEventType,
    Base,
    BucketLeaseModel,
    QueueModel,
    TaskDecisionModel,
    TaskReturnModel,
    TodoEventModel,
    TodoModel,
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.db.repository import (
    ConcurrencyError,
    InvalidTransitionError,
    QueueRepository,
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
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class TestTodoModel:
    async def test_create_todo_with_all_fields(self, async_session: AsyncSession):
        todo = TodoModel(
            todo_id="TODO-ABCD1234",
            title="Implement auth",
            description="Add JWT authentication",
            status=TodoStatus.BACKLOG,
            priority=5,
            queue="core",
            tags='["auth","backend"]',
            risk_level="high",
            work_type="code",
            resource_profile="hybrid",
            parent_todo_id=None,
            child_todo_ids="[]",
            acceptance_criteria='["JWT works"]',
            test_commands='["make test"]',
            molecule_scenarios="[]",
            molecule_evidence_refs="[]",
            coverage_requirements="90%",
            dependencies="[]",
            created_by="agent",
            assigned_agent="gpt-4",
            model_profile="default",
            prompt_profile="standard",
            worktree="/tmp/worktree1",
            branch_name="feature/auth",
            artifacts="[]",
            evidence_refs="[]",
            confidence=0.85,
            manual_hold_reason=None,
            approval_policy="none",
            version=1,
            completed_at=None,
        )
        async_session.add(todo)
        await async_session.flush()
        assert todo.id is not None
        assert todo.todo_id == "TODO-ABCD1234"
        assert todo.title == "Implement auth"
        assert todo.status == TodoStatus.BACKLOG
        assert todo.version == 1

    async def test_todo_default_fields(self, async_session: AsyncSession):
        todo = TodoModel(title="Simple task")
        async_session.add(todo)
        await async_session.flush()
        assert todo.todo_id.startswith("TODO-")
        assert todo.status == TodoStatus.BACKLOG
        assert todo.priority == 0
        assert todo.queue == "core"
        assert todo.version == 1
        assert todo.created_by == "agent"

    async def test_todo_created_at_auto_set(self, async_session: AsyncSession):
        todo = TodoModel(title="Test timestamps")
        async_session.add(todo)
        await async_session.flush()
        assert todo.created_at is not None
        assert todo.updated_at is not None

    async def test_todo_plan_artifact_nullable(self, async_session: AsyncSession):
        todo = TodoModel(title="Plan test")
        async_session.add(todo)
        await async_session.flush()
        assert todo.plan_artifact is None

    async def test_todo_plan_artifact_stored(self, async_session: AsyncSession):
        plan = "## Plan\n1. Write tests\n2. Implement\n3. Review"
        todo = TodoModel(title="Plan test", plan_artifact=plan)
        async_session.add(todo)
        await async_session.flush()
        assert todo.plan_artifact == plan


class TestTodoEventModel:
    async def test_create_todo_event(self, async_session: AsyncSession):
        todo = TodoModel(title="Parent todo")
        async_session.add(todo)
        await async_session.flush()

        evt = TodoEventModel(
            todo_id=todo.todo_id,
            event_type="status_change",
            old_status=TodoStatus.BACKLOG,
            new_status=TodoStatus.QUEUED,
            actor="agent",
            reason="Ready to process",
        )
        async_session.add(evt)
        await async_session.flush()
        assert evt.id is not None
        assert evt.todo_id == todo.todo_id
        assert evt.old_status == TodoStatus.BACKLOG
        assert evt.new_status == TodoStatus.QUEUED


class TestTaskReturnModel:
    async def test_create_task_return(self, async_session: AsyncSession):
        tr = TaskReturnModel(
            return_id="R-001",
            todo_id="TODO-ABCD1234",
            job_id="J-001",
            playbook="noop.yml",
            queue="core",
            work_type="code",
            resource_profile="low_resource",
            status="created",
            exit_code=0,
            result_summary="All good",
            artifacts="[]",
        )
        async_session.add(tr)
        await async_session.flush()
        assert tr.id is not None
        assert tr.return_id == "R-001"
        assert tr.status == "created"


class TestTaskDecisionModel:
    async def test_create_task_decision(self, async_session: AsyncSession):
        td = TaskDecisionModel(
            return_id="R-001",
            matched_todo_id="TODO-ABCD1234",
            decision="complete",
            confidence=0.95,
            evidence_refs='["test_results.json"]',
            todo_updates='{"status":"complete"}',
            child_todos="[]",
            audit_notes='["All tests passed"]',
            policy_flags="[]",
        )
        async_session.add(td)
        await async_session.flush()
        assert td.id is not None
        assert td.decision == "complete"
        assert td.confidence == 0.95


class TestQueueModel:
    async def test_create_queue(self, async_session: AsyncSession):
        q = QueueModel(
            queue_name="core",
            queue_enabled=True,
            priority_weight=100,
            resource_profile="low_resource",
            hard_cap=10,
            soft_cap=5,
            allowed_playbooks='["noop.yml"]',
            allowed_model_profiles="[]",
            allowed_prompt_profiles="[]",
            max_error_rate=0.5,
            retry_policy="{}",
        )
        async_session.add(q)
        await async_session.flush()
        assert q.id is not None
        assert q.queue_name == "core"
        assert q.queue_enabled is True

    async def test_queue_pid_group(self, async_session: AsyncSession):
        q = QueueModel(
            queue_name="worker",
            pid_group="workers",
            resource_profile="hybrid",
            hard_cap=10,
            soft_cap=5,
            max_error_rate=0.5,
        )
        async_session.add(q)
        await async_session.flush()
        assert q.pid_group == "workers"


class TestAuditEventModel:
    async def test_create_audit_event(self, async_session: AsyncSession):
        ae = AuditEventModel(
            event_type=AuditEventType.TODO_CREATED,
            actor="agent",
            entity_type="todo",
            entity_id="TODO-ABCD1234",
            details='{"title":"test"}',
        )
        async_session.add(ae)
        await async_session.flush()
        assert ae.id is not None
        assert ae.event_type == AuditEventType.TODO_CREATED

    async def test_audit_event_with_correlation(self, async_session: AsyncSession):
        ae = AuditEventModel(
            event_type=AuditEventType.TODO_STATUS_CHANGED,
            actor="agent",
            entity_type="todo",
            entity_id="TODO-ABCD1234",
            correlation_id="corr-001",
            details="{}",
        )
        async_session.add(ae)
        await async_session.flush()
        assert ae.correlation_id == "corr-001"


class TestVariableModels:
    async def test_create_namespace(self, async_session: AsyncSession):
        ns = VariableNamespaceModel(namespace="env_vars", description="Environment variables")
        async_session.add(ns)
        await async_session.flush()
        assert ns.id is not None
        assert ns.namespace == "env_vars"

    async def test_create_variable_value(self, async_session: AsyncSession):
        ns = VariableNamespaceModel(namespace="secrets")
        async_session.add(ns)
        await async_session.flush()

        vv = VariableValueModel(
            namespace_id=ns.id,
            key="API_KEY",
            value="***redacted***",
            value_type="string",
        )
        async_session.add(vv)
        await async_session.flush()
        assert vv.id is not None
        assert vv.key == "API_KEY"
        assert vv.namespace_id == ns.id


class TestBucketLeaseModel:
    async def test_create_bucket_lease(self, async_session: AsyncSession):
        lease = BucketLeaseModel(
            bucket_key="todo:core:active",
            holder_id="worker-1",
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async_session.add(lease)
        await async_session.flush()
        assert lease.id is not None
        assert lease.bucket_key == "todo:core:active"
        assert lease.holder_id == "worker-1"


class TestTodoRepository:
    async def test_create_todo(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "New task", "queue": "core"})
        assert todo.todo_id.startswith("TODO-")
        assert todo.title == "New task"
        assert todo.status == TodoStatus.BACKLOG

    async def test_get_by_id(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Find me"})
        found = await repo.get_by_id(created.todo_id)
        assert found is not None
        assert found.todo_id == created.todo_id
        assert found.title == "Find me"

    async def test_get_by_id_not_found(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        found = await repo.get_by_id("TODO-NONEXIST")
        assert found is None

    async def test_update_todo(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Update me"})
        updated = await repo.update(created.todo_id, {"title": "Updated"}, expected_version=1)
        assert updated.title == "Updated"
        assert updated.version == 2

    async def test_update_todo_version_mismatch_raises(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Version test"})
        with pytest.raises(ConcurrencyError):
            await repo.update(created.todo_id, {"title": "Bad update"}, expected_version=999)

    async def test_list_by_status(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        await repo.create({"title": "Backlog 1"})
        await repo.create({"title": "Backlog 2"})
        created = await repo.create({"title": "Queued 1"})
        await repo.update(created.todo_id, {"status": TodoStatus.QUEUED}, expected_version=1)

        backlog = await repo.list_by_status(TodoStatus.BACKLOG)
        assert len(backlog) >= 2
        queued = await repo.list_by_status(TodoStatus.QUEUED)
        assert len(queued) >= 1

    async def test_transition_valid(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Transition test"})
        updated = await repo.transition(created.todo_id, TodoStatus.QUEUED, expected_version=1)
        assert updated.status == TodoStatus.QUEUED
        assert updated.version == 2

    async def test_transition_invalid_raises(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Bad transition"})
        with pytest.raises(InvalidTransitionError):
            await repo.transition(created.todo_id, TodoStatus.COMPLETE, expected_version=1)

    async def test_transition_version_mismatch_raises(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Version mismatch"})
        with pytest.raises(ConcurrencyError):
            await repo.transition(created.todo_id, TodoStatus.QUEUED, expected_version=999)

    async def test_claim_runnable_returns_queued(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Claimable"})
        await repo.transition(created.todo_id, TodoStatus.QUEUED, expected_version=1)
        claimed = await repo.claim_runnable(limit=10)
        assert len(claimed) >= 1
        assert claimed[0].status == TodoStatus.ACTIVE

    async def test_claim_runnable_skips_backlog(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        await repo.create({"title": "Still in backlog"})
        claimed = await repo.claim_runnable(limit=10)
        backlog_claimed = [c for c in claimed if c.title == "Still in backlog"]
        assert len(backlog_claimed) == 0


class TestTaskReturnRepository:
    async def test_create_task_return(self, async_session: AsyncSession):
        repo = TaskReturnRepository(async_session)
        tr = await repo.create({
            "return_id": "R-001",
            "job_id": "J-001",
            "playbook": "noop.yml",
            "queue": "core",
        })
        assert tr.return_id == "R-001"
        assert tr.status == "created"

    async def test_get_by_id(self, async_session: AsyncSession):
        repo = TaskReturnRepository(async_session)
        created = await repo.create({
            "return_id": "R-002",
            "job_id": "J-002",
            "playbook": "noop.yml",
            "queue": "core",
        })
        found = await repo.get_by_id(created.return_id)
        assert found is not None
        assert found.return_id == "R-002"

    async def test_claim_unreviewed(self, async_session: AsyncSession):
        repo = TaskReturnRepository(async_session)
        await repo.create({
            "return_id": "R-010",
            "job_id": "J-010",
            "playbook": "noop.yml",
            "queue": "core",
        })
        claimed = await repo.claim_unreviewed(limit=10)
        assert len(claimed) >= 1
        assert any(c.return_id == "R-010" for c in claimed)


class TestQueueRepository:
    async def test_get_by_name(self, async_session: AsyncSession):
        q = QueueModel(
            queue_name="core",
            queue_enabled=True,
            priority_weight=100,
            resource_profile="low_resource",
            hard_cap=10,
            soft_cap=5,
            max_error_rate=0.5,
        )
        async_session.add(q)
        await async_session.flush()

        repo = QueueRepository(async_session)
        found = await repo.get_by_name("core")
        assert found is not None
        assert found.queue_name == "core"

    async def test_get_by_name_not_found(self, async_session: AsyncSession):
        repo = QueueRepository(async_session)
        found = await repo.get_by_name("nonexistent")
        assert found is None

    async def test_list_enabled(self, async_session: AsyncSession):
        for name, enabled in [("core", True), ("disabled_q", False)]:
            async_session.add(QueueModel(
                queue_name=name,
                queue_enabled=enabled,
                hard_cap=10,
                soft_cap=5,
                max_error_rate=0.5,
            ))
        await async_session.flush()

        repo = QueueRepository(async_session)
        enabled_queues = await repo.list_enabled()
        names = [q.queue_name for q in enabled_queues]
        assert "core" in names
        assert "disabled_q" not in names
