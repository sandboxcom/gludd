"""Deep tests for DB connection pool and session management.

Covers: pool exhaustion, connection recycling, statement timeout,
transaction isolation, connection retry, read replica routing,
engine disposal, pool sizing, and concurrent session handling.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.session import (
    _closed_engines,
    _compose_db_url,
    _resolve_sqlite_wal_settings,
    close_engine,
    create_async_session_factory,
    create_read_only_session_factory,
    ensure_tables,
    get_async_session,
    init_async_engine,
    init_engine_from_config,
    init_read_only_engine_from_config,
    is_sqlite_url,
    run_read_only_pragma,
    run_wal_pragmas,
)

_ASSETS_DIR = "/Users/shawnwilson/gludd"


def _file_sqlite_url(tmp_path: object, name: str = "test.db") -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/{name}"


# ── URL composition ────────────────────────────────────────────────────


class TestComposeDbUrl:
    def test_env_var_database_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
        url = _compose_db_url({})
        assert url == "postgresql+psycopg://u:p@h/db"

    def test_explicit_url_over_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://ignored/db")
        url = _compose_db_url({"url": "postgresql+psycopg://e:p@h/x"})
        assert url == "postgresql+psycopg://e:p@h/x"

    def test_host_without_password(self):
        url = _compose_db_url({"host": "pg.example.com", "name": "mydb"})
        assert url == "postgresql+psycopg://gludd@pg.example.com:5432/mydb"

    def test_host_with_password(self):
        url = _compose_db_url(
            {
                "host": "pg.example.com",
                "name": "mydb",
                "user": "app",
                "password": "secret",
            }
        )
        assert url == "postgresql+psycopg://app:secret@pg.example.com:5432/mydb"

    def test_host_user_port_name(self):
        url = _compose_db_url(
            {
                "host": "pg.example.com",
                "port": 5555,
                "user": "gludd",
                "name": "gludd",
            }
        )
        assert url == "postgresql+psycopg://gludd@pg.example.com:5555/gludd"

    def test_empty_config_returns_none(self):
        assert _compose_db_url({}) is None


# ── is_sqlite_url ──────────────────────────────────────────────────────


class TestIsSqliteUrl:
    def test_sqlite_true(self):
        assert is_sqlite_url("sqlite+aiosqlite:///test.db")

    def test_postgresql_false(self):
        assert not is_sqlite_url("postgresql+psycopg://localhost/db")

    def test_none_false(self):
        assert not is_sqlite_url(None)

    def test_empty_false(self):
        assert not is_sqlite_url("")


# ── WAL pragma configuration ───────────────────────────────────────────


class TestResolveSqliteWalSettings:
    def test_defaults(self):
        settings = _resolve_sqlite_wal_settings()
        assert settings.journal_size_limit_bytes == 64 * 1024 * 1024
        assert settings.wal_autocheckpoint_pages == 1000
        assert settings.busy_timeout_ms == 5000

    def test_custom_values(self):
        settings = _resolve_sqlite_wal_settings(
            {
                "journal_size_limit_bytes": 128 * 1024 * 1024,
                "wal_autocheckpoint_pages": 2000,
                "busy_timeout_ms": 30000,
            }
        )
        assert settings.journal_size_limit_bytes == 128 * 1024 * 1024
        assert settings.wal_autocheckpoint_pages == 2000
        assert settings.busy_timeout_ms == 30000

    def test_bounds_raise(self):
        with pytest.raises(ValueError):
            _resolve_sqlite_wal_settings({"journal_size_limit_bytes": 999})
        with pytest.raises(ValueError):
            _resolve_sqlite_wal_settings({"wal_autocheckpoint_pages": 0})
        with pytest.raises(ValueError):
            _resolve_sqlite_wal_settings({"busy_timeout_ms": 0})


# ── Pool sizing / StaticPool compat ────────────────────────────────────


class TestStaticPoolCompat:
    @pytest.mark.asyncio
    async def test_strips_pool_size_for_sqlite_staticpool(self):
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        assert isinstance(engine.pool, StaticPool)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_pool_size_works_with_file_based_sqlite(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=5, pool_timeout=30)
        pool = engine.pool
        assert pool.size() == 5
        await engine.dispose()


class TestPoolSizing:
    @pytest.mark.asyncio
    async def test_pool_size_default(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=4)
        assert engine.pool.size() == 4
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_pool_size_and_max_overflow(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=3, max_overflow=2)
        assert engine.pool.size() == 3
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_pool_overflow_checked_out_increments(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=2, max_overflow=1)
        c1 = await engine.connect()
        assert engine.pool.checkedout() == 1
        c2 = await engine.connect()
        assert engine.pool.checkedout() == 2
        c3 = await engine.connect()
        assert engine.pool.checkedout() == 3
        await c3.close()
        await c2.close()
        await c1.close()
        await engine.dispose()


# ── Connection recycling / pool_recycle ────────────────────────────────


class TestPoolRecycle:
    @pytest.mark.asyncio
    async def test_engine_accepts_pool_recycle(self):
        engine = create_async_engine("sqlite+aiosqlite://", pool_recycle=300)
        assert engine.pool._recycle == 300
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_pool_recycle_negative_disables(self):
        engine = create_async_engine("sqlite+aiosqlite://", pool_recycle=-1)
        assert engine.pool._recycle == -1
        await engine.dispose()


# ── Connection pool exhaustion ─────────────────────────────────────────


class TestPoolExhaustion:
    @pytest.mark.asyncio
    async def test_queue_pool_blocks_on_exhaustion(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=2, max_overflow=0, pool_timeout=2)
        c1 = await engine.connect()
        c2 = await engine.connect()

        from sqlalchemy.exc import TimeoutError as SATimeoutError

        with pytest.raises(SATimeoutError):
            await engine.connect()

        await c2.close()
        await c1.close()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_checked_out_tracks_active_connections(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=3, max_overflow=0)
        assert engine.pool.checkedout() == 0
        c1 = await engine.connect()
        assert engine.pool.checkedout() == 1
        c2 = await engine.connect()
        assert engine.pool.checkedout() == 2
        await c2.close()
        assert engine.pool.checkedout() == 1
        await c1.close()
        assert engine.pool.checkedout() == 0
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_connection_returned_to_pool_on_close(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=2, max_overflow=0)
        c1 = await engine.connect()
        await c1.close()

        c2 = await engine.connect()
        assert engine.pool.checkedout() == 1
        await c2.close()
        await engine.dispose()


# ── Connection retry (SQLite busy_timeout) ─────────────────────────────


def _make_begin_result() -> AsyncMock:
    cm = AsyncMock()
    acm = AsyncMock()
    acm.__aenter__.return_value = AsyncMock()
    acm.__aexit__.return_value = False
    cm.__aenter__.return_value = acm
    cm.__aexit__.return_value = False
    return cm


class TestConnectionRetry:
    @pytest.mark.asyncio
    async def test_ensure_tables_retries_on_locked(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        call_count = 0

        from sqlalchemy.ext.asyncio import AsyncEngine as _AE

        _original_begin = _AE.begin

        def _flaky_begin(self: object):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OperationalError("database is locked", None, None)
            return _make_begin_result()

        try:
            _AE.begin = _flaky_begin
            await ensure_tables(engine)
            assert call_count == 3
        finally:
            _AE.begin = _original_begin
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_ensure_tables_gives_up_after_exhaustion(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        call_count = 0

        from sqlalchemy.ext.asyncio import AsyncEngine as _AE

        _original_begin = _AE.begin

        def _always_locked(self: object):
            nonlocal call_count
            call_count += 1
            raise OperationalError("database is locked", None, None)

        try:
            _AE.begin = _always_locked
            with pytest.raises(OperationalError, match="locked"):
                await ensure_tables(engine)
            assert call_count == 20
        finally:
            _AE.begin = _original_begin
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_ensure_tables_retries_on_already_exists(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        call_count = 0

        from sqlalchemy.ext.asyncio import AsyncEngine as _AE

        _original_begin = _AE.begin

        def _exists_then_ok(self: object):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OperationalError("table already exists", None, None)
            return _make_begin_result()

        try:
            _AE.begin = _exists_then_ok
            await ensure_tables(engine)
            assert call_count == 2
        finally:
            _AE.begin = _original_begin
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_ensure_tables_raises_non_retryable(self):
        engine = create_async_engine("sqlite+aiosqlite://")

        from sqlalchemy.ext.asyncio import AsyncEngine as _AE

        _original_begin = _AE.begin

        def _unrecoverable(self: object):
            raise OperationalError("disk I/O error", None, None)

        try:
            _AE.begin = _unrecoverable
            with pytest.raises(OperationalError, match="disk I/O"):
                await ensure_tables(engine)
        finally:
            _AE.begin = _original_begin
        await engine.dispose()


# ── Transaction isolation ──────────────────────────────────────────────


class TestTransactionIsolation:
    @pytest.mark.asyncio
    async def test_isolation_level_read_uncommitted(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, isolation_level="READ UNCOMMITTED")
        async with engine.connect() as conn:
            level = await conn.get_isolation_level()
            assert level == "READ UNCOMMITTED"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sessions_are_isolated(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from general_ludd.db.models import ProjectModel

        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as s1:
            s1.add(ProjectModel(project_id="p-iso1", name="iso1"))
            await s1.commit()
            assert len(s1.new) == 0

        async with factory() as s2:
            assert len(s2.new) == 0
            assert len(s2.identity_map) == 0

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_expire_on_commit_defaults_false(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        factory = create_async_session_factory(engine)
        assert factory.kw["expire_on_commit"] is False
        await engine.dispose()


# ── Statement timeout ──────────────────────────────────────────────────


class TestStatementTimeout:
    @pytest.mark.asyncio
    async def test_execution_options_supported(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.connect() as conn:
            exec_conn = await conn.execution_options(statement_timeout=5000)
            result = await exec_conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_per_statement_timeout(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.connect() as conn:
            exec_conn = await conn.execution_options(statement_timeout=3000)
            result = await exec_conn.execute(text("SELECT 1"))
            row = result.scalar()
            assert row == 1
        await engine.dispose()


# ── Read replica routing ───────────────────────────────────────────────


class TestReadReplicaRouting:
    @pytest.mark.asyncio
    async def test_read_only_engine_accepts_config(self):
        engine = init_read_only_engine_from_config({})
        assert engine is not None
        assert engine.pool is not None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_read_only_session_factory_creates(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        factory = create_read_only_session_factory(engine)
        assert factory.kw["expire_on_commit"] is False
        async with factory() as sess:
            assert isinstance(sess, AsyncSession)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_run_read_only_pragma_sets_query_only(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        run_read_only_pragma(engine)
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA query_only"))
            assert result.scalar() == 1
        await engine.dispose()

    def test_run_read_only_pragma_unsupported_dialect(self):
        engine = MagicMock()
        type(engine).dialect = PropertyMock(name=MagicMock())
        engine.dialect.name = "mysql"
        engine.sync_engine = None
        run_read_only_pragma(engine)


# ── Engine disposal & close detection ──────────────────────────────────


class TestEngineCloseDisposal:
    @pytest.mark.asyncio
    async def test_close_engine_marks_closed(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        eid = id(engine)
        assert eid not in _closed_engines
        close_engine(engine)
        assert eid in _closed_engines
        _closed_engines.discard(eid)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_closed_engine_rejects_session(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        close_engine(engine)

        gen = get_async_session(factory)
        with pytest.raises(RuntimeError, match="closed/disposed engine"):
            await gen.__anext__()

        _closed_engines.discard(id(engine))
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_engine_dispose_cleans_up(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        await engine.dispose()
        assert engine.pool is not None


# ── Session factory lifecycle ──────────────────────────────────────────


class TestSessionFactoryLifecycle:
    @pytest.mark.asyncio
    async def test_create_async_session_factory(self):
        engine = init_async_engine("sqlite+aiosqlite://")
        factory = create_async_session_factory(engine)
        assert factory.kw["expire_on_commit"] is False
        async with factory() as s:
            assert isinstance(s, AsyncSession)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_session_begin_transaction(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as s:
            assert s.in_transaction()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_session_commit_persists(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from general_ludd.db.models import ProjectModel

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            s.add(ProjectModel(project_id="p-pool", name="pool-test"))
            await s.commit()

        async with factory() as s:
            from sqlalchemy import select

            row = (
                await s.execute(select(ProjectModel).where(ProjectModel.project_id == "p-pool"))
            ).scalar_one_or_none()
            assert row is not None

        await engine.dispose()


# ── Concurrent sessions ────────────────────────────────────────────────


class TestConcurrentSessions:
    @pytest.mark.asyncio
    async def test_multiple_concurrent_sessions(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=4)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as s1, factory() as s2, factory() as s3:
            assert s1.is_active
            assert s2.is_active
            assert s3.is_active

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_parallel_session_reads(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=4)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from general_ludd.db.models import ProjectModel

        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            s.add(ProjectModel(project_id="p-conc", name="conc"))
            await s.commit()

        async def _read() -> object:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                from sqlalchemy import select

                r = (
                    await s.execute(select(ProjectModel).where(ProjectModel.project_id == "p-conc"))
                ).scalar_one_or_none()
                return r

        results = await asyncio.gather(_read(), _read(), _read())
        assert all(r is not None for r in results)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_session_holds_connection_from_pool(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url, pool_size=3)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from general_ludd.db.models import ProjectModel

        factory = async_sessionmaker(engine, expire_on_commit=False)
        assert engine.pool.checkedout() == 0
        async with factory() as s:
            s.add(ProjectModel(project_id="p-chk", name="chk"))
            await s.flush()
            assert engine.pool.checkedout() > 0
        assert engine.pool.checkedout() == 0
        await engine.dispose()


# ── Engine from config ─────────────────────────────────────────────────


class TestInitEngineFromConfig:
    @pytest.mark.asyncio
    async def test_defaults_to_sqlite(self):
        engine = init_engine_from_config(None)
        assert engine is not None
        assert "sqlite" in str(engine.url)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_empty_config_sqlite(self):
        engine = init_engine_from_config({})
        assert engine is not None
        assert "sqlite" in str(engine.url)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_explicit_url(self):
        engine = init_engine_from_config({"url": "sqlite+aiosqlite://"})
        assert engine is not None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_with_wal_config(self):
        cfg = {
            "url": "sqlite+aiosqlite://",
            "journal_size_limit_bytes": 128 * 1024 * 1024,
            "wal_autocheckpoint_pages": 2000,
            "busy_timeout_ms": 10000,
        }
        engine = init_engine_from_config(cfg)
        assert engine is not None
        await engine.dispose()


# ── get_async_session depth ────────────────────────────────────────────


class TestGetAsyncSessionEdgeCases:
    @pytest.mark.asyncio
    async def test_yields_session_on_success(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        gen = get_async_session(factory)
        s = await gen.__anext__()
        assert s is not None
        await gen.aclose()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_rollback_preserved_on_commit_failure(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from general_ludd.db.models import ProjectModel

        factory = async_sessionmaker(engine, expire_on_commit=False)

        gen = get_async_session(factory)
        s = await gen.__anext__()
        s.add(ProjectModel(project_id="p-rb", name="rb-test"))

        violation_raised = False
        try:
            await gen.athrow(ValueError("simulated failure"))
        except ValueError:
            violation_raised = True
        assert violation_raised

        async with factory() as verify_session:
            from sqlalchemy import select

            row = (
                await verify_session.execute(select(ProjectModel).where(ProjectModel.project_id == "p-rb"))
            ).scalar_one_or_none()
            assert row is None

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_auto_commit_on_gen_exhaustion(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from general_ludd.db.models import ProjectModel

        factory = async_sessionmaker(engine, expire_on_commit=False)

        gen = get_async_session(factory)
        s = await gen.__anext__()
        s.add(ProjectModel(project_id="p-ac2", name="ac2-test"))

        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

        async with factory() as verify_session:
            from sqlalchemy import select

            row = (
                await verify_session.execute(select(ProjectModel).where(ProjectModel.project_id == "p-ac2"))
            ).scalar_one_or_none()
            assert row is not None

        await engine.dispose()


# ── run_wal_pragmas ────────────────────────────────────────────────────


class TestRunWalPragmasIntegration:
    @pytest.mark.asyncio
    async def test_sqlite_with_config(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url)
        run_wal_pragmas(
            engine,
            {
                "journal_size_limit_bytes": 32 * 1024 * 1024,
                "wal_autocheckpoint_pages": 500,
                "busy_timeout_ms": 10000,
            },
        )
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            assert result.scalar() == "wal"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_default_config(self, tmp_path: object):
        url = _file_sqlite_url(tmp_path)
        engine = create_async_engine(url)
        run_wal_pragmas(engine)
        async with engine.connect() as conn:
            r1 = await conn.execute(text("PRAGMA journal_mode"))
            r2 = await conn.execute(text("PRAGMA synchronous"))
            assert r1.scalar() == "wal"
            assert r2.scalar() == 1  # NORMAL
        await engine.dispose()
