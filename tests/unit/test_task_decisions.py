"""E11: task_decisions retention policy — cleanup of old rows.

The index on ``task_decisions.created_at`` (migration 025) makes querying
efficient; the retention policy prevents unbounded table growth.
"""

from __future__ import annotations

import datetime

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TaskDecisionModel, TaskReturnModel
from general_ludd.db.task_decisions_retention import (
    DEFAULT_RETENTION_DAYS,
    cleanup_old_task_decisions,
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
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


async def _seed_decisions(session: AsyncSession, ages_days: list[int]) -> None:
    now = datetime.datetime.now(datetime.UTC)
    for i, age in enumerate(ages_days):
        ret = TaskReturnModel(
            return_id=f"RET-{i:03d}",
            job_id=f"JOB-{i:03d}",
            playbook="test.yml",
            queue="test",
            status="completed",
        )
        session.add(ret)
        await session.flush()
        dec = TaskDecisionModel(
            return_id=f"RET-{i:03d}",
            project_id=None,
            decision="accept",
            confidence=0.9,
            created_at=now - datetime.timedelta(days=age),
        )
        session.add(dec)
    await session.flush()


async def _count_decisions(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(TaskDecisionModel))
    return result.scalar_one()


class TestCleanupOldTaskDecisions:
    @pytest.mark.asyncio
    async def test_deletes_rows_older_than_retention(self, async_session: AsyncSession):
        await _seed_decisions(async_session, [100, 50, 10, 5])

        deleted = await cleanup_old_task_decisions(
            async_session, retention_days=30
        )

        assert deleted == 2
        remaining = await _count_decisions(async_session)
        assert remaining == 2

    @pytest.mark.asyncio
    async def test_keeps_rows_within_retention(self, async_session: AsyncSession):
        await _seed_decisions(async_session, [5, 3, 1])

        deleted = await cleanup_old_task_decisions(
            async_session, retention_days=30
        )

        assert deleted == 0
        remaining = await _count_decisions(async_session)
        assert remaining == 3

    @pytest.mark.asyncio
    async def test_empty_table_returns_zero(self, async_session: AsyncSession):
        deleted = await cleanup_old_task_decisions(
            async_session, retention_days=30
        )

        assert deleted == 0

    @pytest.mark.asyncio
    async def test_dry_run_counts_without_deleting(self, async_session: AsyncSession):
        await _seed_decisions(async_session, [200, 150, 3])

        matched = await cleanup_old_task_decisions(
            async_session, retention_days=30, dry_run=True
        )

        assert matched == 2
        remaining = await _count_decisions(async_session)
        assert remaining == 3

    @pytest.mark.asyncio
    async def test_configurable_retention_period(self, async_session: AsyncSession):
        await _seed_decisions(async_session, [100, 50, 20, 5])

        deleted = await cleanup_old_task_decisions(
            async_session, retention_days=7
        )

        assert deleted == 3
        remaining = await _count_decisions(async_session)
        assert remaining == 1

    @pytest.mark.asyncio
    async def test_deletes_all_when_retention_is_one_day(self, async_session: AsyncSession):
        await _seed_decisions(async_session, [10, 8, 5, 3])

        deleted = await cleanup_old_task_decisions(
            async_session, retention_days=1
        )

        assert deleted == 4
        remaining = await _count_decisions(async_session)
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_injected_now_controls_cutoff(self, async_session: AsyncSession):
        base = datetime.datetime(2026, 7, 10, 12, 0, 0, tzinfo=datetime.UTC)
        for i, age in enumerate([10, 5, 2]):
            ret = TaskReturnModel(
                return_id=f"RET-N{i:03d}",
                job_id=f"JOB-N{i:03d}",
                playbook="test.yml",
                queue="test",
                status="completed",
            )
            session = async_session
            session.add(ret)
            await session.flush()
            dec = TaskDecisionModel(
                return_id=f"RET-N{i:03d}",
                project_id=None,
                decision="accept",
                confidence=0.9,
                created_at=base - datetime.timedelta(days=age),
            )
            session.add(dec)
        await async_session.flush()

        deleted = await cleanup_old_task_decisions(
            async_session,
            retention_days=3,
            now=base,
        )

        assert deleted == 2

    @pytest.mark.asyncio
    async def test_default_retention_is_90_days(self):
        assert DEFAULT_RETENTION_DAYS == 90

    @pytest.mark.asyncio
    async def test_negative_retention_raises(self):
        with pytest.raises(ValueError, match="retention_days must be > 0"):
            await cleanup_old_task_decisions(
                _invalid_session(), retention_days=-5
            )

    @pytest.mark.asyncio
    async def test_zero_retention_raises(self, async_session: AsyncSession):
        with pytest.raises(ValueError, match="retention_days must be > 0"):
            await cleanup_old_task_decisions(async_session, retention_days=0)


def _invalid_session() -> AsyncSession:
    return object()  # type: ignore[return-value]
