"""Unit tests for the blocker detector (remediation system).

Uses SQLite in-memory with the live TodoModel + HumanTodoModel + TodoEventModel
tables so the detector's SQL queries are exercised against real schemas. The
detector is the read half of the system — these tests cover scan() signal
sources, classification, and chronic grouping.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, HumanTodoModel, TodoEventModel, TodoModel
from general_ludd.db.repository import HumanTodoRepository, TodoRepository
from general_ludd.remediation.blocker_detector import (
    BlockerDetector,
    RemediationConfig,
)
from general_ludd.schemas.todo import TodoStatus


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


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_blocked_todo(
    session: AsyncSession,
    *,
    todo_id: str = "TODO-BLOCKED-1",
    work_type: str = "code",
    status: TodoStatus = TodoStatus.BLOCKED_ON_HUMAN,
    updated_at: datetime | None = None,
    run_count: int = 0,
    project_id: str | None = None,
) -> TodoModel:
    todo = TodoModel(
        todo_id=todo_id,
        title="blocked task",
        status=status.value,
        work_type=work_type,
        queue="core",
        project_id=project_id,
        run_count=run_count,
    )
    if updated_at is not None:
        todo.updated_at = updated_at
    session.add(todo)
    await session.flush()
    return todo


async def _make_human_todo(
    session: AsyncSession,
    *,
    parent_agent_todo_id: str | None = None,
    category: str = "input_request",
    priority: str = "medium",
    created_at: datetime | None = None,
    title: str = "Need input",
) -> HumanTodoModel:
    repo = HumanTodoRepository(session)
    row = await repo.create(
        agent_id="agent-1",
        title=title,
        body="some body",
        category=category,
        priority=priority,
        parent_agent_todo_id=parent_agent_todo_id,
    )
    if created_at is not None:
        row.created_at = created_at
        row.updated_at = created_at
    await session.flush()
    return row


class TestScan:
    @pytest.mark.asyncio
    async def test_scan_finds_todos_blocked_over_threshold(
        self, async_session: AsyncSession
    ):
        """BLOCKED_ON_HUMAN todo older than 24h surfaces in scan()."""
        old = _now() - timedelta(hours=30)
        await _make_blocked_todo(async_session, todo_id="OLD-1", updated_at=old)
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan()
        ids = [f.todo_id for f in findings]
        assert "OLD-1" in ids

    @pytest.mark.asyncio
    async def test_scan_ignores_recently_blocked_todos(
        self, async_session: AsyncSession
    ):
        """A todo blocked 1 minute ago must NOT surface."""
        await _make_blocked_todo(
            async_session, todo_id="NEW-1", updated_at=_now() - timedelta(minutes=1)
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = await detector.scan()
        assert all(f.todo_id != "NEW-1" for f in findings)

    @pytest.mark.asyncio
    async def test_scan_classifies_blocker_kind_from_linked_human_todo_category(
        self, async_session: AsyncSession
    ):
        """Linked human-todo category drives the blocker_kind."""
        old = _now() - timedelta(hours=30)
        todo = await _make_blocked_todo(
            async_session, todo_id="CLZ-1", updated_at=old
        )
        await _make_human_todo(
            async_session,
            parent_agent_todo_id=todo.todo_id,
            category="permission_escalation",
            created_at=old,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(
                human_input_block_hours=24,
                permission_escalation_block_hours=4,
            ),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CLZ-1"]
        assert findings, "expected CLZ-1 to surface"
        assert findings[0].blocker_kind == "permission_escalation"
        assert findings[0].suggested_remediation == "schedule_retry"


class TestChronic:
    @pytest.mark.asyncio
    async def test_chronic_blockers_groups_by_task_type_and_kind(
        self, async_session: AsyncSession
    ):
        """Multiple BLOCKED_ON_HUMAN events for the same (task_type, kind) bucket."""
        now = _now()
        # 6 events for task_type=code, kind=permission_escalation (via reason)
        for i in range(6):
            t = TodoModel(
                todo_id=f"CHRONIC-{i}",
                title="chronic task",
                status=TodoStatus.QUEUED.value,
                work_type="code",
                queue="core",
            )
            session_t = now - timedelta(days=1, hours=-i)
            async_session.add(t)
            await async_session.flush()
            ev = TodoEventModel(
                todo_id=t.todo_id,
                event_type="status_changed",
                new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                old_status=TodoStatus.QUEUED.value,
                reason="permission denied",
                created_at=session_t,
            )
            async_session.add(ev)
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5, chronic_lookback_days=7),
        )
        chronic = await detector.chronic_blockers()
        assert len(chronic) == 1
        assert chronic[0].task_type == "code"
        assert chronic[0].blocker_kind == "permission_escalation"
        assert chronic[0].incident_count == 6

    @pytest.mark.asyncio
    async def test_chronic_blockers_respects_min_incidents_threshold(
        self, async_session: AsyncSession
    ):
        """A pair with fewer incidents than the threshold is NOT surfaced."""
        now = _now()
        for i in range(3):  # below threshold of 5
            t = TodoModel(
                todo_id=f"RARE-{i}",
                title="rare task",
                status=TodoStatus.QUEUED.value,
                work_type="docs",
                queue="core",
            )
            async_session.add(t)
            await async_session.flush()
            async_session.add(
                TodoEventModel(
                    todo_id=t.todo_id,
                    event_type="status_changed",
                    new_status=TodoStatus.BLOCKED_ON_HUMAN.value,
                    old_status=TodoStatus.QUEUED.value,
                    reason="input needed",
                    created_at=now - timedelta(hours=i),
                )
            )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(min_chronic_incidents=5),
        )
        chronic = await detector.chronic_blockers()
        assert chronic == []


class TestSuggestedRemediation:
    @pytest.mark.asyncio
    async def test_suggested_remediation_dispatch_agent_for_resource_contention(
        self, async_session: AsyncSession
    ):
        """A chronic re-queue (no linked human-todo) ⇒ resource_contention ⇒ dispatch_agent."""
        # A live todo re-queued 4 times (threshold 3) with NO linked human-todo.
        await _make_blocked_todo(
            async_session,
            todo_id="CHRONIC-RQ-1",
            status=TodoStatus.QUEUED,
            run_count=4,
            work_type="infra",
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        findings = [f for f in await detector.scan() if f.todo_id == "CHRONIC-RQ-1"]
        assert findings, "expected chronic re-queue to surface"
        assert findings[0].blocker_kind == "resource_contention"
        assert findings[0].suggested_remediation == "dispatch_agent"

    @pytest.mark.asyncio
    async def test_suggested_remediation_file_human_todo_for_input_request(
        self, async_session: AsyncSession
    ):
        """Stale human-todo of category=input_request ⇒ file_human_todo."""
        old = _now() - timedelta(hours=30)
        # No parent agent todo — synthetic HTODO:<id> form
        await _make_human_todo(
            async_session,
            parent_agent_todo_id=None,
            category="input_request",
            created_at=old,
            title="Need prod key",
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(human_input_block_hours=24),
        )
        findings = [
            f for f in await detector.scan() if f.todo_id.startswith("HTODO:")
        ]
        assert findings, "expected stale human-todo to surface"
        assert findings[0].suggested_remediation == "file_human_todo"

    @pytest.mark.asyncio
    async def test_suggested_remediation_schedule_retry_for_permission_escalation(
        self, async_session: AsyncSession
    ):
        """Stale human-todo of category=permission_escalation ⇒ schedule_retry."""
        old = _now() - timedelta(hours=6)
        await _make_human_todo(
            async_session,
            parent_agent_todo_id=None,
            category="permission_escalation",
            created_at=old,
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=TodoRepository(async_session),
            human_todo_repo=HumanTodoRepository(async_session),
            session=async_session,
            config=RemediationConfig(permission_escalation_block_hours=4),
        )
        findings = [
            f
            for f in await detector.scan()
            if f.todo_id.startswith("HTODO:")
            and f.blocker_kind == "permission_escalation"
        ]
        assert findings, "expected permission escalation to surface after 4h"
        assert findings[0].suggested_remediation == "schedule_retry"
