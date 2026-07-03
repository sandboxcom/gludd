"""Dedicated optimistic-lock tests for TodoModel.version.

Proves that concurrent updates to the same todo are detected and rejected,
preventing silent overwrite of in-flight work.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import ConcurrencyError, TodoRepository


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


class TestTodoOptimisticLock:
    """Tests that prove TodoModel.version acts as an optimistic lock."""

    async def test_update_correct_version_succeeds_and_increments(
        self, async_session: AsyncSession
    ) -> None:
        """Update with the correct expected_version succeeds and bumps version."""
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Update with correct version"})
        assert created.version == 1

        updated = await repo.update(
            created.todo_id,
            {"title": "Correctly updated"},
            expected_version=1,
        )
        assert updated.title == "Correctly updated"
        assert updated.version == 2

    async def test_update_stale_version_raises_concurrency_error(
        self, async_session: AsyncSession
    ) -> None:
        """Update with a stale expected_version raises ConcurrencyError."""
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Stale version test"})
        assert created.version == 1

        # First update consumes version 1.
        await repo.update(created.todo_id, {"title": "First update"}, expected_version=1)
        # Second update with the now-stale version 1 must be rejected.
        with pytest.raises(ConcurrencyError):
            await repo.update(
                created.todo_id,
                {"title": "Stale update attempt"},
                expected_version=1,
            )

    async def test_concurrent_update_same_version_one_wins_one_loses(
        self, async_session: AsyncSession, async_engine
    ) -> None:
        """Two writers racing on the same version — one wins, one loses.

        Simulates lost-update detection: writer A updates at version 1 (succeeds),
        writer B attempts the same version 1 and is rejected (rowcount == 0).
        """
        # Create the todo in the primary session.
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "Concurrent race test"})
        assert created.version == 1

        # Writer A: update succeeds, bumping version to 2.
        await repo.update(
            created.todo_id,
            {"title": "Writer A won"},
            expected_version=1,
        )

        # Writer B (the loser): opens a SEPARATE session and tries to update
        # using the now-stale version 1.  The WHERE clause on id AND version
        # matches zero rows, so update() raises ConcurrencyError.
        session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as writer_b_session:
            writer_b_repo = TodoRepository(writer_b_session)
            with pytest.raises(ConcurrencyError):
                await writer_b_repo.update(
                    created.todo_id,
                    {"title": "Writer B lost"},
                    expected_version=1,
                )

        # Verify writer A's changes persisted (not overwritten by B).
        # Use a raw query on the primary session to bypass identity-map caching.
        from sqlalchemy import select as _select
        result = await async_session.execute(
            _select(TodoModel).where(TodoModel.todo_id == created.todo_id)
        )
        updated = result.scalar_one_or_none()
        assert updated is not None
        assert updated.title == "Writer A won"
        assert updated.version == 2
