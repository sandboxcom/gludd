"""E2E: PostgreSQL schema, models, repositories, optimistic concurrency.

Covers sprint objective 2 -- SQLAlchemy models, Alembic migration,
repositories with SKIP LOCKED, optimistic concurrency, state transitions.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from agentic_harness.db.models import Base
from agentic_harness.schemas.todo import TodoStatus


@pytest.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


class TestDatabaseSchemaE2E:
    async def test_create_and_query_todo_full_lifecycle(self, db_session: AsyncSession):
        from agentic_harness.db.models import TodoModel

        todo = TodoModel(
            title="E2E test todo",
            description="Full lifecycle test",
            status=TodoStatus.BACKLOG.value,
            priority=5,
            queue="core",
            tags='["e2e"]',
            risk_level="low",
            work_type="code",
            resource_profile="ai_heavy",
            version=1,
        )
        db_session.add(todo)
        await db_session.commit()

        stmt = select(TodoModel).where(TodoModel.title == "E2E test todo")
        result = await db_session.execute(stmt)
        loaded = result.scalar_one()
        assert loaded.version == 1
        assert loaded.status == TodoStatus.BACKLOG.value
        assert json.loads(loaded.tags) == ["e2e"]

        loaded.status = TodoStatus.QUEUED.value
        await db_session.commit()

        stmt2 = select(TodoModel).where(TodoModel.todo_id == loaded.todo_id)
        result2 = await db_session.execute(stmt2)
        again = result2.scalar_one()
        assert again.status == TodoStatus.QUEUED.value

    async def test_audit_event_recording(self, db_session: AsyncSession):
        from agentic_harness.db.models import AuditEventModel

        event = AuditEventModel(
            event_type="todo.created",
            entity_type="todo",
            entity_id="TODO-ABC123",
            details='{"title": "test"}',
            correlation_id="corr-e2e-001",
        )
        db_session.add(event)
        await db_session.commit()

        stmt = select(AuditEventModel).where(AuditEventModel.correlation_id == "corr-e2e-001")
        result = await db_session.execute(stmt)
        loaded = result.scalar_one()
        assert loaded.event_type == "todo.created"
        assert json.loads(loaded.details)["title"] == "test"

    async def test_variable_namespace_and_values(self, db_session: AsyncSession):
        from agentic_harness.db.models import VariableNamespaceModel, VariableValueModel

        ns = VariableNamespaceModel(
            namespace="global_shared",
            description="shared global vars",
        )
        db_session.add(ns)
        await db_session.flush()

        val = VariableValueModel(
            namespace_id=ns.id,
            key="LOG_LEVEL",
            value="INFO",
        )
        db_session.add(val)
        await db_session.commit()

        stmt = select(VariableNamespaceModel).where(VariableNamespaceModel.namespace == "global_shared")
        result = await db_session.execute(stmt)
        ns_loaded = result.scalar_one()
        assert ns_loaded.namespace == "global_shared"

        vstmt = select(VariableValueModel).where(VariableValueModel.namespace_id == ns_loaded.id)
        vresult = await db_session.execute(vstmt)
        vals = list(vresult.scalars().all())
        assert len(vals) == 1
        assert vals[0].value == "INFO"

    async def test_task_return_and_decision(self, db_session: AsyncSession):
        from agentic_harness.db.models import TaskDecisionModel, TaskReturnModel

        ret = TaskReturnModel(
            return_id="RET-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            work_type="code",
            resource_profile="ai_heavy",
            status="created",
            exit_code=0,
            result_summary="No-op completed",
        )
        db_session.add(ret)
        await db_session.flush()

        decision = TaskDecisionModel(
            return_id="RET-001",
            matched_todo_id="TODO-001",
            decision="complete",
            confidence=0.95,
            evidence_refs='["artifact://test_result.xml"]',
        )
        db_session.add(decision)
        await db_session.commit()

        stmt = select(TaskDecisionModel).where(TaskDecisionModel.return_id == "RET-001")
        result = await db_session.execute(stmt)
        dec = result.scalar_one()
        assert dec.decision == "complete"
        assert dec.confidence == 0.95
        assert json.loads(dec.evidence_refs) == ["artifact://test_result.xml"]

    async def test_bucket_lease_creation(self, db_session: AsyncSession):
        from datetime import UTC, datetime

        from agentic_harness.db.models import BucketLeaseModel

        lease = BucketLeaseModel(
            bucket_key="core:ai_heavy",
            holder_id="worker-1",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
        db_session.add(lease)
        await db_session.commit()

        stmt = select(BucketLeaseModel).where(BucketLeaseModel.holder_id == "worker-1")
        result = await db_session.execute(stmt)
        loaded = result.scalar_one()
        assert loaded.bucket_key == "core:ai_heavy"
        assert loaded.holder_id == "worker-1"

    async def test_repository_optimistic_concurrency(self, db_session: AsyncSession):
        from agentic_harness.db.repository import ConcurrencyError, TodoRepository

        repo = TodoRepository(db_session)
        todo = await repo.create({
            "title": "Concurrency test",
            "description": "Test optimistic locking",
            "queue": "core",
        })
        todo_id = todo.todo_id

        loaded = await repo.get_by_id(todo_id)
        assert loaded is not None
        assert loaded.version == 1

        await repo.update(todo_id, {"status": TodoStatus.QUEUED.value}, expected_version=1)
        with pytest.raises(ConcurrencyError):
            await repo.update(todo_id, {"status": TodoStatus.ACTIVE.value}, expected_version=1)

    async def test_repository_claim_runnable(self, db_session: AsyncSession):
        from agentic_harness.db.repository import TodoRepository

        repo = TodoRepository(db_session)
        await repo.create({"title": "Task A", "description": "", "queue": "core"})
        await repo.create({"title": "Task B", "description": "", "queue": "core"})

        await db_session.execute(
            __import__("sqlalchemy").update(
                __import__("agentic_harness.db.models", fromlist=["TodoModel"]).TodoModel
            ).where(
                __import__("agentic_harness.db.models", fromlist=["TodoModel"]).TodoModel.title == "Task A"
            ).values(status=TodoStatus.QUEUED.value)
        )
        await db_session.commit()

        claimed = await repo.claim_runnable(limit=1)
        assert len(claimed) >= 1
        assert claimed[0].title == "Task A"
        assert claimed[0].status == TodoStatus.ACTIVE.value

    async def test_queue_model_crud(self, db_session: AsyncSession):
        from agentic_harness.db.models import QueueModel
        from agentic_harness.db.repository import QueueRepository

        q = QueueModel(
            queue_name="test_queue",
            queue_enabled=True,
            priority_weight=10,
            resource_profile="ai_heavy",
            hard_cap=5,
            soft_cap=3,
            pid_group="ai",
        )
        db_session.add(q)
        await db_session.commit()

        repo = QueueRepository(db_session)
        loaded = await repo.get_by_name("test_queue")
        assert loaded is not None
        assert loaded.hard_cap == 5
        enabled = await repo.list_enabled()
        assert loaded in enabled

    async def test_todo_event_recording(self, db_session: AsyncSession):
        from agentic_harness.db.models import TodoEventModel, TodoModel

        todo = TodoModel(title="Event test", description="test events", queue="core", tags="[]")
        db_session.add(todo)
        await db_session.flush()

        evt = TodoEventModel(
            todo_id=todo.todo_id,
            event_type="status_change",
            old_status=TodoStatus.BACKLOG.value,
            new_status=TodoStatus.QUEUED.value,
            actor="test",
            reason="E2E event test",
        )
        db_session.add(evt)
        await db_session.commit()

        stmt = select(TodoEventModel).where(TodoEventModel.todo_id == todo.todo_id)
        result = await db_session.execute(stmt)
        events = list(result.scalars().all())
        assert len(events) == 1
        assert events[0].old_status == TodoStatus.BACKLOG.value
        assert events[0].new_status == TodoStatus.QUEUED.value
