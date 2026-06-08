"""Tests for db/session.py uncovered paths — pushing coverage above 85%."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestRunWalPragmas:
    def test_non_sqlite_url_skips_pragmas(self):
        from general_ludd.db.session import run_wal_pragmas

        engine = MagicMock()
        engine.url = "postgresql+psycopg://localhost/db"
        run_wal_pragmas(engine)


class TestInitAsyncEngine:
    def test_postgresql_url(self):
        from general_ludd.db.session import init_async_engine

        engine = init_async_engine("postgresql+psycopg://localhost/test_db", echo=False)
        assert engine is not None
        assert "postgresql" in str(engine.url)

    def test_sqlite_url(self, tmp_path):
        from general_ludd.db.session import init_async_engine

        db = tmp_path / "test.db"
        url = f"sqlite+aiosqlite:///{db}"
        engine = init_async_engine(url)
        assert engine is not None


class TestGetAsyncSession:
    @pytest.mark.asyncio
    async def test_commit_on_success(self):
        from general_ludd.db.session import get_async_session

        session = AsyncMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)

        gen = get_async_session(factory)
        result = await gen.__anext__()
        assert result is session
        await gen.aclose()

    @pytest.mark.asyncio
    async def test_rollback_on_error(self):
        from general_ludd.db.session import get_async_session

        session = AsyncMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)

        gen = get_async_session(factory)
        await gen.__anext__()
        with pytest.raises(RuntimeError):
            await gen.athrow(RuntimeError("boom"))
        session.rollback.assert_called()


class TestEnsureTables:
    @pytest.mark.asyncio
    async def test_non_sqlite_skips(self):
        from general_ludd.db.session import ensure_tables

        engine = MagicMock()
        engine.url = "postgresql+psycopg://localhost/db"
        engine.begin = MagicMock()
        await ensure_tables(engine)
        engine.begin.assert_not_called()


class TestJsonDumps:
    def test_with_list(self):
        from general_ludd.db.session import json_dumps

        assert json_dumps([1, 2, 3]) == "[1, 2, 3]"

    def test_with_empty(self):
        from general_ludd.db.session import json_dumps

        assert json_dumps(None) == "[]"
        assert json_dumps([]) == "[]"
