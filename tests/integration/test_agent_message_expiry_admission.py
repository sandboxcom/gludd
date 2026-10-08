"""Acceptance coverage for bounded agent-message expiry admission."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import AgentMessageModel, Base
from general_ludd.db.repositories import messaging as messaging_repository
from general_ludd.db.repository import AgentMessageRepository

_NOW = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


def _message(
    message_id: str,
    *,
    created_at: datetime,
    ttl_seconds: int | None,
) -> AgentMessageModel:
    return AgentMessageModel(
        id=message_id,
        sender="planner",
        recipient="coder",
        topic=message_id,
        created_at=created_at,
        ttl_seconds=ttl_seconds,
    )


@pytest.mark.asyncio
async def test_expired_rows_cannot_consume_limit_or_hide_live_rows(
    async_engine: AsyncEngine,
    async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(messaging_repository, "_utc_now", lambda: _NOW)
    async_session.add_all(
        [
            _message(
                f"MSG-EXPIRED-{index:03d}",
                created_at=_NOW - timedelta(minutes=5, seconds=index),
                ttl_seconds=1,
            )
            for index in range(5)
        ]
        + [
            _message(
                "MSG-LIVE-001",
                created_at=_NOW - timedelta(seconds=1),
                ttl_seconds=60,
            )
        ]
    )
    await async_session.flush()

    statements: list[str] = []

    @event.listens_for(async_engine.sync_engine, "before_cursor_execute")
    def _record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    rows = await AgentMessageRepository(async_session).inbox("coder", limit=1)

    assert [row.id for row in rows] == ["MSG-LIVE-001"]
    assert len(statements) == 1
    assert "LIMIT" in statements[0].upper()


@pytest.mark.asyncio
async def test_message_at_exact_ttl_boundary_is_excluded(
    async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(messaging_repository, "_utc_now", lambda: _NOW)
    async_session.add(
        _message(
            "MSG-BOUNDARY",
            created_at=_NOW - timedelta(seconds=30),
            ttl_seconds=30,
        )
    )
    await async_session.flush()

    assert await AgentMessageRepository(async_session).inbox("coder") == []


@pytest.mark.asyncio
async def test_inbox_has_stable_tie_breaker_and_hard_result_ceiling(
    async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(messaging_repository, "_utc_now", lambda: _NOW)
    ids = [f"MSG-LIVE-{index:03d}" for index in reversed(range(101))]
    async_session.add_all(
        [
            _message(
                message_id,
                created_at=_NOW - timedelta(seconds=1),
                ttl_seconds=60,
            )
            for message_id in ids
        ]
    )
    await async_session.flush()

    rows = await AgentMessageRepository(async_session).inbox("coder", limit=10_000)

    assert [row.id for row in rows] == sorted(ids)[:100]

