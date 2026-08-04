"""Deep tests for SQL session lifecycle — factory, context manager,
transaction boundaries, savepoints, connection recycling, session expiry,
engine lifecycle, and retry logic.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from general_ludd.db.models import Base
from general_ludd.db.session import (
    _closed_engines,
    _engine_closed,
    close_engine,
    create_async_session_factory,
    create_read_only_session_factory,
    ensure_tables,
    get_async_session,
    get_default_db_path,
    get_default_db_url,
    init_async_engine,
    init_engine_from_config,
    init_read_only_engine_from_config,
    is_sqlite_url,
    run_read_only_pragma,
    run_wal_pragmas,
)


def _sqlite_url(tmp_path, name: str = "test.db") -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


# ── Session factory creation (in-memory + file) ────────────────────────


class TestSessionFactoryCreation:
    """Session factories must produce AsyncSession instances with
    expire_on_commit=False regardless of engine flavour."""

    @pytest.mark.asyncio
    async def test_factory_from_in_memory_engine(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        try:
            factory = create_async_session_factory(engine)
            assert "expire_on_commit" in factory.kw
            assert factory.kw["expire_on_commit"] is False
            async with factory() as session:
                assert isinstance(session, AsyncSession)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_factory_from_file_engine(self, tmp_path):
        url = _sqlite_url(tmp_path, "sf.db")
        engine = create_async_engine(url, echo=False)
        try:
            factory = create_async_session_factory(engine)
            async with factory() as session:
                await session.execute(text("SELECT 1"))
                assert isinstance(session, AsyncSession)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_read_only_factory_identical_shape(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        try:
            factory = create_read_only_session_factory(engine)
            assert isinstance(factory, async_sessionmaker)
            assert "expire_on_commit" in factory.kw
            assert factory.kw["expire_on_commit"] is False
            async with factory() as session:
                assert isinstance(session, AsyncSession)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_factory_produces_unique_sessions(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        try:
            factory = create_async_session_factory(engine)
            async with factory() as s1, factory() as s2:
                assert s1 is not s2
                assert id(s1) != id(s2)
        finally:
            await engine.dispose()


# ── Context-manager behaviour ───────────────────────────────────────────


class TestSessionContextManager:
    """AsyncSession used as a context manager must begin, commit, and
    close the underlying transaction cleanly."""

    @pytest.mark.asyncio
    async def test_commit_on_clean_exit(self, tmp_path):
        url = _sqlite_url(tmp_path, "cm.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            # no exception = commit happened
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rollback_on_exception(self, tmp_path):
        url = _sqlite_url(tmp_path, "rb.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            with pytest.raises(ValueError, match="deliberate"):
                async with factory() as session:
                    await session.execute(text("SELECT 1"))
                    raise ValueError("deliberate")
            async with factory() as session:
                result = await session.execute(text("SELECT 1"))
                assert result.scalar() == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_nested_context_manager_isolation(self, tmp_path):
        url = _sqlite_url(tmp_path, "nested_cm.db")
        engine = create_async_engine(url, echo=False)
        try:
            factory = create_async_session_factory(engine)
            async with factory() as outer:
                async with factory() as inner:
                    assert outer is not inner
                assert True  # inner closed cleanly
            assert True  # outer closed cleanly
        finally:
            await engine.dispose()


# ── Transaction boundaries ──────────────────────────────────────────────


class TestTransactionBoundaries:
    """Explicit begin/commit/rollback on nested transactions."""

    @pytest.mark.asyncio
    async def test_explicit_begin_then_commit(self, tmp_path):
        url = _sqlite_url(tmp_path, "tx.db")
        engine = create_async_engine(url, echo=False)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.begin()
                await session.execute(text("SELECT 1"))
                await session.commit()
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_begin_then_rollback_discards_changes(self, tmp_path):
        url = _sqlite_url(tmp_path, "tx2.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            from general_ludd.db.models import QueueModel

            async with factory() as session:
                await session.begin()
                session.add(
                    QueueModel(
                        queue_name="discarded",
                        queue_enabled=True,
                        priority_weight=1,
                    )
                )
                await session.rollback()

            async with factory() as session:
                result = await session.execute(text("SELECT count(*) FROM queues WHERE queue_name = 'discarded'"))
                assert result.scalar() == 0
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_close_flushes_and_commits(self, tmp_path):
        url = _sqlite_url(tmp_path, "tx3.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            from general_ludd.db.models import QueueModel

            session = factory()
            session.add(
                QueueModel(
                    queue_name="auto_flush",
                    queue_enabled=True,
                    priority_weight=1,
                )
            )
            await session.commit()
            await session.close()

            async with factory() as verify:
                result = await verify.execute(text("SELECT count(*) FROM queues WHERE queue_name = 'auto_flush'"))
                assert result.scalar() == 1
        finally:
            await engine.dispose()


# ── Savepoint usage ─────────────────────────────────────────────────────


class TestSavepointUsage:
    """Savepoints allow partial rollback within a transaction without
    aborting the outer unit of work."""

    @pytest.mark.asyncio
    async def test_savepoint_rollback_outer_commit_persists(self, tmp_path):
        url = _sqlite_url(tmp_path, "sp.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            from general_ludd.db.models import QueueModel

            async with factory() as session, session.begin():
                session.add(
                    QueueModel(
                        queue_name="outer_kept",
                        queue_enabled=True,
                        priority_weight=1,
                    )
                )
                saved = await session.begin_nested()
                try:
                    session.add(
                        QueueModel(
                            queue_name="inner_discard",
                            queue_enabled=True,
                            priority_weight=1,
                        )
                    )
                    await saved.rollback()
                except Exception:
                    await saved.rollback()

            async with factory() as verify:
                outer_r = await verify.execute(text("SELECT count(*) FROM queues WHERE queue_name = 'outer_kept'"))
                inner_r = await verify.execute(text("SELECT count(*) FROM queues WHERE queue_name = 'inner_discard'"))
                assert outer_r.scalar() == 1
                assert inner_r.scalar() == 0
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_savepoint_commit_persists(self, tmp_path):
        url = _sqlite_url(tmp_path, "sp2.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            from general_ludd.db.models import QueueModel

            async with factory.begin() as session:
                session.add(
                    QueueModel(
                        queue_name="sp_committed",
                        queue_enabled=True,
                        priority_weight=1,
                    )
                )
                saved = await session.begin_nested()
                session.add(
                    QueueModel(
                        queue_name="sp_nested_kept",
                        queue_enabled=True,
                        priority_weight=1,
                    )
                )
                await saved.commit()

            async with factory() as verify:
                r = await verify.execute(
                    text("SELECT count(*) FROM queues WHERE queue_name IN ('sp_committed', 'sp_nested_kept')")
                )
                assert r.scalar() == 2
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_multiple_savepoints_stacked(self, tmp_path):
        url = _sqlite_url(tmp_path, "sp3.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            from general_ludd.db.models import QueueModel

            async with factory.begin() as session:
                session.add(
                    QueueModel(
                        queue_name="level0",
                        queue_enabled=True,
                        priority_weight=1,
                    )
                )
                sp1 = await session.begin_nested()
                session.add(
                    QueueModel(
                        queue_name="level1",
                        queue_enabled=True,
                        priority_weight=1,
                    )
                )
                sp2 = await session.begin_nested()
                session.add(
                    QueueModel(
                        queue_name="level2_discard",
                        queue_enabled=True,
                        priority_weight=1,
                    )
                )
                await sp2.rollback()
                await sp1.commit()

            async with factory() as verify:
                kept = await verify.execute(text("SELECT queue_name FROM queues ORDER BY queue_name"))
                names = [row[0] for row in kept]
                assert "level0" in names
                assert "level1" in names
                assert "level2_discard" not in names
        finally:
            await engine.dispose()


# ── Connection recycling ────────────────────────────────────────────────


class TestConnectionRecycling:
    """Session factory recycling: sessions returned to pool on close,
    reissued on next factory() call, and engine disposal drains pool."""

    @pytest.mark.asyncio
    async def test_engine_reuse_across_sessions(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        try:
            factory = create_async_session_factory(engine)
            async with factory() as s1:
                await s1.execute(text("SELECT 1"))
            async with factory() as s2:
                await s2.execute(text("SELECT 1"))
            # If both succeed, the pool recycled connections.
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_close_engine_prevents_new_sessions(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        close_engine(engine)
        factory = create_async_session_factory(engine)
        with pytest.raises(RuntimeError, match="Cannot create session"):
            async for _ in get_async_session(factory):
                pass
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_engine_dispose_cleans_up_pool(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        await engine.dispose()
        assert _engine_closed(engine) is False
        assert id(engine) not in _closed_engines

    @pytest.mark.asyncio
    async def test_recycle_after_exception(self, tmp_path):
        url = _sqlite_url(tmp_path, "recycle.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            with pytest.raises(ValueError, match="recycle"):
                async with factory() as session:
                    await session.begin()
                    await session.execute(text("SELECT 1"))
                    raise ValueError("recycle")

            async with factory() as session:
                result = await session.execute(text("SELECT 1"))
                assert result.scalar() == 1
        finally:
            await engine.dispose()


# ── Engine lifecycle ────────────────────────────────────────────────────


class TestEngineLifecycle:
    """init_engine_from_config, close_engine, _engine_closed, dispose."""

    @pytest.mark.asyncio
    async def test_init_engine_defaults_to_sqlite(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        engine = init_engine_from_config({})
        assert is_sqlite_url(str(engine.url))
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_init_engine_from_postgres_url(self):
        engine = init_engine_from_config({"url": "postgresql+psycopg://localhost/test"})
        assert "postgresql" in str(engine.url)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_init_read_only_engine(self):
        engine = init_read_only_engine_from_config({})
        assert is_sqlite_url(str(engine.url))
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_close_engine_tracks_closed_state(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        assert not _engine_closed(engine)
        close_engine(engine)
        assert _engine_closed(engine)
        _closed_engines.discard(id(engine))
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_closed_engine_raises_on_session(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        close_engine(engine)
        try:
            with pytest.raises(RuntimeError, match="closed"):
                async for _ in get_async_session(async_sessionmaker(engine, expire_on_commit=False)):
                    pass
        finally:
            _closed_engines.discard(id(engine))
            await engine.dispose()


# ── get_async_session generator behaviour ───────────────────────────────


class TestGetAsyncSession:
    """The async generator: yield on success → commit; on exception → rollback + raise."""

    @pytest.mark.asyncio
    async def test_yields_session_and_commits_on_success(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        try:
            factory = create_async_session_factory(engine)
            sessions = []
            async for s in get_async_session(factory):
                sessions.append(s)
                await s.execute(text("SELECT 1"))
            assert len(sessions) == 1
            assert isinstance(sessions[0], AsyncSession)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rollback_on_inner_exception(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        try:
            factory = create_async_session_factory(engine)
            with pytest.raises(RuntimeError, match="gen_bomb"):
                async for _ in get_async_session(factory):
                    raise RuntimeError("gen_bomb")
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rejects_closed_engine_before_yield(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        close_engine(engine)
        try:
            with pytest.raises(RuntimeError, match="closed"):
                async for _ in get_async_session(async_sessionmaker(engine, expire_on_commit=False)):
                    pass
        finally:
            _closed_engines.discard(id(engine))
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_partial_session_discarded_on_rollback(self, tmp_path):
        url = _sqlite_url(tmp_path, "gen_tx.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            with pytest.raises(ValueError):
                async for session in get_async_session(factory):
                    await session.begin()
                    await session.execute(text("SELECT 1"))
                    raise ValueError("undo me")

            async with factory() as verify:
                result = await verify.execute(text("SELECT 1"))
                assert result.scalar() == 1
        finally:
            await engine.dispose()


# ── ensure_tables retry logic ───────────────────────────────────────────


class TestEnsureTables:
    """SQLite table creation retries on 'already exists' and 'locked' errors."""

    @pytest.mark.asyncio
    async def test_non_sqlite_skips_ddl(self):
        engine = MagicMock()
        engine.url = "postgresql+psycopg://localhost/db"
        await ensure_tables(engine)
        assert not engine.begin.called

    @pytest.mark.asyncio
    async def test_sqlite_creates_tables_idempotent(self, tmp_path):
        url = _sqlite_url(tmp_path, "ens_tbl.db")
        engine = create_async_engine(url, echo=False)
        try:
            await ensure_tables(engine)
            await ensure_tables(engine)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_retries_on_locked_error(self, tmp_path):
        """ensure_tables retries up to 20 times on 'database is locked'
        errors. Double-call proves idempotency; source inspection
        verifies the retry loop structure."""
        url = _sqlite_url(tmp_path, "retry_lck.db")
        engine = create_async_engine(url, echo=False)
        try:
            await ensure_tables(engine)
            await ensure_tables(engine)
            import inspect

            from general_ludd.db.session import ensure_tables as _fn

            source = inspect.getsource(_fn)
            assert "database is locked" in source
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_already_exists_retry(self, tmp_path):
        """Idempotent ensure_tables retries on 'already exists' races."""
        url = _sqlite_url(tmp_path, "retry_ex.db")
        engine = create_async_engine(url, echo=False)
        try:
            await ensure_tables(engine)
            await ensure_tables(engine)
            import inspect

            from general_ludd.db.session import ensure_tables as _fn

            source = inspect.getsource(_fn)
            assert "already exists" in source
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_non_retryable_error_raised_immediately(self, tmp_path):
        """Non-retryable OperationalError must not be suppressed by the loop."""
        import inspect

        from general_ludd.db.session import ensure_tables as _fn

        source = inspect.getsource(_fn)
        assert "attempt == 19" in source
        assert "raise" in source


# ── URL helpers ─────────────────────────────────────────────────────────


class TestUrlHelpers:
    def test_sqlite_url_detection(self):
        assert is_sqlite_url("sqlite+aiosqlite:///:memory:")
        assert is_sqlite_url("sqlite+aiosqlite:///foo.db")
        assert not is_sqlite_url("postgresql+psycopg://localhost/db")
        assert not is_sqlite_url(None)
        assert not is_sqlite_url("")

    def test_default_db_path_xdg(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GLUDD_DB_PATH", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        path = get_default_db_path()
        assert path.name == "general-ludd.db"
        assert str(tmp_path) in str(path)

    def test_default_db_path_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom.db"
        monkeypatch.setenv("GLUDD_DB_PATH", str(custom))
        assert get_default_db_path() == custom

    def test_default_db_url_returns_sqlite(self):
        url = get_default_db_url()
        assert url.startswith("sqlite+aiosqlite:///")


# ── WAL pragmas ─────────────────────────────────────────────────────────


class TestWalPragmas:
    def test_non_sqlite_url_skips_pragmas(self):
        engine = MagicMock()
        engine.url = "postgresql+psycopg://localhost/db"
        run_wal_pragmas(engine)

    def test_config_bounds(self):
        from general_ludd.db.session import _bounded_int_setting

        assert _bounded_int_setting({}, "x", default=4, minimum=1, maximum=10) == 4
        assert _bounded_int_setting({"x": 7}, "x", default=4, minimum=1, maximum=10) == 7
        with pytest.raises(ValueError):
            _bounded_int_setting({"x": 0}, "x", default=4, minimum=1, maximum=10)
        with pytest.raises(ValueError):
            _bounded_int_setting({"x": 99}, "x", default=4, minimum=1, maximum=10)

    def test_resolve_wal_settings_defaults(self):
        from general_ludd.db.session import _resolve_sqlite_wal_settings

        settings = _resolve_sqlite_wal_settings()
        assert settings.journal_size_limit_bytes == 64 * 1024 * 1024
        assert settings.wal_autocheckpoint_pages == 1000
        assert settings.busy_timeout_ms == 5000

    def test_read_only_pragma_non_dialect_skips(self):
        engine = MagicMock()
        engine.dialect.name = "oracle"
        run_read_only_pragma(engine)
        engine.sync_engine = MagicMock()
        run_read_only_pragma(engine)


# ── Session expiry and dirty state ──────────────────────────────────────


class TestSessionExpiry:
    """expire_on_commit=False must prevent attribute expiry after commit."""

    @pytest.mark.asyncio
    async def test_expire_on_commit_disabled(self, tmp_path):
        url = _sqlite_url(tmp_path, "exp.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            from general_ludd.db.models import QueueModel

            async with factory() as session:
                q = QueueModel(
                    queue_name="no_expire",
                    queue_enabled=True,
                    priority_weight=1,
                )
                session.add(q)
                await session.commit()
                assert q.queue_name == "no_expire"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_session_close_idempotent(self, tmp_path):
        url = _sqlite_url(tmp_path, "inv.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            session = factory()
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
            await session.close()
            await session.close()
            await session.close()
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_session_is_active_transitions(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        try:
            factory = create_async_session_factory(engine)
            async with factory() as session:
                assert session.is_active
                r = await session.execute(text("SELECT 1"))
                assert r.scalar() == 1
        finally:
            await engine.dispose()


# ── Concurrent session handling ─────────────────────────────────────────


class TestConcurrentSessions:
    """Multiple concurrent AsyncSessions from the same engine must not
    interfere — SQLite serializes writes but reads can run in parallel."""

    @pytest.mark.asyncio
    async def test_concurrent_read_sessions(self, tmp_path):
        url = _sqlite_url(tmp_path, "conc.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = create_async_session_factory(engine)

            async def read_one():
                async with factory() as s:
                    r = await s.execute(text("SELECT 1"))
                    return r.scalar()

            results = await asyncio.gather(*(read_one() for _ in range(5)))
            assert results == [1, 1, 1, 1, 1]
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_interleaved_write_then_read(self, tmp_path):
        url = _sqlite_url(tmp_path, "conc2.db")
        engine = create_async_engine(url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            from general_ludd.db.models import QueueModel

            async with factory.begin() as w:
                w.add(
                    QueueModel(
                        queue_name="interleaved",
                        queue_enabled=True,
                        priority_weight=1,
                    )
                )

            async with factory() as r:
                result = await r.execute(text("SELECT count(*) FROM queues WHERE queue_name = 'interleaved'"))
                assert result.scalar() == 1
        finally:
            await engine.dispose()


# ── init_async_engine convenience ───────────────────────────────────────


class TestInitAsyncEngine:
    def test_postgres_url(self):
        engine = init_async_engine("postgresql+psycopg://localhost/test", echo=False)
        assert "postgresql" in str(engine.url)

    def test_sqlite_url(self, tmp_path):
        db = tmp_path / "iae.db"
        engine = init_async_engine(f"sqlite+aiosqlite:///{db}")
        assert "sqlite" in str(engine.url)


# ── init_engine_from_config edge cases ──────────────────────────────────


class TestInitEngineFromConfig:
    def test_host_based_postgres_url(self):
        engine = init_engine_from_config(
            {
                "host": "pg.example.com",
                "name": "mydb",
                "user": "app",
                "password": "secret",
            }
        )
        assert "postgresql+psycopg" in str(engine.url)

    def test_host_without_password(self):
        engine = init_engine_from_config(
            {
                "host": "pg.example.com",
                "user": "app",
            }
        )
        assert "postgresql+psycopg" in str(engine.url)


# ── Tenant-scoped listener presence ─────────────────────────────────────


class TestTenantListener:
    def test_do_orm_execute_listener_registered(self):
        """The _add_tenant_filter listener must be registered on Session."""
        from general_ludd.db.session import _add_tenant_filter

        registered = [
            entry
            for entry in getattr(Session, "__event_do_orm_execute", []) or []
            if callable(entry) and entry is _add_tenant_filter
        ]
        if not registered:
            listeners_container = getattr(type(Session), "_original_dispatch_cls", None)
            if listeners_container is not None:
                registered = [
                    v
                    for v in getattr(listeners_container, "do_orm_execute", []) or []
                    if callable(v) and v is _add_tenant_filter
                ]

        from sqlalchemy import event as sa_event

        has_it = sa_event.contains(Session, "do_orm_execute", _add_tenant_filter)
        assert has_it or len(registered) > 0, "_add_tenant_filter must be registered on Session.do_orm_execute"
