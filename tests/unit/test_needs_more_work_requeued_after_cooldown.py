"""Unit tests: NEEDS_MORE_WORK → QUEUED requeue sweep.

Covers:
  1. Valid requeue: NEEDS_MORE_WORK todo past cooldown, run_count < threshold → QUEUED
  2. Chronic skip: run_count >= max_requeues_before_chronic → NOT requeued
  3. Cooldown respect: recent NEEDS_MORE_WORK todo → NOT requeued
  4. Multiple todos: mix of chronic/eligible/recent → only eligible requeued
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import DateTime, bindparam, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.schemas.todo import TodoStatus

_SET_UPDATED_AT = text(
    "UPDATE todos SET updated_at = :ts WHERE id = :id"
).bindparams(bindparam("ts", type_=DateTime(timezone=True)))


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
async def async_session_factory(async_engine):
    return sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(async_session_factory) -> AsyncSession:
    async with async_session_factory() as s:
        yield s


async def _seed_needs_more_work(
    repo: TodoRepository,
    todo_id: str,
    *,
    run_count: int = 0,
    hours_ago: int = 48,
    title: str = "test nmw todo",
) -> TodoModel:
    todo = await repo.create(
        {
            "todo_id": todo_id,
            "title": title,
            "status": TodoStatus.NEEDS_MORE_WORK.value,
            "work_type": "code",
            "queue": "core",
            "run_count": run_count,
        }
    )
    old_stamp = datetime.now(UTC) - timedelta(hours=hours_ago)
    await repo._session.execute(
        _SET_UPDATED_AT,
        {"ts": old_stamp, "id": todo.id},
    )
    await repo._session.flush()
    await repo._session.refresh(todo)
    return todo


async def _get_todo_status(repo: TodoRepository, todo_id: str) -> str | None:
    todo = await repo.get_by_id(todo_id)
    return todo.status if todo else None


async def _get_todo_run_count(repo: TodoRepository, todo_id: str) -> int:
    todo = await repo.get_by_id(todo_id)
    return int(getattr(todo, "run_count", 0) or 0)


# ── tests ──────────────────────────────────────────────────────────────────────


class TestNeedsMoreWorkRequeuedAfterCooldown:
    async def test_requeues_eligible_work(self, session: AsyncSession):
        repo = TodoRepository(session)
        await _seed_needs_more_work(repo, "NMW-001", run_count=0, hours_ago=48)

        count = await repo.requeue_needs_more_work(cooldown_hours=24, max_run_count=3, limit=10)
        assert count == 1
        assert await _get_todo_status(repo, "NMW-001") == TodoStatus.QUEUED.value

    async def test_skips_recent_work(self, session: AsyncSession):
        repo = TodoRepository(session)
        await _seed_needs_more_work(repo, "NMW-002", run_count=0, hours_ago=1)

        count = await repo.requeue_needs_more_work(cooldown_hours=24, max_run_count=3, limit=10)
        assert count == 0
        assert await _get_todo_status(repo, "NMW-002") == TodoStatus.NEEDS_MORE_WORK.value

    async def test_skips_chronic_work(self, session: AsyncSession):
        repo = TodoRepository(session)
        await _seed_needs_more_work(repo, "NMW-003", run_count=5, hours_ago=48)

        count = await repo.requeue_needs_more_work(cooldown_hours=24, max_run_count=3, limit=10)
        assert count == 0
        assert await _get_todo_status(repo, "NMW-003") == TodoStatus.NEEDS_MORE_WORK.value

    async def test_mixed_batch(self, session: AsyncSession):
        repo = TodoRepository(session)
        await _seed_needs_more_work(repo, "NMW-A", run_count=0, hours_ago=48)
        await _seed_needs_more_work(repo, "NMW-B", run_count=5, hours_ago=48)
        await _seed_needs_more_work(repo, "NMW-C", run_count=1, hours_ago=2)
        await _seed_needs_more_work(repo, "NMW-D", run_count=0, hours_ago=72)

        count = await repo.requeue_needs_more_work(cooldown_hours=24, max_run_count=3, limit=10)
        assert count == 2
        assert await _get_todo_status(repo, "NMW-A") == TodoStatus.QUEUED.value
        assert await _get_todo_status(repo, "NMW-B") == TodoStatus.NEEDS_MORE_WORK.value
        assert await _get_todo_status(repo, "NMW-C") == TodoStatus.NEEDS_MORE_WORK.value
        assert await _get_todo_status(repo, "NMW-D") == TodoStatus.QUEUED.value

    async def test_respects_limit(self, session: AsyncSession):
        repo = TodoRepository(session)
        for i in range(5):
            await _seed_needs_more_work(repo, f"NMW-LIM-{i}", run_count=0, hours_ago=48)

        count = await repo.requeue_needs_more_work(cooldown_hours=24, max_run_count=3, limit=2)
        assert count == 2

    async def test_skips_non_needs_more_work_status(self, session: AsyncSession):
        repo = TodoRepository(session)
        todo = await repo.create(
            {
                "todo_id": "NMW-BLOCKED",
                "title": "blocked todo",
                "status": TodoStatus.BLOCKED.value,
                "work_type": "code",
                "queue": "core",
            }
        )
        old_stamp = datetime.now(UTC) - timedelta(hours=48)
        await session.execute(
            _SET_UPDATED_AT,
            {"ts": old_stamp, "id": todo.id},
        )
        await session.flush()

        count = await repo.requeue_needs_more_work(cooldown_hours=24, max_run_count=3, limit=10)
        assert count == 0
        assert await _get_todo_status(repo, "NMW-BLOCKED") == TodoStatus.BLOCKED.value

    async def test_nothing_to_requeue(self, session: AsyncSession):
        repo = TodoRepository(session)
        count = await repo.requeue_needs_more_work(cooldown_hours=24, max_run_count=3, limit=10)
        assert count == 0
