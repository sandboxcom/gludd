"""Integration tests for TodoScheduler against a real SQLite database.

These tests exercise the full production stack: a real AsyncSession, a real
TodoRepository, and a real TodoScheduler.tick() — no mocks. They are the
end-to-end proof for review DEFECT 0 (the scheduler was previously unwired and
non-functional): a due SCHEDULED row really transitions to QUEUED / spawns a
QUEUED child against a real database.

The fixture pattern is taken from tests/security/test_db_redteam.py
(file-backed SQLite so multiple AsyncSession instances share the same on-disk
database; in-memory SQLite is NOT used because each aiosqlite connection gets a
private in-memory DB).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from general_ludd.db.models import Base, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.scheduler import TodoScheduler
from general_ludd.schemas.todo import TodoStatus


def _make_engine(db_path: Path):
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}")


@pytest_asyncio.fixture
async def factory(tmp_path):
    """async_sessionmaker over a fresh file-based SQLite DB (function-scoped)."""
    engine = _make_engine(tmp_path / "scheduler_integration.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_one_shot(
    factory,
    todo_id: str,
    scheduled_at: datetime,
    *,
    paused: bool = False,
) -> None:
    async with factory() as s:
        s.add(
            TodoModel(
                todo_id=todo_id,
                title=f"one-shot {todo_id}",
                status=TodoStatus.SCHEDULED.value,
                scheduled_at=scheduled_at,
                cron=None,
                schedule_paused=paused,
                queue="core",
                version=1,
            )
        )
        await s.commit()


async def _seed_cron_template(
    factory,
    todo_id: str,
    next_run_at: datetime,
    *,
    run_count: int = 0,
    max_runs: int | None = None,
) -> None:
    async with factory() as s:
        s.add(
            TodoModel(
                todo_id=todo_id,
                title=f"cron-tmpl {todo_id}",
                status=TodoStatus.SCHEDULED.value,
                cron="* * * * *",
                schedule_timezone="UTC",
                next_run_at=next_run_at,
                schedule_paused=False,
                run_count=run_count,
                max_runs=max_runs,
                queue="core",
                version=1,
            )
        )
        await s.commit()


class TestOneShotSchedulerIntegration:
    async def test_due_one_shot_is_promoted_to_queued(self, factory) -> None:
        todo_id = "TODO-SINT0001"
        due_at = datetime.now(UTC) - timedelta(minutes=10)
        await _seed_one_shot(factory, todo_id, scheduled_at=due_at)

        now = datetime.now(UTC)
        async with factory() as s:
            promoted, spawned = await TodoScheduler(repo=TodoRepository(s)).tick(now)
            await s.commit()

        assert promoted == 1
        assert spawned == 0

        async with factory() as s:
            row = await TodoRepository(s).get_by_id(todo_id)
        assert row is not None
        assert row.status == TodoStatus.QUEUED.value
        assert row.version == 2  # transition() bumped it

    async def test_future_one_shot_stays_scheduled(self, factory) -> None:
        todo_id = "TODO-SINT0002"
        due_at = datetime.now(UTC) + timedelta(hours=1)
        await _seed_one_shot(factory, todo_id, scheduled_at=due_at)

        now = datetime.now(UTC)
        async with factory() as s:
            promoted, spawned = await TodoScheduler(repo=TodoRepository(s)).tick(now)
            await s.commit()

        assert promoted == 0
        assert spawned == 0

        async with factory() as s:
            row = await TodoRepository(s).get_by_id(todo_id)
        assert row is not None
        assert row.status == TodoStatus.SCHEDULED.value
        assert row.version == 1

    async def test_paused_one_shot_stays_scheduled(self, factory) -> None:
        todo_id = "TODO-SINT0006"
        due_at = datetime.now(UTC) - timedelta(minutes=5)
        await _seed_one_shot(factory, todo_id, scheduled_at=due_at, paused=True)

        now = datetime.now(UTC)
        async with factory() as s:
            promoted, spawned = await TodoScheduler(repo=TodoRepository(s)).tick(now)
            await s.commit()

        assert promoted == 0
        assert spawned == 0

        async with factory() as s:
            row = await TodoRepository(s).get_by_id(todo_id)
        assert row is not None
        assert row.status == TodoStatus.SCHEDULED.value
        assert row.version == 1  # unchanged


class TestCronTemplateSchedulerIntegration:
    async def test_due_cron_spawns_queued_child_and_advances_template(
        self, factory
    ) -> None:
        tmpl_id = "TODO-SINT0003"
        past_run = datetime.now(UTC) - timedelta(minutes=5)
        await _seed_cron_template(factory, tmpl_id, next_run_at=past_run)

        now = datetime.now(UTC)
        async with factory() as s:
            promoted, spawned = await TodoScheduler(repo=TodoRepository(s)).tick(now)
            await s.commit()

        assert promoted == 0
        assert spawned == 1

        async with factory() as s:
            tmpl = await TodoRepository(s).get_by_id(tmpl_id)
        assert tmpl is not None
        assert tmpl.status == TodoStatus.SCHEDULED.value
        assert tmpl.run_count == 1
        assert tmpl.last_run_at is not None
        assert tmpl.next_run_at is not None
        # SQLite stores DateTime tz-naive, so normalize both sides to naive-UTC
        # before comparing (the scheduler wrote a tz-aware UTC value).
        _next = tmpl.next_run_at
        if _next.tzinfo is not None:
            _next = _next.astimezone(UTC).replace(tzinfo=None)
        assert _next > now.replace(tzinfo=None)

        async with factory() as s:
            result = await s.execute(
                select(TodoModel).where(
                    TodoModel.parent_todo_id == tmpl_id,
                    TodoModel.status == TodoStatus.QUEUED.value,
                )
            )
            children = list(result.scalars().all())
        assert len(children) == 1, "expected exactly one spawned child"
        child = children[0]
        assert child.parent_todo_id == tmpl_id
        assert child.cron is None
        assert child.scheduled_at is None
        assert child.next_run_at is None

    async def test_not_due_cron_does_not_spawn(self, factory) -> None:
        tmpl_id = "TODO-SINT0004"
        future_run = datetime.now(UTC) + timedelta(hours=1)
        await _seed_cron_template(factory, tmpl_id, next_run_at=future_run)

        now = datetime.now(UTC)
        async with factory() as s:
            promoted, spawned = await TodoScheduler(repo=TodoRepository(s)).tick(now)
            await s.commit()

        assert promoted == 0
        assert spawned == 0

        async with factory() as s:
            tmpl = await TodoRepository(s).get_by_id(tmpl_id)
        assert tmpl is not None
        assert tmpl.status == TodoStatus.SCHEDULED.value
        assert tmpl.run_count == 0

    async def test_max_runs_reached_cancels_template_in_db(self, factory) -> None:
        tmpl_id = "TODO-SINT0005"
        past_run = datetime.now(UTC) - timedelta(minutes=5)
        await _seed_cron_template(
            factory, tmpl_id, next_run_at=past_run, run_count=3, max_runs=3
        )

        now = datetime.now(UTC)
        async with factory() as s:
            promoted, spawned = await TodoScheduler(repo=TodoRepository(s)).tick(now)
            await s.commit()

        assert promoted == 0
        assert spawned == 0

        async with factory() as s:
            tmpl = await TodoRepository(s).get_by_id(tmpl_id)
        assert tmpl is not None
        assert tmpl.status == TodoStatus.CANCELLED.value
        assert tmpl.version == 2  # transition() bumped it

        async with factory() as s:
            result = await s.execute(
                select(TodoModel).where(TodoModel.parent_todo_id == tmpl_id)
            )
            children = list(result.scalars().all())
        assert len(children) == 0
