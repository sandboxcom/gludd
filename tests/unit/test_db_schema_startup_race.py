"""Concurrent SQLite startup must tolerate check-then-create DDL races."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine as sqlalchemy_create_async_engine

from general_ludd.db.models import Base, QueueModel
from general_ludd.db.session import (
    _conflict_ignoring_insert,
    _install_sqlite_async_pool_compat,
    ensure_tables,
    init_async_engine,
    init_read_only_engine_from_config,
)


@pytest.mark.asyncio
async def test_concurrent_sqlite_workers_ensure_schema_idempotently(
    tmp_path: Path,
) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'startup-race.db'}"
    engines = [init_async_engine(url) for _ in range(4)]
    try:
        await asyncio.gather(*(ensure_tables(engine) for engine in engines))
        await asyncio.gather(*(ensure_tables(engine) for engine in engines))
    finally:
        await asyncio.gather(*(engine.dispose() for engine in engines))


@pytest.mark.asyncio
async def test_schema_changed_during_create_all_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry only SQLite's transient concurrent-schema invalidation."""
    engine = init_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'schema-changed.db'}"
    )
    original_create_all = Base.metadata.create_all
    attempts = 0

    def create_all_with_one_schema_race(bind: Any, **kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError(
                'PRAGMA main.table_info("agent_tokens")',
                {},
                RuntimeError("database schema has changed"),
            )
        original_create_all(bind, **kwargs)

    monkeypatch.setattr(Base.metadata, "create_all", create_all_with_one_schema_race)
    try:
        await ensure_tables(engine)
        assert attempts == 2
    finally:
        await engine.dispose()


def test_sqlite_pool_compat_install_is_bounded_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave SQLAlchemy globals unchanged after both bounded early exits."""
    globals_map: dict[str, Any] = sqlalchemy_create_async_engine.__globals__
    assert globals_map.get("_gludd_static_pool_compat") is True
    _install_sqlite_async_pool_compat()

    with monkeypatch.context() as context:
        context.delitem(globals_map, "_gludd_static_pool_compat", raising=False)
        context.delitem(globals_map, "_create_engine", raising=False)
        _install_sqlite_async_pool_compat()

    assert globals_map.get("_gludd_static_pool_compat") is True


def test_queue_bootstrap_insert_selects_owned_dialects_fail_closed() -> None:
    """Select PostgreSQL explicitly and reject every unowned SQL dialect."""
    statement = _conflict_ignoring_insert(QueueModel, "postgresql")
    assert statement.table.name == "queues"

    with pytest.raises(ValueError, match="does not support SQL dialect 'mysql'"):
        _conflict_ignoring_insert(QueueModel, "mysql")


@pytest.mark.asyncio
async def test_read_only_postgresql_engine_skips_sqlite_provisioning() -> None:
    """Keep PostgreSQL schema ownership outside SQLite provisioning."""
    engine = init_read_only_engine_from_config(
        {"url": "postgresql+psycopg://localhost/gludd"}
    )
    try:
        assert engine.dialect.name == "postgresql"
    finally:
        await engine.dispose()
