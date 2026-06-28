"""Regression test for XT-11: AgentMessageRepository.ack must enforce project
ownership so a caller scoped to project A cannot mark project B's message read
by guessing its id.

Uses a dedicated in-memory engine WITHOUT the foreign-key pragma so arbitrary
project_id strings can be seeded without ProjectModel rows — the unit under test
is the ownership guard, not the FK.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import AgentMessageRepository


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed(repo: AgentMessageRepository, project_id: str | None):
    return await repo.send(
        {
            "sender": "planner",
            "recipient": "coder",
            "topic": "task",
            "body": "do x",
            "project_id": project_id,
        }
    )


async def test_cross_tenant_ack_is_refused(session: AsyncSession) -> None:
    repo = AgentMessageRepository(session)
    msg_a = await _seed(repo, "proj-a")

    # A caller scoped to proj-b must NOT be able to ack proj-a's message.
    result = await repo.ack(msg_a.id, project_id="proj-b")
    assert result is None

    # ...and the message stays unread (the guarded UPDATE matched 0 rows).
    refetched = await repo.get_by_id(msg_a.id)
    assert refetched is not None
    assert refetched.read_at is None


async def test_same_tenant_ack_succeeds(session: AsyncSession) -> None:
    repo = AgentMessageRepository(session)
    msg_a = await _seed(repo, "proj-a")

    acked = await repo.ack(msg_a.id, project_id="proj-a")
    assert acked is not None
    assert acked.read_at is not None


async def test_unscoped_ack_is_back_compat(session: AsyncSession) -> None:
    # project_id=None (the default) preserves the unscoped/admin behaviour.
    repo = AgentMessageRepository(session)
    msg = await _seed(repo, "proj-a")

    acked = await repo.ack(msg.id)
    assert acked is not None
    assert acked.read_at is not None


async def test_cross_tenant_then_owner_can_still_ack(session: AsyncSession) -> None:
    # A refused cross-tenant ack must not have consumed the unread state — the
    # rightful owner can still ack afterwards.
    repo = AgentMessageRepository(session)
    msg = await _seed(repo, "proj-a")

    assert await repo.ack(msg.id, project_id="proj-b") is None
    owner_ack = await repo.ack(msg.id, project_id="proj-a")
    assert owner_ack is not None
    assert owner_ack.read_at is not None
