"""Tests for SQLite default database with WAL mode."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.session import (
    get_default_db_path,
    get_default_db_url,
    init_engine_from_config,
    is_sqlite_url,
    run_wal_pragmas,
)


class TestSqliteDetection:
    def test_sqlite_url_detected(self):
        assert is_sqlite_url("sqlite+aiosqlite:///path/to/db.sqlite")

    def test_in_memory_detected(self):
        assert is_sqlite_url("sqlite+aiosqlite://")

    def test_postgres_not_detected(self):
        assert not is_sqlite_url("postgresql+psycopg://localhost/gludd")

    def test_none_not_detected(self):
        assert not is_sqlite_url(None)

    def test_empty_not_detected(self):
        assert not is_sqlite_url("")


class TestDefaultDbPath:
    def test_default_path_under_xdg_data(self):
        path = get_default_db_path()
        assert "general-ludd" in str(path)
        assert str(path).endswith("general-ludd.db")

    def test_custom_path_from_env(self, tmp_path):
        custom = tmp_path / "custom.db"
        with patch.dict(os.environ, {"GLUDD_DB_PATH": str(custom)}):
            path = get_default_db_path()
            assert path == custom

    def test_default_db_url_uses_aiosqlite(self):
        url = get_default_db_url()
        assert url.startswith("sqlite+aiosqlite:///")


class TestWalModePragmas:
    @pytest.mark.asyncio
    async def test_wal_mode_set_on_connect(self, tmp_path):
        db_path = tmp_path / "test_wal.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url)
        run_wal_pragmas(engine)
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            mode = result.scalar()
            assert mode == "wal"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_busy_timeout_set(self, tmp_path):
        db_path = tmp_path / "test_busy.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url)
        run_wal_pragmas(engine)
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA busy_timeout"))
            timeout = result.scalar()
            assert timeout == 5000
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_synchronous_normal(self, tmp_path):
        db_path = tmp_path / "test_sync.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url)
        run_wal_pragmas(engine)
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA synchronous"))
            sync_level = result.scalar()
            assert sync_level in (1, "NORMAL")
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_foreign_keys_enabled(self, tmp_path):
        db_path = tmp_path / "test_fk.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url)
        run_wal_pragmas(engine)
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            fk = result.scalar()
            assert fk == 1
        await engine.dispose()


class TestInitEngineFromConfig:
    def test_empty_config_returns_sqlite(self):
        engine = init_engine_from_config({})
        url = str(engine.url)
        assert "sqlite" in url
        engine.sync_engine.dispose()

    def test_none_config_returns_sqlite(self):
        engine = init_engine_from_config(None)
        url = str(engine.url)
        assert "sqlite" in url
        engine.sync_engine.dispose()

    def test_postgres_url_creates_postgres_engine(self):
        engine = init_engine_from_config({"url": "postgresql+psycopg://localhost/gludd"})
        url = str(engine.url)
        assert "postgresql" in url
        engine.sync_engine.dispose()

    def test_sqlite_path_from_config(self, tmp_path):
        db_path = tmp_path / "from_config.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = init_engine_from_config({"url": url})
        assert "sqlite" in str(engine.url)
        engine.sync_engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_creates_parent_dirs(self, tmp_path):
        db_path = tmp_path / "nested" / "dir" / "test.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = init_engine_from_config({"url": url})
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        assert db_path.exists()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_auto_creates_tables(self, tmp_path):
        db_path = tmp_path / "auto_create.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = init_engine_from_config({"url": url})
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with engine.begin() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: sync_conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            )
            names = [row[0] for row in tables]
            assert "todos" in names
            assert "projects" in names
        await engine.dispose()
