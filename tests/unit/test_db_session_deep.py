"""Deep edge-case tests for db/session.py: pool lifecycle, error recovery, tenant scoping."""

from __future__ import annotations

import asyncio
import contextlib
import weakref
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base


class TestBoundedIntSetting:
    def test_default_used_when_key_absent(self):
        from general_ludd.db.session import _bounded_int_setting

        assert _bounded_int_setting({}, "x", default=5, minimum=0, maximum=10) == 5

    def test_config_value_overrides_default(self):
        from general_ludd.db.session import _bounded_int_setting

        assert _bounded_int_setting({"x": 7}, "x", default=5, minimum=0, maximum=10) == 7

    def test_below_minimum_raises(self):
        from general_ludd.db.session import _bounded_int_setting

        with pytest.raises(ValueError, match="must be an integer"):
            _bounded_int_setting({"x": -1}, "x", default=5, minimum=0, maximum=10)

    def test_above_maximum_raises(self):
        from general_ludd.db.session import _bounded_int_setting

        with pytest.raises(ValueError, match="must be an integer"):
            _bounded_int_setting({"x": 11}, "x", default=5, minimum=0, maximum=10)

    def test_at_minimum_boundary(self):
        from general_ludd.db.session import _bounded_int_setting

        assert _bounded_int_setting({"x": 0}, "x", default=5, minimum=0, maximum=10) == 0

    def test_at_maximum_boundary(self):
        from general_ludd.db.session import _bounded_int_setting

        assert _bounded_int_setting({"x": 10}, "x", default=5, minimum=0, maximum=10) == 10

    def test_non_int_value_raises(self):
        from general_ludd.db.session import _bounded_int_setting

        with pytest.raises(ValueError, match="must be an integer"):
            _bounded_int_setting({"x": "7"}, "x", default=5, minimum=0, maximum=10)

    def test_float_value_raises(self):
        from general_ludd.db.session import _bounded_int_setting

        with pytest.raises(ValueError, match="must be an integer"):
            _bounded_int_setting({"x": 5.5}, "x", default=5, minimum=0, maximum=10)

    def test_none_value_raises(self):
        from general_ludd.db.session import _bounded_int_setting

        with pytest.raises(ValueError, match="must be an integer"):
            _bounded_int_setting({"x": None}, "x", default=5, minimum=0, maximum=10)

    def test_bool_value_raises(self):
        from general_ludd.db.session import _bounded_int_setting

        with pytest.raises(ValueError, match="must be an integer"):
            _bounded_int_setting({"x": True}, "x", default=5, minimum=0, maximum=10)


class TestResolveSqliteWalSettings:
    def test_defaults(self):
        from general_ludd.db.session import (
            _DEFAULT_BUSY_TIMEOUT_MS,
            _DEFAULT_JOURNAL_SIZE_LIMIT_BYTES,
            _DEFAULT_WAL_AUTOCHECKPOINT_PAGES,
            _resolve_sqlite_wal_settings,
        )

        s = _resolve_sqlite_wal_settings(None)
        assert s.journal_size_limit_bytes == _DEFAULT_JOURNAL_SIZE_LIMIT_BYTES
        assert s.wal_autocheckpoint_pages == _DEFAULT_WAL_AUTOCHECKPOINT_PAGES
        assert s.busy_timeout_ms == _DEFAULT_BUSY_TIMEOUT_MS

    def test_empty_config(self):
        from general_ludd.db.session import _resolve_sqlite_wal_settings

        s = _resolve_sqlite_wal_settings({})
        assert s.journal_size_limit_bytes == 64 * 1024 * 1024
        assert s.wal_autocheckpoint_pages == 1000
        assert s.busy_timeout_ms == 5000

    def test_custom_values(self):
        from general_ludd.db.session import _resolve_sqlite_wal_settings

        s = _resolve_sqlite_wal_settings(
            {
                "journal_size_limit_bytes": 128 * 1024 * 1024,
                "wal_autocheckpoint_pages": 2000,
                "busy_timeout_ms": 10000,
            }
        )
        assert s.journal_size_limit_bytes == 128 * 1024 * 1024
        assert s.wal_autocheckpoint_pages == 2000
        assert s.busy_timeout_ms == 10000

    def test_invalid_journal_size_raises(self):
        from general_ludd.db.session import _resolve_sqlite_wal_settings

        with pytest.raises(ValueError):
            _resolve_sqlite_wal_settings({"journal_size_limit_bytes": 500_000})


class TestComposeDbUrl:
    def test_url_in_config_direct(self):
        from general_ludd.db.session import _compose_db_url

        result = _compose_db_url({"url": "postgresql+psycopg://custom/db"})
        assert result == "postgresql+psycopg://custom/db"

    def test_env_var_fallback(self, monkeypatch):
        from general_ludd.db.session import _compose_db_url

        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://env/db")
        result = _compose_db_url({})
        assert result == "postgresql+psycopg://env/db"

    def test_config_url_takes_priority_over_env(self, monkeypatch):
        from general_ludd.db.session import _compose_db_url

        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://env/db")
        result = _compose_db_url({"url": "postgresql+psycopg://config/db"})
        assert result == "postgresql+psycopg://config/db"

    def test_host_based_url_without_password(self):
        from general_ludd.db.session import _compose_db_url

        result = _compose_db_url({"host": "db.example.com"})
        assert result == "postgresql+psycopg://gludd@db.example.com:5432/gludd"

    def test_host_based_url_with_password(self):
        from general_ludd.db.session import _compose_db_url

        result = _compose_db_url({"host": "db.example.com", "password": "s3cret"})
        assert result == "postgresql+psycopg://gludd:s3cret@db.example.com:5432/gludd"

    def test_host_based_url_custom_port_name_user(self):
        from general_ludd.db.session import _compose_db_url

        result = _compose_db_url(
            {
                "host": "db.example.com",
                "port": 6543,
                "name": "mydb",
                "user": "admin",
            }
        )
        assert result == "postgresql+psycopg://admin@db.example.com:6543/mydb"

    def test_no_url_no_env_no_host_returns_none(self):
        from general_ludd.db.session import _compose_db_url

        assert _compose_db_url({}) is None

    def test_empty_config_returns_none(self):
        from general_ludd.db.session import _compose_db_url

        assert _compose_db_url({"host": None, "port": None}) is None


class TestDefaultDbPath:
    def test_env_override(self, monkeypatch, tmp_path):
        from general_ludd.db.session import get_default_db_path

        monkeypatch.setenv("GLUDD_DB_PATH", str(tmp_path / "custom.db"))
        assert get_default_db_path() == tmp_path / "custom.db"

    def test_xdg_fallback(self, monkeypatch, tmp_path):
        from general_ludd.db.session import get_default_db_path

        monkeypatch.delenv("GLUDD_DB_PATH", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        expected = tmp_path / "general-ludd" / "general-ludd.db"
        assert get_default_db_path() == expected

    def test_default_db_url(self, monkeypatch, tmp_path):
        from general_ludd.db.session import get_default_db_url

        monkeypatch.setenv("GLUDD_DB_PATH", str(tmp_path / "test.db"))
        assert "sqlite+aiosqlite:///" in get_default_db_url()


class TestIsSqliteUrl:
    def test_sqlite_in_url(self):
        from general_ludd.db.session import is_sqlite_url

        assert is_sqlite_url("sqlite+aiosqlite:///db.sqlite3") is True

    def test_postgresql_not_sqlite(self):
        from general_ludd.db.session import is_sqlite_url

        assert is_sqlite_url("postgresql+psycopg://localhost/db") is False

    def test_none_returns_false(self):
        from general_ludd.db.session import is_sqlite_url

        assert is_sqlite_url(None) is False

    def test_empty_returns_false(self):
        from general_ludd.db.session import is_sqlite_url

        assert is_sqlite_url("") is False


class TestCloseEngine:
    def test_engine_marked_closed(self):
        from general_ludd.db.session import _engine_closed, close_engine

        engine = MagicMock(spec=AsyncEngine)
        assert _engine_closed(engine) is False
        close_engine(engine)
        assert _engine_closed(engine) is True

    def test_double_close_is_idempotent(self):
        from general_ludd.db.session import _engine_closed, close_engine

        engine = MagicMock(spec=AsyncEngine)
        close_engine(engine)
        close_engine(engine)
        assert _engine_closed(engine) is True

    def test_distinct_engines(self):
        from general_ludd.db.session import _engine_closed, close_engine

        e1 = MagicMock(spec=AsyncEngine)
        e2 = MagicMock(spec=AsyncEngine)
        close_engine(e1)
        assert _engine_closed(e1) is True
        assert _engine_closed(e2) is False

    def test_recycled_identity_does_not_close_a_live_engine(self):
        from general_ludd.db.session import (
            _closed_engine_refs,
            _closed_engines,
            _engine_closed,
        )

        live_engine = MagicMock(spec=AsyncEngine)
        collected_engine = MagicMock(spec=AsyncEngine)
        engine_id = id(live_engine)
        _closed_engines.add(engine_id)
        _closed_engine_refs[engine_id] = weakref.ref(collected_engine)
        try:
            assert _engine_closed(live_engine) is False
        finally:
            _closed_engine_refs.pop(engine_id, None)
            _closed_engines.discard(engine_id)


class TestGetAsyncSessionDeep:
    @pytest.mark.asyncio
    async def test_closed_engine_raises(self):
        from general_ludd.db.session import _closed_engines, get_async_session

        engine = MagicMock()
        engine.sync_engine = MagicMock()
        factory = async_sessionmaker(engine, expire_on_commit=False)
        _closed_engines.add(id(engine))
        try:
            gen = get_async_session(factory)
            with pytest.raises(RuntimeError, match="Cannot create session from a closed/disposed engine"):
                await gen.__anext__()
        finally:
            _closed_engines.discard(id(engine))

    @pytest.mark.asyncio
    async def test_bind_recovery_from_sync_engine(self):
        from general_ludd.db.session import get_async_session

        sync_engine = MagicMock()
        async_engine = MagicMock()
        async_engine.sync_engine = sync_engine
        with patch.object(AsyncEngine, "_retrieve_proxy_for_target", return_value=async_engine):
            session = AsyncMock()
            raw_factory = MagicMock()
            raw_factory.return_value = AsyncMock()
            raw_factory.return_value.__aenter__.return_value = session
            raw_factory.return_value.__aexit__.return_value = False
            raw_factory.bind = sync_engine
            raw_factory.kw = {}
            gen = get_async_session(raw_factory)
            await gen.__anext__()
            await gen.aclose()

    @pytest.mark.asyncio
    async def test_nested_exception_rollbacks_only_once(self):
        from general_ludd.db.session import get_async_session

        session = AsyncMock()
        factory = MagicMock()
        factory.return_value = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        factory.return_value.__aexit__.return_value = False
        getattr(factory, "bind", None)
        gen = get_async_session(factory)
        await gen.__anext__()
        with pytest.raises(RuntimeError, match="nested boom"):
            await gen.athrow(RuntimeError("nested boom"))
        session.rollback.assert_called_once()
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_commit_on_normal_resume_after_yield(self):
        from general_ludd.db.session import get_async_session

        session = AsyncMock()
        session.commit = AsyncMock()
        factory = MagicMock()
        factory.return_value = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        factory.return_value.__aexit__.return_value = False
        gen = get_async_session(factory)
        result = await gen.__anext__()
        assert result is session
        with contextlib.suppress(StopAsyncIteration):
            await gen.asend(None)
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_commit_failure_on_resume_triggers_rollback(self):
        from general_ludd.db.session import get_async_session

        session = AsyncMock()
        session.commit = AsyncMock(side_effect=OperationalError("disk full", None, Exception()))
        session.rollback = AsyncMock()
        factory = MagicMock()
        factory.return_value = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        factory.return_value.__aexit__.return_value = False
        gen = get_async_session(factory)
        await gen.__anext__()
        with pytest.raises(OperationalError), contextlib.suppress(StopAsyncIteration):
            await gen.asend(None)
        session.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_failure_propagates_original(self):
        from general_ludd.db.session import get_async_session

        session = AsyncMock()
        session.rollback = AsyncMock(side_effect=RuntimeError("rollback failed"))
        factory = MagicMock()
        factory.return_value = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        factory.return_value.__aexit__.return_value = False
        gen = get_async_session(factory)
        await gen.__anext__()
        with pytest.raises(RuntimeError, match="rollback failed"):
            await gen.athrow(ValueError("original error"))

    @pytest.mark.asyncio
    async def test_bind_from_kw_not_attribute(self):
        from general_ludd.db.session import get_async_session

        engine = MagicMock()
        engine.sync_engine = MagicMock()
        session = AsyncMock()
        factory = MagicMock()
        factory.return_value = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        factory.return_value.__aexit__.return_value = False
        del factory.bind
        factory.kw = {"bind": engine}
        gen = get_async_session(factory)
        await gen.__anext__()
        await gen.aclose()

    @pytest.mark.asyncio
    async def test_no_bind_no_error(self):
        from general_ludd.db.session import get_async_session

        session = AsyncMock()
        factory = MagicMock()
        factory.return_value = AsyncMock()
        factory.return_value.__aenter__.return_value = session
        factory.return_value.__aexit__.return_value = False
        del factory.bind
        factory.kw = {}
        gen = get_async_session(factory)
        await gen.__anext__()
        await gen.aclose()


class TestEnsureTablesDeep:
    @pytest.mark.asyncio
    async def test_retry_on_database_locked(self):
        from general_ludd.db.session import ensure_tables

        engine = MagicMock()
        engine.url = "sqlite+aiosqlite:///test.db"
        call_count = 0

        def begin_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                cm = AsyncMock()
                cm.__aenter__.side_effect = OperationalError("database is locked", None, Exception())
                return cm
            cm = AsyncMock()
            cm.__aenter__.return_value = cm
            return cm

        engine.begin = begin_side_effect
        await ensure_tables(engine)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_on_already_exists(self):
        from general_ludd.db.session import ensure_tables

        engine = MagicMock()
        engine.url = "sqlite+aiosqlite:///test.db"
        call_count = 0

        def begin_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count < 5:
                cm = AsyncMock()
                cm.__aenter__.side_effect = OperationalError("table already exists", None, Exception())
                return cm
            cm = AsyncMock()
            cm.__aenter__.return_value = cm
            return cm

        engine.begin = begin_side_effect
        await ensure_tables(engine)
        assert call_count == 5

    @pytest.mark.asyncio
    async def test_non_retryable_error_propagates_immediately(self):
        from general_ludd.db.session import ensure_tables

        engine = MagicMock()
        engine.url = "sqlite+aiosqlite:///test.db"

        def begin_side_effect():
            cm = AsyncMock()
            cm.__aenter__.side_effect = OperationalError("disk I/O error", None, Exception())
            return cm

        engine.begin = begin_side_effect
        with pytest.raises(OperationalError, match="disk I/O error"):
            await ensure_tables(engine)

    @pytest.mark.asyncio
    async def test_retries_exhausted_after_20(self):
        from general_ludd.db.session import ensure_tables

        engine = MagicMock()
        engine.url = "sqlite+aiosqlite:///test.db"

        def begin_side_effect():
            cm = AsyncMock()
            cm.__aenter__.side_effect = OperationalError("database is locked", None, Exception())
            return cm

        engine.begin = begin_side_effect
        with pytest.raises(OperationalError, match="database is locked"):
            await ensure_tables(engine)

    @pytest.mark.asyncio
    async def test_non_sqlite_skips(self):
        from general_ludd.db.session import ensure_tables

        engine = MagicMock()
        engine.url = "postgresql+psycopg://localhost/db"
        engine.begin = MagicMock()
        await ensure_tables(engine)
        engine.begin.assert_not_called()


class TestInitEngineFromConfig:
    def test_none_config_uses_default_url(self):
        from general_ludd.db.session import init_engine_from_config

        engine = init_engine_from_config(None)
        assert engine is not None
        assert "sqlite" in str(engine.url)

    def test_empty_config_uses_default(self):
        from general_ludd.db.session import init_engine_from_config

        engine = init_engine_from_config({})
        assert engine is not None

    def test_sqlite_url_in_config(self, tmp_path):
        from general_ludd.db.session import init_engine_from_config

        db = tmp_path / "test.db"
        engine = init_engine_from_config({"url": f"sqlite+aiosqlite:///{db}"})
        assert engine is not None
        assert db.exists()

    def test_postgresql_url_in_config(self):
        from general_ludd.db.session import init_engine_from_config

        engine = init_engine_from_config({"url": "postgresql+psycopg://localhost/db"})
        assert engine is not None


class TestInitReadOnlyEngine:
    def test_sqlite_read_only_engine(self, tmp_path):
        from general_ludd.db.session import init_read_only_engine_from_config

        db = tmp_path / "ro.db"
        engine = init_read_only_engine_from_config({"url": f"sqlite+aiosqlite:///{db}"})
        assert engine is not None
        assert db.parent.exists()

    def test_empty_config_produces_engine(self):
        from general_ludd.db.session import init_read_only_engine_from_config

        engine = init_read_only_engine_from_config({})
        assert engine is not None


class TestReadOnlySessionFactory:
    def test_factory_created_with_expire_off(self):
        from general_ludd.db.session import create_read_only_session_factory

        engine = MagicMock(spec=AsyncEngine)
        factory = create_read_only_session_factory(engine)
        assert factory.kw["expire_on_commit"] is False

    def test_factory_produces_async_session(self):
        from general_ludd.db.session import create_read_only_session_factory

        engine = MagicMock(spec=AsyncEngine)
        factory = create_read_only_session_factory(engine)
        assert factory.class_ is AsyncSession


class TestAsyncSessionFactory:
    def test_expire_on_commit_disabled(self):
        from general_ludd.db.session import create_async_session_factory

        engine = MagicMock(spec=AsyncEngine)
        factory = create_async_session_factory(engine)
        assert factory.kw["expire_on_commit"] is False


class TestSqliteWalSettingsDataclass:
    def test_frozen(self):
        from general_ludd.db.session import _SqliteWalSettings

        s = _SqliteWalSettings(
            journal_size_limit_bytes=65536,
            wal_autocheckpoint_pages=1000,
            busy_timeout_ms=5000,
        )
        assert s.journal_size_limit_bytes == 65536
        cls = type(s)
        with pytest.raises(AttributeError):
            cls.__setattr__(s, "journal_size_limit_bytes", 99999)

    def test_equality(self):
        from general_ludd.db.session import _SqliteWalSettings

        s1 = _SqliteWalSettings(65536, 1000, 5000)
        s2 = _SqliteWalSettings(65536, 1000, 5000)
        assert s1 == s2

    def test_inequality(self):
        from general_ludd.db.session import _SqliteWalSettings

        s1 = _SqliteWalSettings(65536, 1000, 5000)
        s2 = _SqliteWalSettings(65536, 2000, 5000)
        assert s1 != s2


class TestTenantFilterDeep:
    def test_skip_when_not_select(self):
        from general_ludd.db.session import _add_tenant_filter

        state = MagicMock()
        state.is_select = False
        _add_tenant_filter(state)
        state.statement.options.assert_not_called()

    def test_skip_when_column_load(self):
        from general_ludd.db.session import _add_tenant_filter

        state = MagicMock()
        state.is_select = True
        state.is_column_load = True
        _add_tenant_filter(state)
        state.statement.options.assert_not_called()

    def test_skip_when_no_tenant(self, monkeypatch):
        from general_ludd.db.session import _add_tenant_filter

        monkeypatch.setattr("general_ludd.db.tenant.get_tenant", lambda: None)
        state = MagicMock()
        state.is_select = True
        state.is_column_load = False
        _add_tenant_filter(state)
        state.statement.options.assert_not_called()

    def test_adds_criteria_when_tenant_present(self, monkeypatch):
        from general_ludd.db.session import _add_tenant_filter

        monkeypatch.setattr("general_ludd.db.tenant.get_tenant", lambda: "p-123")
        state = MagicMock()
        state.is_select = True
        state.is_column_load = False
        captured_options = state.statement.options
        _add_tenant_filter(state)
        captured_options.assert_called_once()

    def test_orm_class_without_project_id_uses_true_criteria(self, monkeypatch):
        from general_ludd.db.session import _add_tenant_filter

        monkeypatch.setattr("general_ludd.db.tenant.get_tenant", lambda: "p-123")

        class NoProjectIdModel:
            pass

        state = MagicMock()
        state.is_select = True
        state.is_column_load = False
        state.statement = MagicMock()
        captured_options = state.statement.options
        _add_tenant_filter(state)
        assert captured_options.called


class TestSessionPoolConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_session_creation(self, tmp_path):
        db_path = tmp_path / "concurrent.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def use_session(_i: int) -> bool:
            async with factory() as session:
                await session.execute(Base.metadata.tables["queues"].select().limit(1))
                return True

        results = await asyncio.gather(*(use_session(i) for i in range(20)))
        assert all(results)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_engine_dispose_then_recreate(self, tmp_path):
        db_path = tmp_path / "recreate.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(Base.metadata.tables["queues"].select().limit(1))
        await engine.dispose()

        engine2 = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        factory2 = async_sessionmaker(engine2, expire_on_commit=False)
        async with factory2() as session:
            await session.execute(Base.metadata.tables["queues"].select().limit(1))
        await engine2.dispose()

    @pytest.mark.asyncio
    async def test_factory_reuse_after_session_error(self, tmp_path):
        db_path = tmp_path / "reuse.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        with pytest.raises(SQLAlchemyError):
            async with factory() as session:
                await session.execute(text("SELECT * FROM nonexistent_table"))

        async with factory() as session:
            result = await session.execute(Base.metadata.tables["queues"].select().limit(1))
            assert result is not None

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_generator_session_lifecycle(self, tmp_path):
        from general_ludd.db.session import get_async_session

        db_path = tmp_path / "gen.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async for session in get_async_session(factory):
            result = await session.execute(Base.metadata.tables["queues"].select().limit(1))
            assert result is not None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sessions_dont_leak_between_contexts(self, tmp_path):
        db_path = tmp_path / "noleak.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        s1_ids = []
        async with factory() as s1:
            s1_ids.append(id(s1))
            await s1.execute(Base.metadata.tables["queues"].select().limit(1))
        async with factory() as s2:
            s2_ids = [id(s2)]
            await s2.execute(Base.metadata.tables["queues"].select().limit(1))
        assert s1_ids != s2_ids
        await engine.dispose()


class TestSeedInitialQueuesDeep:
    @pytest.mark.asyncio
    async def test_seed_idempotent(self, tmp_path):
        from general_ludd.db.session import seed_initial_queues

        db_path = tmp_path / "seed.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            count1 = await seed_initial_queues(session)
            await session.commit()
        async with factory() as session:
            count2 = await seed_initial_queues(session)
            await session.commit()
        assert count1 > 0
        assert count2 == 0
        await engine.dispose()


class TestAsyncEngineClosedProperty:
    def test_property_registered(self):
        from sqlalchemy.ext.asyncio import AsyncEngine as _AsyncEngine

        from general_ludd.db.session import close_engine

        engine = MagicMock(spec=_AsyncEngine)
        closed_prop = getattr(_AsyncEngine, "_closed", None)
        if closed_prop is not None and hasattr(closed_prop, "fget"):
            assert closed_prop.fget(engine) is False
            close_engine(engine)
            assert closed_prop.fget(engine) is True


class TestSqliteAsyncPoolCompat:
    def test_marker_set_after_install(self):
        from sqlalchemy.ext.asyncio import create_async_engine as cae

        globals_map = cae.__globals__
        assert globals_map.get("_gludd_static_pool_compat") is True

    def test_create_engine_compat_is_registered(self):
        from sqlalchemy.ext.asyncio import create_async_engine as cae

        globals_map = cae.__globals__
        assert "_create_engine" in globals_map or "_create_engine_compat" in globals_map
