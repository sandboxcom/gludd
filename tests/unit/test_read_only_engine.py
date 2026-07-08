"""Tests for read-only engine factory — Phase 1 of gunicorn multi-worker.

HTTP workers need read-only DB engines so they can serve reads without
competing for the single write lock. The read-only engine enforces
PRAGMA query_only=ON at the SQLite connection level.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from general_ludd.db.session import (
    create_async_session_factory,
    create_read_only_session_factory,
    init_engine_from_config,
    init_read_only_engine_from_config,
)


def _sqlite_url(tmp_path) -> str:
    db = tmp_path / "test.db"
    return f"sqlite+aiosqlite:///{db}"


class TestReadOnlyEnginePragma:
    @pytest.mark.asyncio
    async def test_read_only_engine_sets_query_only_pragma(self, tmp_path):
        engine = init_read_only_engine_from_config({"url": _sqlite_url(tmp_path)})
        try:
            async with engine.connect() as conn:
                value = await conn.execute(text("PRAGMA query_only"))
                assert value.scalar() == 1
        finally:
            await engine.dispose()


class TestReadOnlyEngineWriteBlocked:
    @pytest.mark.asyncio
    async def test_read_only_engine_write_raises(self, tmp_path):
        url = _sqlite_url(tmp_path)
        write_engine = init_engine_from_config({"url": url})
        try:
            async with write_engine.begin() as conn:
                await conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        finally:
            await write_engine.dispose()

        read_engine = init_read_only_engine_from_config({"url": url})
        try:
            async with read_engine.begin() as conn:
                with pytest.raises(OperationalError):
                    await conn.execute(text("INSERT INTO t (id) VALUES (1)"))
        finally:
            await read_engine.dispose()


class TestReadOnlyFactoryIndependence:
    @pytest.mark.asyncio
    async def test_read_only_factory_independent_from_write_factory(self, tmp_path):
        url = _sqlite_url(tmp_path)
        write_engine = init_engine_from_config({"url": url})
        read_engine = init_read_only_engine_from_config({"url": url})
        write_factory = create_async_session_factory(write_engine)
        read_factory = create_read_only_session_factory(read_engine)
        try:
            async with write_factory() as session:
                await session.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
                await session.execute(text("INSERT INTO t (id) VALUES (1)"))
                await session.commit()

            async with read_factory() as session:
                with pytest.raises(OperationalError):
                    await session.execute(text("INSERT INTO t (id) VALUES (2)"))
        finally:
            await write_engine.dispose()
            await read_engine.dispose()


class TestReadOnlyEngineValidation:
    def test_non_sqlite_url_refused(self):
        with pytest.raises(ValueError, match="SQLite only"):
            init_read_only_engine_from_config({"url": "postgresql+psycopg://localhost/db"})
