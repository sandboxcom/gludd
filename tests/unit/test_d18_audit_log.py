"""SEC.1 D-18: Audit log for sensitive operations — behavioral tests.

Covers:
- AuditEventType enum values and uniqueness
- AuditEventRepository.record_typed writes typed events
- project_id is required (NULL reject)
- Events can be queried by entity and project
- record_typed delegates to create with correct JSON serialization
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import AuditEventModel, AuditEventType, Base, ProjectModel
from general_ludd.db.repository import AuditEventRepository


@pytest_asyncio.fixture
async def _engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(_engine):
    factory = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


async def _seed_project(session: AsyncSession, name: str) -> ProjectModel:
    project = ProjectModel(name=name)
    session.add(project)
    await session.flush()
    return project


class TestAuditEventTypeEnum:
    def test_enum_values_are_unique(self) -> None:
        values = [v.value for v in AuditEventType]
        assert len(set(values)) == len(values)

    def test_required_event_types_exist(self) -> None:
        expected = {
            "todo_created",
            "todo_status_changed",
            "todo_updated",
            "todo_deleted",
            "task_return_created",
            "task_return_claimed",
            "task_decision_made",
            "queue_updated",
            "bucket_lease_acquired",
            "bucket_lease_released",
        }
        actual = {e.value for e in AuditEventType}
        assert expected == actual

    def test_enum_is_str_enum(self) -> None:
        assert AuditEventType.TODO_CREATED.value == "todo_created"
        assert str(AuditEventType.TODO_CREATED) == "todo_created"


@pytest.mark.asyncio
class TestAuditEventRepositoryBasic:
    async def test_record_typed_writes_event(self, async_session: AsyncSession) -> None:
        await _seed_project(async_session, "proj-d18-1")
        repo = AuditEventRepository(async_session)
        event = await repo.record_typed(
            event_type=AuditEventType.TODO_CREATED,
            entity_type="todo",
            entity_id="todo-001",
            project_id="proj-d18-1",
            details={"key": "value"},
        )
        assert event.event_type == "todo_created"
        assert event.entity_type == "todo"
        assert event.entity_id == "todo-001"
        assert event.project_id == "proj-d18-1"
        details = json.loads(event.details)
        assert details["key"] == "value"

    async def test_project_id_none_rejected(self, async_session: AsyncSession) -> None:
        repo = AuditEventRepository(async_session)
        with pytest.raises(ValueError, match="project_id"):
            await repo.record_typed(
                event_type=AuditEventType.TODO_CREATED,
                entity_type="todo",
                entity_id="todo-001",
                project_id=None,
            )

    async def test_record_typed_without_details_writes_empty_json(self, async_session: AsyncSession) -> None:
        await _seed_project(async_session, "proj-d18-2")
        repo = AuditEventRepository(async_session)
        event = await repo.record_typed(
            event_type=AuditEventType.QUEUE_UPDATED,
            entity_type="queue",
            entity_id="q-001",
            project_id="proj-d18-2",
        )
        assert json.loads(event.details) == {}

    async def test_record_typed_all_event_types(self, async_session: AsyncSession) -> None:
        await _seed_project(async_session, "proj-d18-all")
        repo = AuditEventRepository(async_session)
        for etype in AuditEventType:
            event = await repo.record_typed(
                event_type=etype,
                entity_type="test",
                entity_id="test-001",
                project_id="proj-d18-all",
            )
            assert event.event_type == etype.value

    async def test_list_by_entity_finds_events(self, async_session: AsyncSession) -> None:
        await _seed_project(async_session, "proj-d18-list")
        repo = AuditEventRepository(async_session)
        await repo.record_typed(
            event_type=AuditEventType.TODO_CREATED,
            entity_type="todo",
            entity_id="todo-a",
            project_id="proj-d18-list",
        )
        await repo.record_typed(
            event_type=AuditEventType.TODO_STATUS_CHANGED,
            entity_type="todo",
            entity_id="todo-a",
            project_id="proj-d18-list",
        )
        events = await repo.list_by_entity("todo", "todo-a")
        assert len(events) == 2
        assert all(e.entity_id == "todo-a" for e in events)

    async def test_list_by_project_finds_events(self, async_session: AsyncSession) -> None:
        await _seed_project(async_session, "proj-d18-z")
        repo = AuditEventRepository(async_session)
        await repo.record_typed(
            event_type=AuditEventType.TASK_RETURN_CREATED,
            entity_type="task",
            entity_id="task-1",
            project_id="proj-d18-z",
        )
        events = await repo.list_by_project("proj-d18-z")
        assert len(events) >= 1
        assert all(e.project_id == "proj-d18-z" for e in events)

    async def test_list_by_entity_respects_limit(self, async_session: AsyncSession) -> None:
        await _seed_project(async_session, "proj-d18-limit")
        repo = AuditEventRepository(async_session)
        for _ in range(5):
            await repo.record_typed(
                event_type=AuditEventType.BUCKET_LEASE_ACQUIRED,
                entity_type="bucket",
                entity_id="bkt-1",
                project_id="proj-d18-limit",
            )
        events = await repo.list_by_entity("bucket", "bkt-1", limit=3)
        assert len(events) == 3

    async def test_events_ordered_by_created_at_desc(self, async_session: AsyncSession) -> None:
        await _seed_project(async_session, "proj-d18-order")
        repo = AuditEventRepository(async_session)
        await repo.record_typed(
            event_type=AuditEventType.TODO_CREATED,
            entity_type="todo",
            entity_id="todo-order",
            project_id="proj-d18-order",
        )
        await repo.record_typed(
            event_type=AuditEventType.TODO_DELETED,
            entity_type="todo",
            entity_id="todo-order",
            project_id="proj-d18-order",
        )
        events = await repo.list_by_entity("todo", "todo-order")
        assert len(events) >= 2
        assert events[0].created_at >= events[1].created_at


class TestAuditEventModelConstraints:
    def test_model_has_required_fields(self) -> None:
        assert hasattr(AuditEventModel, "event_type")
        assert hasattr(AuditEventModel, "entity_type")
        assert hasattr(AuditEventModel, "entity_id")
        assert hasattr(AuditEventModel, "project_id")
        assert hasattr(AuditEventModel, "details")
        assert hasattr(AuditEventModel, "created_at")
