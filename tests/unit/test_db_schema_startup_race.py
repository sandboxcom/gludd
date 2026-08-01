"""Concurrent SQLite startup must tolerate check-then-create DDL races."""

from __future__ import annotations

import asyncio

import pytest

from general_ludd.db.session import ensure_tables, init_async_engine


@pytest.mark.asyncio
async def test_concurrent_sqlite_workers_ensure_schema_idempotently(tmp_path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'startup-race.db'}"
    engines = [init_async_engine(url) for _ in range(4)]
    try:
        await asyncio.gather(*(ensure_tables(engine) for engine in engines))
        await asyncio.gather(*(ensure_tables(engine) for engine in engines))
    finally:
        await asyncio.gather(*(engine.dispose() for engine in engines))
