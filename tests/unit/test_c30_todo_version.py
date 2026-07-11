"""Tests for C30: TodoModel.version wired as SQLAlchemy version_id_col."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TodoModel


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


class TestC30TodoVersionIdCol:
    """Tests that prove TodoModel.version is wired as version_id_col."""

    async def test_version_id_col_is_wired(self) -> None:
        """TodoModel.__mapper__.version_id_col references the version column."""
        assert TodoModel.__mapper__.version_id_col is not None
        assert TodoModel.__mapper__.version_id_col.name == "version"

    async def test_single_update_succeeds(
        self, async_session: AsyncSession
    ) -> None:
        """Normal single update succeeds — version auto-increments."""
        todo = TodoModel(
            title="test single update",
            status="backlog",
            priority=0,
        )
        async_session.add(todo)
        await async_session.commit()

        initial_version = todo.version
        assert initial_version == 1

        todo.title = "updated title"
        await async_session.commit()

        await async_session.refresh(todo)
        assert todo.version == initial_version + 1
        assert todo.title == "updated title"

    async def test_concurrent_updates_detect_conflict(
        self, async_session: AsyncSession, async_engine
    ) -> None:
        """Two concurrent updates — exactly one wins, loser gets StaleDataError."""
        factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as s1:
            todo = TodoModel(
                title="concurrent test",
                status="backlog",
                priority=0,
            )
            s1.add(todo)
            await s1.commit()
            todo_id = todo.id
            initial_version = todo.version
            assert initial_version == 1

        async with factory() as s2:
            todo_s2 = await s2.get(TodoModel, todo_id)
            assert todo_s2 is not None
            assert todo_s2.version == initial_version

            async with factory() as s3:
                todo_s3 = await s3.get(TodoModel, todo_id)
                assert todo_s3 is not None
                todo_s3.title = "update from s3 (winner)"
                await s3.commit()
                await s3.refresh(todo_s3)
                assert todo_s3.version == initial_version + 1

            todo_s2.title = "update from s2 (stale)"
            try:
                await s2.commit()
            except StaleDataError:
                await s2.rollback()
            else:
                await s2.rollback()
                pytest.fail(
                    "Expected StaleDataError for stale concurrent update, "
                    "but commit succeeded — version_id_col not enforcing."
                )

        async with factory() as s4:
            todo_final = await s4.get(TodoModel, todo_id)
            assert todo_final.version == initial_version + 1
            assert todo_final.title == "update from s3 (winner)"
