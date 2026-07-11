"""Integration tests for D9 (#52) — auto-remediation tick end-to-end.

Validates the full chain: blocked todo → tick → BlockerDetector.scan →
RemediationDispatcher.remediate → RemediationActionModel audit row.

Also covers the "never fires" case: a blocked todo exists but the
remediation interval has not elapsed, so no action is taken.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, RemediationActionModel, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import TodoStatus


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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


def _make_blocked_todo_attrs(**overrides):
    return {
        "todo_id": "TODO-BLOCK-1",
        "title": "repeatedly failing task",
        "queue": "core",
        "work_type": "code",
        "status": TodoStatus.BLOCKED.value,
        "run_count": 5,
        **overrides,
    }


class TestRemediationTickEndToEnd:
    @pytest.mark.asyncio
    async def test_chronic_requeue_yields_audit_row_after_tick(
        self, async_session: AsyncSession
    ):
        """Seeds a chronically re-queued BLOCKED todo (run_count > threshold).

        After one qualifying tick (remediation_check_interval_ticks=1), the
        phase scans, dispatches, and persists a RemediationActionModel row.
        """
        todo_repo = TodoRepository(async_session)
        await todo_repo.create(_make_blocked_todo_attrs(run_count=5))
        await async_session.commit()

        loop = EventLoop(
            session=async_session,
            todo_repo=todo_repo,
            config={
                "remediation_check_interval_ticks": 1,
                "remediation_max_actions_per_tick": 5,
            },
            daemon_state={},
        )
        loop._total_ticks = 1

        await loop._phase_remediate_blocked_tasks()
        await async_session.commit()

        rows = (
            (await async_session.execute(select(RemediationActionModel)))
            .scalars()
            .all()
        )
        assert len(rows) >= 1, (
            "expected ≥1 RemediationActionModel row after "
            "remediation phase scanned the chronic re-queue"
        )
        matching = [r for r in rows if r.blocked_todo_id == "TODO-BLOCK-1"]
        assert len(matching) == 1
        assert matching[0].ok is True

    @pytest.mark.asyncio
    async def test_child_todo_created_for_dispatch_agent(
        self, async_session: AsyncSession
    ):
        """A chronic re-queue finding triggers dispatch_agent remediation,
        which clones the blocked todo as a QUEUED child."""
        todo_repo = TodoRepository(async_session)
        await todo_repo.create(_make_blocked_todo_attrs(run_count=5))
        await async_session.commit()

        loop = EventLoop(
            session=async_session,
            todo_repo=todo_repo,
            config={
                "remediation_check_interval_ticks": 1,
                "remediation_max_actions_per_tick": 5,
            },
            daemon_state={},
        )
        loop._total_ticks = 1

        await loop._phase_remediate_blocked_tasks()
        await async_session.commit()

        child_todos = (
            (
                await async_session.execute(
                    select(TodoModel).where(
                        TodoModel.parent_todo_id == "TODO-BLOCK-1"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(child_todos) >= 1, (
            "expected a QUEUED child todo spawned by dispatch_agent "
            "remediation for the chronic re-queue"
        )
        assert child_todos[0].status == TodoStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_off_interval_never_fires(
        self, async_session: AsyncSession
    ):
        """A blocked todo exists but the interval has not elapsed — no audit
        row is created."""
        todo_repo = TodoRepository(async_session)
        await todo_repo.create(_make_blocked_todo_attrs(run_count=5))
        await async_session.commit()

        loop = EventLoop(
            session=async_session,
            todo_repo=todo_repo,
            config={
                "remediation_check_interval_ticks": 30,
                "remediation_max_actions_per_tick": 5,
            },
            daemon_state={},
        )
        loop._total_ticks = 7

        await loop._phase_remediate_blocked_tasks()
        await async_session.commit()

        rows = (
            (await async_session.execute(select(RemediationActionModel)))
            .scalars()
            .all()
        )
        assert len(rows) == 0, (
            "expected zero RemediationActionModel rows when tick is "
            "off-interval (7 % 30 != 0)"
        )

    @pytest.mark.asyncio
    async def test_todo_below_chronic_threshold_not_flagged(
        self, async_session: AsyncSession
    ):
        """A todo with run_count below the chronic threshold is not flagged."""
        todo_repo = TodoRepository(async_session)
        await todo_repo.create(_make_blocked_todo_attrs(run_count=2))
        await async_session.commit()

        loop = EventLoop(
            session=async_session,
            todo_repo=todo_repo,
            config={
                "remediation_check_interval_ticks": 1,
                "remediation_max_actions_per_tick": 5,
            },
            daemon_state={},
        )
        loop._total_ticks = 1

        await loop._phase_remediate_blocked_tasks()
        await async_session.commit()

        rows = (
            (await async_session.execute(select(RemediationActionModel)))
            .scalars()
            .all()
        )
        assert len(rows) == 0, (
            "expected zero actions — run_count=2 is below "
            "max_requeues_before_chronic=3 (default)"
        )

    @pytest.mark.asyncio
    async def test_multiple_ticks_suppress_duplicate_actions(
        self, async_session: AsyncSession
    ):
        """Two qualifying ticks with the same still-blocked chronic todo
        produce only one remediation action (idempotency)."""
        todo_repo = TodoRepository(async_session)
        await todo_repo.create(_make_blocked_todo_attrs(run_count=5))
        await async_session.commit()

        loop = EventLoop(
            session=async_session,
            todo_repo=todo_repo,
            config={
                "remediation_check_interval_ticks": 1,
                "remediation_max_actions_per_tick": 5,
            },
            daemon_state={},
        )

        loop._total_ticks = 1
        await loop._phase_remediate_blocked_tasks()
        await async_session.commit()

        loop._total_ticks = 2
        await loop._phase_remediate_blocked_tasks()
        await async_session.commit()

        rows = (
            (await async_session.execute(select(RemediationActionModel)))
            .scalars()
            .all()
        )
        matching = [r for r in rows if r.blocked_todo_id == "TODO-BLOCK-1"]
        assert len(matching) == 1, (
            f"expected 1 action for chronic todo across 2 ticks, "
            f"got {len(matching)} — idempotency breach"
        )
