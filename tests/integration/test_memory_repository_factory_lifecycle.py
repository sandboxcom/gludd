"""Lifecycle acceptance for factory-owned memory repository sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, func, inspect, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

from general_ludd.db.models import Base, MemoryRecordModel
from general_ludd.db.repository import MemoryRepository


@pytest_asyncio.fixture
async def lifecycle_database(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    """Provide a real bounded pool and the default expiring session policy."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'memory-lifecycle.db'}",
        pool_size=4,
        max_overflow=0,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession)
    yield engine, sessions
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.checkedout() == 0
    await engine.dispose()


async def test_factory_rows_are_loaded_and_detached_after_commit(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Default expire-on-commit must not make repository results unusable."""
    engine, sessions = lifecycle_database
    repository = MemoryRepository(session_factory=sessions)

    created = await repository.set("agent", "alpha", "one", namespace="notes")
    fetched = await repository.get("agent", "alpha", namespace="notes")
    listed = await repository.list_by_namespace("agent", namespace="notes")

    assert fetched is not None
    assert (created.key, created.value, created.namespace) == ("alpha", "one", "notes")
    assert (fetched.key, fetched.value, fetched.namespace) == ("alpha", "one", "notes")
    assert [(row.key, row.value) for row in listed] == [("alpha", "one")]
    assert inspect(created).detached
    assert inspect(fetched).detached
    assert all(inspect(row).detached for row in listed)
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.checkedout() == 0


async def test_factory_list_remains_bounded_and_releases_connection(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Factory ownership preserves the public bound and returns its lease."""
    engine, sessions = lifecycle_database
    repository = MemoryRepository(session_factory=sessions)
    for index in range(5):
        await repository.set("agent", f"key-{index}", f"value-{index}")

    rows = await repository.list_by_namespace("agent", limit=2)

    assert [(row.key, row.value) for row in rows] == [
        ("key-0", "value-0"),
        ("key-1", "value-1"),
    ]
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.checkedout() == 0


async def test_caller_owned_rows_stay_attached_and_rollback_is_preserved(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Injected sessions retain identity-map and transaction ownership."""
    _, sessions = lifecycle_database
    async with sessions() as session:
        repository = MemoryRepository(session=session)
        created = await repository.set("agent", "owned", "pending")
        fetched = await repository.get("agent", "owned")
        listed = await repository.list_by_namespace("agent")

        assert fetched is created
        assert listed == [created]
        assert inspect(created).session is session.sync_session
        assert inspect(fetched).session is session.sync_session
        assert all(inspect(row).session is session.sync_session for row in listed)
        await session.rollback()

    async with sessions() as verification_session:
        count = await verification_session.scalar(select(func.count()).select_from(MemoryRecordModel))
    assert count == 0


async def test_factory_failure_rolls_back_and_releases_connection(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-flush failure cannot commit partial factory-owned state."""
    engine, sessions = lifecycle_database
    repository = MemoryRepository(session_factory=sessions)

    async def fail_refresh(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(AsyncSession, "refresh", fail_refresh)
    with pytest.raises(RuntimeError, match="refresh failed"):
        await repository.set("agent", "rollback", "discard")

    async with sessions() as verification_session:
        count = await verification_session.scalar(select(func.count()).select_from(MemoryRecordModel))
    assert count == 0
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.checkedout() == 0


async def test_factory_expired_get_commits_ttl_deletion(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Lazy TTL cleanup commits within a repository-owned transaction."""
    engine, sessions = lifecycle_database
    async with sessions.begin() as seed_session:
        seed_session.add(
            MemoryRecordModel(
                agent_id="agent",
                key="expired",
                value="discard",
                ttl_seconds=1,
                created_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )

    repository = MemoryRepository(session_factory=sessions)
    assert await repository.get("agent", "expired") is None

    async with sessions() as verification_session:
        count = await verification_session.scalar(select(func.count()).select_from(MemoryRecordModel))
    assert count == 0
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.checkedout() == 0


async def test_concurrent_factory_calls_use_independent_sessions(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Concurrent calls do not share mutable AsyncSession transaction state."""
    engine, sessions = lifecycle_database
    repository = MemoryRepository(session_factory=sessions)

    created = await asyncio.gather(
        *(repository.set("agent", f"key-{index}", f"value-{index}") for index in range(4))
    )
    fetched = await asyncio.gather(
        *(repository.get("agent", f"key-{index}") for index in range(4))
    )

    assert [row.value for row in created] == [f"value-{index}" for index in range(4)]
    assert [row.value if row is not None else None for row in fetched] == [
        f"value-{index}" for index in range(4)
    ]
    assert all(inspect(row).detached for row in created)
    assert all(row is not None and inspect(row).detached for row in fetched)
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.checkedout() == 0


async def test_concurrent_project_lists_are_fail_closed_and_release_connections(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Concurrent global and tenant reads must return disjoint partitions."""
    engine, sessions = lifecycle_database
    repository = MemoryRepository(session_factory=sessions)
    await asyncio.gather(
        repository.set("agent", "global", "shared"),
        repository.set("agent", "project-a", "secret-a", project_id="project-a"),
        repository.set("agent", "project-b", "secret-b", project_id="project-b"),
    )

    global_rows, project_a_rows, project_b_rows = await asyncio.gather(
        repository.list_by_namespace("agent", namespace="*"),
        repository.list_by_namespace("agent", namespace="*", project_id="project-a"),
        repository.list_by_namespace("agent", namespace="*", project_id="project-b"),
    )

    assert [(row.key, row.project_id) for row in global_rows] == [("global", None)]
    assert [(row.key, row.project_id) for row in project_a_rows] == [
        ("project-a", "project-a")
    ]
    assert [(row.key, row.project_id) for row in project_b_rows] == [
        ("project-b", "project-b")
    ]
    assert all(inspect(row).detached for row in [*global_rows, *project_a_rows, *project_b_rows])
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.checkedout() == 0


async def test_caller_owned_scoped_list_preserves_rollback_control(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Tenant filtering must not commit or detach caller-owned transactions."""
    _, sessions = lifecycle_database
    async with sessions() as session:
        repository = MemoryRepository(session=session)
        global_row = await repository.set("agent", "global", "shared")
        project_row = await repository.set(
            "agent", "project", "secret", project_id="project-a"
        )

        listed = await repository.list_by_namespace("agent", namespace="*")

        assert listed == [global_row]
        assert project_row not in listed
        assert inspect(global_row).session is session.sync_session
        await session.rollback()

    async with sessions() as verification_session:
        count = await verification_session.scalar(select(func.count()).select_from(MemoryRecordModel))
    assert count == 0


async def test_scoped_list_uses_one_bounded_query(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Isolation and the public result bound belong to the same SQL statement."""
    engine, sessions = lifecycle_database
    repository = MemoryRepository(session_factory=sessions)
    await repository.set("agent", "global", "shared")
    await repository.set("agent", "project", "secret", project_id="project-a")
    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        rows = await repository.list_by_namespace("agent", namespace="*", limit=1)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)

    assert [(row.key, row.project_id) for row in rows] == [("global", None)]
    assert len(statements) == 1
    assert "project_id IS NULL" in statements[0]
    assert "LIMIT" in statements[0]


async def test_factory_list_error_rolls_back_and_releases_connection(
    lifecycle_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """A query error must discard its transaction and return the pool lease."""
    engine, sessions = lifecycle_database
    repository = MemoryRepository(session_factory=sessions)
    await repository.set("agent", "global", "shared")

    def fail_query(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced scoped-list failure")

    event.listen(engine.sync_engine, "before_cursor_execute", fail_query)
    try:
        with pytest.raises(RuntimeError, match="forced scoped-list failure"):
            await repository.list_by_namespace("agent")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail_query)

    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.checkedout() == 0
    rows = await repository.list_by_namespace("agent")
    assert [(row.key, row.project_id) for row in rows] == [("global", None)]
