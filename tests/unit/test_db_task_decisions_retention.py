"""Tests for task_decision retention cleanup (E11/E12 periodic delete)."""

import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, insert, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from general_ludd.db.models import Base, TaskDecisionModel, TaskReturnModel
from general_ludd.db.task_decisions_retention import (
    DEFAULT_RETENTION_DAYS,
    cleanup_old_task_decisions,
)


def _make_task_decision(**overrides) -> TaskDecisionModel:
    data = {
        "id": None,
        "return_id": "R-default",
        "decision": "defer",
        "confidence": 0.5,
        "created_at": datetime.now(UTC),
    }
    data.update(overrides)
    return TaskDecisionModel(**{k: v for k, v in data.items() if k != "id"})


@pytest_asyncio.fixture
async def engine():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    @event.listens_for(_engine.sync_engine, "connect")
    def _pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with _engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
        return_ids = [
            "R-old",
            "R-recent",
            "R-dry1",
            "R-dry2",
            "R-fixed-old",
            "R-fixed-new",
            "R-ret5",
            "R-midnight",
            *(f"R-batch-{index}" for index in range(7)),
            "R-mix-old",
            "R-mix-new",
            "R-mix-new2",
            "R-boundary",
            "R-dry-old",
            "R-dry-new",
        ]
        await conn.execute(
            insert(TaskReturnModel),
            [
                {
                    "return_id": return_id,
                    "job_id": f"job-{return_id}",
                    "playbook": "retention-test.yml",
                    "queue": "test",
                }
                for return_id in return_ids
            ],
        )
    yield _engine
    async with _engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.drop_all(c))
    await _engine.dispose()
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


def _hours_ago(hours: int) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def _days_ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


class TestCleanupOldTaskDecisions:
    async def test_deletes_rows_older_than_retention(self, session_factory, engine):
        ancient = _days_ago(100)
        row = _make_task_decision(created_at=ancient, return_id="R-old")
        async with session_factory() as s:
            s.add(row)
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s)
            await s.commit()

        assert count == 1

        async with session_factory() as s:
            result = await s.execute(text("SELECT COUNT(*) FROM task_decisions"))
            assert result.scalar_one() == 0

    async def test_keeps_rows_newer_than_retention(self, session_factory, engine):
        recent = _days_ago(10)
        row = _make_task_decision(created_at=recent, return_id="R-recent")
        async with session_factory() as s:
            s.add(row)
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s)
            await s.commit()

        assert count == 0

        async with session_factory() as s:
            result = await s.execute(text("SELECT COUNT(*) FROM task_decisions"))
            assert result.scalar_one() == 1

    async def test_dry_run_returns_count_without_deleting(self, session_factory, engine):
        ancient = _days_ago(100)
        async with session_factory() as s:
            s.add(_make_task_decision(created_at=ancient, return_id="R-dry1"))
            s.add(_make_task_decision(created_at=ancient + timedelta(hours=1), return_id="R-dry2"))
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s, dry_run=True)
            await s.commit()

        assert count == 2

        async with session_factory() as s:
            result = await s.execute(text("SELECT COUNT(*) FROM task_decisions"))
            assert result.scalar_one() == 2

    async def test_raises_valueerror_on_zero_retention(self, session):
        with pytest.raises(ValueError, match="retention_days must be > 0"):
            await cleanup_old_task_decisions(session, retention_days=0)

    async def test_raises_valueerror_on_negative_retention(self, session):
        with pytest.raises(ValueError, match="retention_days must be > 0"):
            await cleanup_old_task_decisions(session, retention_days=-5)

    async def test_explicit_now_used_for_cutoff(self, session_factory, engine):
        fixed_now = datetime(2026, 1, 15, 12, 30, 0, tzinfo=UTC)
        ancient = fixed_now - timedelta(days=100)
        recent = fixed_now - timedelta(days=10)
        async with session_factory() as s:
            s.add(_make_task_decision(created_at=ancient, return_id="R-fixed-old"))
            s.add(_make_task_decision(created_at=recent, return_id="R-fixed-new"))
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s, retention_days=30, now=fixed_now)

        assert count == 1

    async def test_zero_rows_affected_on_empty_table(self, session):
        count = await cleanup_old_task_decisions(session)
        assert count == 0

    async def test_respects_custom_retention_days(self, session_factory, engine):
        old_5 = _days_ago(10)
        async with session_factory() as s:
            s.add(_make_task_decision(created_at=old_5, return_id="R-ret5"))
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s, retention_days=5)

        assert count == 1

    async def test_cutoff_truncates_to_midnight(self, session_factory, engine):
        fixed_now = datetime(2026, 1, 15, 23, 59, 59, tzinfo=UTC)
        midnight_old = datetime(2026, 1, 14, 0, 0, 0, tzinfo=UTC) - timedelta(days=10)
        async with session_factory() as s:
            s.add(_make_task_decision(created_at=midnight_old, return_id="R-midnight"))
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s, retention_days=5, now=fixed_now)

        assert count == 1

    async def test_deletes_exact_count_of_old_rows(self, session_factory, engine):
        ancient = _days_ago(200)
        async with session_factory() as s:
            for i in range(7):
                s.add(
                    _make_task_decision(
                        created_at=ancient + timedelta(hours=i),
                        return_id=f"R-batch-{i}",
                    )
                )
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s)

        assert count == 7

    async def test_leaves_recent_rows_untouched_with_mixed_data(self, session_factory, engine):
        ancient = _days_ago(200)
        recent = _days_ago(5)
        async with session_factory() as s:
            s.add(_make_task_decision(created_at=ancient, return_id="R-mix-old"))
            s.add(_make_task_decision(created_at=recent, return_id="R-mix-new"))
            s.add(_make_task_decision(created_at=recent + timedelta(hours=1), return_id="R-mix-new2"))
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s)
            await s.commit()

        assert count == 1

        async with session_factory() as s:
            result = await s.execute(text("SELECT return_id FROM task_decisions ORDER BY return_id"))
            ids = {row[0] for row in result}
            assert ids == {"R-mix-new", "R-mix-new2"}

    async def test_boundary_day_at_retention_limit_keeps_row(self, session_factory, engine):
        fixed_now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        exactly_90_days_ago = fixed_now - timedelta(days=90)
        async with session_factory() as s:
            s.add(_make_task_decision(created_at=exactly_90_days_ago, return_id="R-boundary"))
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s, retention_days=90, now=fixed_now)

        assert count == 0

    async def test_dry_run_zero_on_empty_table(self, session):
        count = await cleanup_old_task_decisions(session, dry_run=True)
        assert count == 0

    async def test_dry_run_respects_cutoff(self, session_factory, engine):
        ancient = _days_ago(100)
        recent = _days_ago(10)
        async with session_factory() as s:
            s.add(_make_task_decision(created_at=ancient, return_id="R-dry-old"))
            s.add(_make_task_decision(created_at=recent, return_id="R-dry-new"))
            await s.commit()

        async with session_factory() as s:
            count = await cleanup_old_task_decisions(s, dry_run=True, retention_days=30)

        assert count == 1


class TestDefaultRetentionConstant:
    def test_default_retention_days_is_90(self):
        assert DEFAULT_RETENTION_DAYS == 90
