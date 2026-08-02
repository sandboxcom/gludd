"""Unit tests for EventWorkTransport ORM model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, EventWorkTransportModel


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
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class TestEventWorkTransportModel:
    async def test_create_with_defaults(self, async_session: AsyncSession):
        transport = EventWorkTransportModel(
            event_type="terraform_apply",
            payload='{"stack": "core"}',
        )
        async_session.add(transport)
        await async_session.flush()

        assert transport.id is not None
        assert transport.event_type == "terraform_apply"
        assert transport.status == "pending"
        assert transport.claimed_by is None
        assert transport.claimed_at is None
        assert transport.created_at is not None
        assert transport.completed_at is None
        assert transport.attempts == 0

    async def test_fenced_claim(self, async_session: AsyncSession):
        t1 = EventWorkTransportModel(
            event_type="build_image",
            payload='{"image": "gludd:latest"}',
        )
        async_session.add(t1)
        await async_session.flush()

        now = datetime.now(UTC)
        t1.status = "claimed"
        t1.claimed_by = "worker-3"
        t1.claimed_at = now
        await async_session.flush()

        stmt = select(EventWorkTransportModel).where(EventWorkTransportModel.id == t1.id)
        result = await async_session.execute(stmt)
        fetched = result.scalar_one()

        assert fetched.status == "claimed"
        assert fetched.claimed_by == "worker-3"
        assert fetched.claimed_at == now

    async def test_lifecycle_pending_to_completed(self, async_session: AsyncSession):
        transport = EventWorkTransportModel(
            event_type="slurm_submit",
            payload='{"job_id": "J-001"}',
        )
        async_session.add(transport)
        await async_session.flush()
        assert transport.status == "pending"

        transport.status = "processing"
        await async_session.flush()
        assert transport.status == "processing"

        transport.status = "completed"
        transport.completed_at = datetime.now(UTC)
        await async_session.flush()
        assert transport.status == "completed"
        assert transport.completed_at is not None

    async def test_lifecycle_pending_to_failed(self, async_session: AsyncSession):
        transport = EventWorkTransportModel(
            event_type="deploy",
            payload='{"target": "staging"}',
        )
        async_session.add(transport)
        await async_session.flush()

        transport.status = "failed"
        transport.completed_at = datetime.now(UTC)
        transport.error_message = "connection refused"
        await async_session.flush()

        assert transport.status == "failed"
        assert transport.error_message == "connection refused"

    async def test_attempts_increment(self, async_session: AsyncSession):
        transport = EventWorkTransportModel(
            event_type="terraform_destroy",
            payload='{"stack": "dev"}',
        )
        async_session.add(transport)
        await async_session.flush()
        assert transport.attempts == 0

        transport.attempts = 1
        await async_session.flush()

        stmt = select(EventWorkTransportModel).where(EventWorkTransportModel.id == transport.id)
        result = await async_session.execute(stmt)
        fetched = result.scalar_one()
        assert fetched.attempts == 1

    async def test_select_pending_unclaimed(self, async_session: AsyncSession):
        t1 = EventWorkTransportModel(
            event_type="run_playbook",
            payload='{"playbook": "site.yml"}',
            status="pending",
        )
        t2 = EventWorkTransportModel(
            event_type="run_playbook",
            payload='{"playbook": "deploy.yml"}',
            status="claimed",
            claimed_by="worker-1",
            claimed_at=datetime.now(UTC),
        )
        async_session.add_all([t1, t2])
        await async_session.flush()

        stmt = select(EventWorkTransportModel).where(
            EventWorkTransportModel.status == "pending",
        )
        result = await async_session.execute(stmt)
        pending = result.scalars().all()

        assert len(pending) == 1
        assert pending[0].id == t1.id

    async def test_claim_stale_expired(self, async_session: AsyncSession):
        stale_at = datetime.now(UTC) - timedelta(minutes=15)
        t1 = EventWorkTransportModel(
            event_type="audit_check",
            payload="{}",
            status="claimed",
            claimed_by="worker-dead",
            claimed_at=stale_at,
        )
        async_session.add(t1)
        await async_session.flush()

        staleness_cutoff = datetime.now(UTC) - timedelta(minutes=5)
        stmt = select(EventWorkTransportModel).where(
            EventWorkTransportModel.status == "claimed",
            EventWorkTransportModel.claimed_at < staleness_cutoff,
        )
        result = await async_session.execute(stmt)
        stale = result.scalars().all()

        assert len(stale) == 1
        assert stale[0].claimed_by == "worker-dead"

    async def test_created_at_is_utc(self, async_session: AsyncSession):
        transport = EventWorkTransportModel(
            event_type="health_check",
            payload="{}",
        )
        async_session.add(transport)
        await async_session.flush()

        assert transport.created_at.tzinfo is not None
        assert transport.created_at.utcoffset() == timedelta(0)
