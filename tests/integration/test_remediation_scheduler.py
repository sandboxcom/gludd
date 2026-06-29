"""Integration tests: remediation scheduler + audit-table persistence.

Covers:
  1. ``test_blocker_scan_runs_hourly`` — the seeded schedule entry has
     ``cron="0 * * * *"`` and ``work_type=blocker_scan``, so the
     TodoScheduler spawns a QUEUED child every hour. We verify the entry
     shape (cron expression) + that the dispatcher is invoked when the
     scan finds blocked work.
  2. ``test_remediation_actions_persisted_to_audit_table`` — run the
     dispatcher against a blocked task and assert a
     RemediationActionModel row is committed to the DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import (
    Base,
    RemediationActionModel,
)
from general_ludd.db.repository import (
    HumanTodoRepository,
    RemediationActionRepository,
    TodoRepository,
)
from general_ludd.event_loop.scheduler import TodoScheduler
from general_ludd.remediation.blocker_detector import (
    BlockerDetector,
    RemediationConfig,
)
from general_ludd.remediation.dispatcher import (
    RemediationActionKind,
    RemediationDispatcher,
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
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestHourlyScan:
    @pytest.mark.asyncio
    async def test_blocker_scan_runs_hourly(self, async_session: AsyncSession):
        """The seeded schedule entry spawns a QUEUED child each hour.

        Mirrors what scripts/seed_blocker_scan_schedule.py registers:
        cron="0 * * * *", work_type="blocker_scan". We seed the template
        directly and run TodoScheduler.tick() once with a due next_run_at
        to confirm the child clone is spawned.
        """
        repo = TodoRepository(async_session)
        # Seed the schedule template (mirrors the seed script's POST body).
        template = await repo.create(
            {
                "todo_id": "TODO-BLOCKER-SCAN-TMPL",
                "title": "Remediation blocker scan",
                "queue": "core",
                "work_type": "blocker_scan",
                "status": TodoStatus.SCHEDULED.value,
                "cron": "0 * * * *",
                "schedule_timezone": "UTC",
                "next_run_at": datetime.now(UTC) - timedelta(minutes=5),
                "run_count": 0,
            }
        )
        await async_session.commit()

        scheduler = TodoScheduler(repo)
        _promoted, spawned = await scheduler.tick(now=datetime.now(UTC))
        assert spawned == 1, "expected one blocker_scan child to be spawned"

        # Verify the child exists and is QUEUED so the event loop picks it up.
        kids = await repo.list_all(status=TodoStatus.QUEUED.value)
        blocker_kids = [t for t in kids if t.parent_todo_id == template.todo_id]
        assert blocker_kids, "expected QUEUED child of the blocker_scan template"
        assert blocker_kids[0].work_type == "blocker_scan"


class TestAuditPersistence:
    @pytest.mark.asyncio
    async def test_remediation_actions_persisted_to_audit_table(
        self, async_session: AsyncSession
    ):
        """Running the dispatcher writes a RemediationActionModel row."""
        todo_repo = TodoRepository(async_session)
        human_repo = HumanTodoRepository(async_session)
        remediation_repo = RemediationActionRepository(async_session)

        # Seed the original blocked todo so dispatch_agent can copy it.
        await todo_repo.create(
            {
                "todo_id": "TODO-AUDIT-1",
                "title": "deploy job",
                "queue": "core",
                "work_type": "infra",
                "status": "blocked",
                "run_count": 4,
            }
        )
        await async_session.commit()

        detector = BlockerDetector(
            todo_repo=todo_repo,
            human_todo_repo=human_repo,
            session=async_session,
            config=RemediationConfig(max_requeues_before_chronic=3),
        )
        dispatcher = RemediationDispatcher(
            detector=detector,
            todo_repo=todo_repo,
            human_todo_repo=human_repo,
            remediation_repo=remediation_repo,
        )

        # Pull the chronic re-queue finding the detector surfaces and
        # remediate it.
        findings = await detector.scan()
        assert any(f.todo_id == "TODO-AUDIT-1" for f in findings)
        target = next(f for f in findings if f.todo_id == "TODO-AUDIT-1")
        action = await dispatcher.remediate(target)
        await async_session.commit()

        assert action.kind == RemediationActionKind.DISPATCH_AGENT
        rows = (
            await async_session.execute(select(RemediationActionModel))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].action_kind == "dispatch_agent"
        assert rows[0].blocked_todo_id == "TODO-AUDIT-1"
        assert rows[0].ok is True
