"""Unit tests for RemediationDispatcher (remediation system).

The dispatcher is the write half: it acts on BlockedTask findings and persists
audit rows. Tests inject a minimal fake detector (so we do not re-test the
detector here) and assert the side effects: new todo created, scheduled task
created, human-todo filed with high priority, audit row written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, RemediationActionModel
from general_ludd.db.repository import (
    HumanTodoRepository,
    RemediationActionRepository,
    TodoRepository,
)
from general_ludd.remediation.blocker_detector import (
    BlockedTask,
    RemediationConfig,
)
from general_ludd.remediation.dispatcher import (
    RemediationActionKind,
    RemediationDispatcher,
)


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

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


@dataclass
class _FakeDetector:
    """Minimal stand-in for BlockerDetector — exposes only .config."""
    config: RemediationConfig


def _make_blocked(
    *,
    todo_id: str = "TODO-X",
    blocker_kind: str = "resource_contention",
    remediation: str = "dispatch_agent",
    task_type: str = "code",
) -> BlockedTask:
    return BlockedTask(
        todo_id=todo_id,
        project_id=None,
        blocked_at=datetime.now(UTC) - timedelta(hours=10),
        blocked_duration_seconds=36000,
        blocker_kind=blocker_kind,
        blocker_summary="test summary",
        suggested_remediation=remediation,
        task_type=task_type,
    )


class TestDispatchAgent:
    @pytest.mark.asyncio
    async def test_dispatch_agent_creates_new_todo_with_blocker_note(
        self, async_session: AsyncSession
    ):
        # Seed the original blocked todo so _dispatch_remediation_agent can
        # copy its title/work_type/description.
        todo_repo = TodoRepository(async_session)
        original = await todo_repo.create(
            {
                "todo_id": "TODO-ORIG-1",
                "title": "deploy the service",
                "description": "original",
                "queue": "core",
                "work_type": "infra",
                "status": "blocked",
            }
        )
        await async_session.flush()

        detector = _FakeDetector(config=RemediationConfig())
        dispatcher = RemediationDispatcher(
            detector=detector,
            todo_repo=todo_repo,
            human_todo_repo=HumanTodoRepository(async_session),
            remediation_repo=RemediationActionRepository(async_session),
        )
        blocked = _make_blocked(
            todo_id=original.todo_id, remediation="dispatch_agent"
        )
        action = await dispatcher.remediate(blocked)

        assert action.kind == RemediationActionKind.DISPATCH_AGENT
        assert action.ok
        new_id = action.detail.get("new_todo_id")
        assert new_id and new_id != original.todo_id
        # The new todo exists and carries the blocker note.
        new_todo = await todo_repo.get_by_id(new_id)
        assert new_todo is not None
        assert "[remediation]" in (new_todo.description or "")
        assert new_todo.parent_todo_id == original.todo_id
        # Audit row was persisted.
        from sqlalchemy import select

        rows = (
            await async_session.execute(select(RemediationActionModel))
        ).scalars().all()
        assert any(r.action_kind == "dispatch_agent" for r in rows)


class TestScheduleRetry:
    @pytest.mark.asyncio
    async def test_schedule_retry_creates_scheduled_task(
        self, async_session: AsyncSession
    ):
        todo_repo = TodoRepository(async_session)
        detector = _FakeDetector(
            config=RemediationConfig(retry_delay_hours=4)
        )
        dispatcher = RemediationDispatcher(
            detector=detector,
            todo_repo=todo_repo,
            human_todo_repo=HumanTodoRepository(async_session),
            remediation_repo=RemediationActionRepository(async_session),
        )
        blocked = _make_blocked(
            todo_id="TODO-R-1", remediation="schedule_retry"
        )
        action = await dispatcher.remediate(blocked)

        assert action.kind == RemediationActionKind.SCHEDULE_RETRY
        assert action.ok
        new_id = action.detail.get("scheduled_todo_id")
        assert new_id
        scheduled = await todo_repo.get_by_id(new_id)
        assert scheduled is not None
        assert scheduled.status == "scheduled"
        # scheduled_at should be ~4h from now.
        assert scheduled.scheduled_at is not None
        delta = scheduled.scheduled_at.replace(tzinfo=UTC) - datetime.now(UTC)
        assert timedelta(hours=3, minutes=55) <= delta <= timedelta(hours=4, minutes=5)


class TestFileHumanTodo:
    @pytest.mark.asyncio
    async def test_file_blocker_human_todo_uses_high_priority(
        self, async_session: AsyncSession
    ):
        human_repo = HumanTodoRepository(async_session)
        detector = _FakeDetector(config=RemediationConfig())
        dispatcher = RemediationDispatcher(
            detector=detector,
            todo_repo=TodoRepository(async_session),
            human_todo_repo=human_repo,
            remediation_repo=RemediationActionRepository(async_session),
        )
        blocked = _make_blocked(
            todo_id="TODO-H-1",
            blocker_kind="human_input",
            remediation="file_human_todo",
        )
        action = await dispatcher.remediate(blocked)

        assert action.kind == RemediationActionKind.FILE_HUMAN_TODO
        assert action.ok
        ht_id = action.detail.get("human_todo_id")
        assert ht_id
        ht = await human_repo.get(ht_id)
        assert ht is not None
        assert ht.priority == "high"
        assert "remediation" in (ht.title or "")
