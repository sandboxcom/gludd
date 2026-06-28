"""FIFO-ordering regression tests for TodoRepository.claim_runnable.

claim_runnable previously had NO ORDER BY, so under a backlog of QUEUED todos
the database was free to return rows in any order — an older todo could be
perpetually skipped (starvation) while newer ones were claimed first. The fix
adds ``.order_by(TodoModel.created_at, TodoModel.id)`` so the oldest QUEUED
todo is always claimed first; ``id`` is a deterministic tiebreaker for todos
created within the same ``created_at`` instant.

These tests insert TodoModel rows directly with explicit ``created_at`` values
that are the REVERSE of insertion order, so a test that passed by accident of
insertion order would fail — only a real created_at ORDER BY satisfies them.

asyncio_mode = "auto" is set in pyproject.toml — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.schemas.todo import TodoStatus


def _make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_engine()
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


_BASE = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed_queued_reverse_age(session: AsyncSession, n: int) -> None:
    """Insert *n* QUEUED todos where insertion order is the REVERSE of age.

    Todo ``t{i}`` is inserted at index *i* but stamped created_at = base+(n-i)s,
    so t0 is the NEWEST and t{n-1} is the OLDEST. A correct FIFO claim therefore
    returns t{n-1}, t{n-2}, ... — the opposite of insertion order.
    """
    for i in range(n):
        session.add(
            TodoModel(
                todo_id=f"t{i}",
                title=f"t{i}",
                status=TodoStatus.QUEUED.value,
                queue="core",
                work_type="code",
                version=1,
                created_at=_BASE + timedelta(seconds=(n - i)),
            )
        )
    await session.flush()


class TestClaimRunnableFifo:
    async def test_claims_in_created_at_order_not_insertion_order(
        self, async_session: AsyncSession
    ) -> None:
        """Claiming all QUEUED todos returns them oldest-created_at first."""
        await _seed_queued_reverse_age(async_session, 5)
        repo = TodoRepository(async_session)

        claimed = await repo.claim_runnable(limit=10)

        # Oldest-first by created_at: t4(+1s), t3(+2s), t2(+3s), t1(+4s), t0(+5s).
        assert [t.todo_id for t in claimed] == ["t4", "t3", "t2", "t1", "t0"]

    async def test_limit_claims_oldest_subset_preventing_starvation(
        self, async_session: AsyncSession
    ) -> None:
        """A capped claim takes the OLDEST todos, so newer ones can't starve them."""
        await _seed_queued_reverse_age(async_session, 5)
        repo = TodoRepository(async_session)

        claimed = await repo.claim_runnable(limit=3)

        # The 3 oldest by created_at — never the 3 most-recently inserted (t0..t2).
        assert [t.todo_id for t in claimed] == ["t4", "t3", "t2"]
        # And they are all now ACTIVE (genuinely claimed, not just selected).
        assert all(t.status == TodoStatus.ACTIVE.value for t in claimed)


class TestClaimRunnablePriority:
    """F2: ``claim_runnable`` must order by ``priority DESC`` so the PID-cap trim
    path cannot drop a higher-priority todo in favor of a lower-priority one
    that happens to have a slightly older ``created_at``.
    """

    async def test_higher_priority_claimed_first(
        self, async_session: AsyncSession
    ) -> None:
        """Three todos with identical created_at but priorities [0, 5, 2] must
        claim in priority order: [5, 2, 0]."""
        for tid, prio in (("t0", 0), ("t1", 5), ("t2", 2)):
            async_session.add(
                TodoModel(
                    todo_id=tid,
                    title=tid,
                    status=TodoStatus.QUEUED.value,
                    queue="core",
                    work_type="code",
                    version=1,
                    priority=prio,
                    created_at=_BASE,
                )
            )
        await async_session.flush()

        repo = TodoRepository(async_session)
        claimed = await repo.claim_runnable(limit=10)

        assert [t.todo_id for t in claimed] == ["t1", "t2", "t0"], (
            f"priority-desc order broken: {[t.todo_id for t in claimed]}"
        )

    async def test_priority_breaks_tie_then_created_at(
        self, async_session: AsyncSession
    ) -> None:
        """Equal priority falls back to created_at order (FIFO tiebreak)."""
        # Same priority, REVERSE insertion order vs created_at — claim must
        # follow created_at (oldest first), NOT insertion order.
        for i in range(3):
            async_session.add(
                TodoModel(
                    todo_id=f"t{i}",
                    title=f"t{i}",
                    status=TodoStatus.QUEUED.value,
                    queue="core",
                    work_type="code",
                    version=1,
                    priority=3,  # equal priority
                    created_at=_BASE + timedelta(seconds=(3 - i)),
                )
            )
        await async_session.flush()

        repo = TodoRepository(async_session)
        claimed = await repo.claim_runnable(limit=10)

        # t2 has the oldest created_at (+1s), then t1 (+2s), then t0 (+3s).
        assert [t.todo_id for t in claimed] == ["t2", "t1", "t0"]
