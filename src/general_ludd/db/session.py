"""SQLAlchemy database engine and session management.

Supports SQLite (default) with WAL mode and PostgreSQL.
SQLite is used out-of-the-box with no external database required.
"""

from __future__ import annotations

import asyncio
import logging
import os
import weakref
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, with_loader_criteria

from general_ludd.db.models import Base, QueueModel
from general_ludd.schemas.queue import INITIAL_QUEUES

logger = logging.getLogger(__name__)

_DEFAULT_JOURNAL_SIZE_LIMIT_BYTES = 64 * 1024 * 1024
_MIN_JOURNAL_SIZE_LIMIT_BYTES = 1024 * 1024
_MAX_JOURNAL_SIZE_LIMIT_BYTES = 1024 * 1024 * 1024
_DEFAULT_WAL_AUTOCHECKPOINT_PAGES = 1000
_MAX_WAL_AUTOCHECKPOINT_PAGES = 100_000
_DEFAULT_BUSY_TIMEOUT_MS = 5000
_MAX_BUSY_TIMEOUT_MS = 60_000
_closed_engines: set[int] = set()
_closed_engine_refs: dict[int, weakref.ReferenceType[AsyncEngine]] = {}


@dataclass(frozen=True, slots=True)
class _SqliteWalSettings:
    journal_size_limit_bytes: int
    wal_autocheckpoint_pages: int
    busy_timeout_ms: int


def _bounded_int_setting(
    config: dict[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = config.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(
            f"database.{name} must be an integer between {minimum} and {maximum}"
        )
    return value


def _resolve_sqlite_wal_settings(
    config: dict[str, Any] | None = None,
) -> _SqliteWalSettings:
    cfg = config or {}
    return _SqliteWalSettings(
        journal_size_limit_bytes=_bounded_int_setting(
            cfg,
            "journal_size_limit_bytes",
            default=_DEFAULT_JOURNAL_SIZE_LIMIT_BYTES,
            minimum=_MIN_JOURNAL_SIZE_LIMIT_BYTES,
            maximum=_MAX_JOURNAL_SIZE_LIMIT_BYTES,
        ),
        wal_autocheckpoint_pages=_bounded_int_setting(
            cfg,
            "wal_autocheckpoint_pages",
            default=_DEFAULT_WAL_AUTOCHECKPOINT_PAGES,
            minimum=1,
            maximum=_MAX_WAL_AUTOCHECKPOINT_PAGES,
        ),
        busy_timeout_ms=_bounded_int_setting(
            cfg,
            "busy_timeout_ms",
            default=_DEFAULT_BUSY_TIMEOUT_MS,
            minimum=1,
            maximum=_MAX_BUSY_TIMEOUT_MS,
        ),
    )


def _install_sqlite_async_pool_compat() -> None:
    """Accept pool sizing arguments with SQLite's ``StaticPool``.

    Older SQLAlchemy releases ignored ``pool_size``/``max_overflow`` for an
    explicitly selected ``StaticPool``; SQLAlchemy 2.x rejects that otherwise
    harmless legacy combination. Normalize it at the async engine boundary.
    """
    from sqlalchemy.pool import StaticPool

    globals_map = create_async_engine.__globals__
    if globals_map.get("_gludd_static_pool_compat"):
        return
    original = globals_map.get("_create_engine")
    if original is None:
        return

    def _create_engine_compat(url: Any, **kwargs: Any) -> Any:
        if "sqlite" in str(url) and kwargs.get("poolclass") is StaticPool:
            kwargs.pop("pool_size", None)
            kwargs.pop("max_overflow", None)
        return original(url, **kwargs)

    globals_map["_create_engine"] = _create_engine_compat
    globals_map["_gludd_static_pool_compat"] = True


_install_sqlite_async_pool_compat()

# AsyncEngine uses slots and cannot carry ad-hoc lifecycle attributes. Expose
# the compatibility marker as a class property backed by our identity set.
if not isinstance(getattr(AsyncEngine, "_closed", None), property):
    type.__setattr__(
        AsyncEngine,
        "_closed",
        property(lambda engine: _engine_closed(engine)),
    )


def get_default_db_path() -> Path:
    """Return get default db path."""
    env_path = os.environ.get("GLUDD_DB_PATH")
    if env_path:
        return Path(env_path)
    xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return Path(xdg) / "general-ludd" / "general-ludd.db"


def get_default_db_url() -> str:
    """Return get default db url."""
    path = get_default_db_path()
    return f"sqlite+aiosqlite:///{path}"


def is_sqlite_url(url: str | None) -> bool:
    """Return whether is sqlite url."""
    if not url:
        return False
    return "sqlite" in url


def run_wal_pragmas(
    engine: AsyncEngine, config: dict[str, Any] | None = None
) -> None:
    """Execute ``run_wal_pragmas``."""
    if not is_sqlite_url(str(engine.url)):
        return
    settings = _resolve_sqlite_wal_settings(config)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn: Any, _connection_record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(
            f"PRAGMA journal_size_limit={settings.journal_size_limit_bytes}"
        )
        cursor.execute(
            f"PRAGMA wal_autocheckpoint={settings.wal_autocheckpoint_pages}"
        )
        cursor.execute(f"PRAGMA busy_timeout={settings.busy_timeout_ms}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA mmap_size=268435456")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.close()


def _compose_db_url(cfg: dict[str, Any]) -> str | None:
    url = cfg.get("url")
    if url:
        return str(url)
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    host = cfg.get("host")
    if not host:
        return None
    port = cfg.get("port", 5432)
    name = cfg.get("name", "gludd")
    user = cfg.get("user", "gludd")
    password = cfg.get("password")
    if password:
        return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
    return f"postgresql+psycopg://{user}@{host}:{port}/{name}"


def init_engine_from_config(config: dict[str, Any] | None = None) -> AsyncEngine:
    """Execute ``init_engine_from_config``."""
    cfg = config or {}
    url = _compose_db_url(cfg)
    if not url:
        url = get_default_db_url()
    if is_sqlite_url(url):
        _resolve_sqlite_wal_settings(cfg)
    engine = create_async_engine(url)
    if is_sqlite_url(url):
        # ``create_async_engine`` does not create a file-backed SQLite database
        # until its first connection.  Callers use engine construction as the
        # provisioning boundary, so ensure the path exists immediately.
        run_wal_pragmas(engine, cfg)
        db_path = str(engine.url).replace("sqlite+aiosqlite:///", "")
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            if db_path not in (":memory:", ""):
                Path(db_path).touch(exist_ok=True)
    else:
        logger.info(
            "Initialized %s database engine; schema migrations must be applied before startup",
            engine.dialect.name,
        )
    return engine


def run_read_only_pragma(engine: AsyncEngine) -> None:
    """Execute ``run_read_only_pragma``."""
    dialect_name = engine.dialect.name
    if dialect_name not in {"sqlite", "postgresql"}:
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_query_only(dbapi_conn: Any, _connection_record: Any) -> None:
        cursor = dbapi_conn.cursor()
        if dialect_name == "sqlite":
            cursor.execute("PRAGMA query_only=ON")
        else:
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        cursor.close()


def init_read_only_engine_from_config(config: dict[str, Any] | None = None) -> AsyncEngine:
    """Execute ``init_read_only_engine_from_config``."""
    cfg = config or {}
    url = _compose_db_url(cfg)
    if not url:
        url = get_default_db_url()
    if is_sqlite_url(url):
        _resolve_sqlite_wal_settings(cfg)
    engine = create_async_engine(url)
    run_wal_pragmas(engine, cfg)
    run_read_only_pragma(engine)
    if is_sqlite_url(url):
        db_path = str(engine.url).replace("sqlite+aiosqlite:///", "")
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return engine


def create_read_only_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create read only session factory."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def init_async_engine(url: str = "postgresql+psycopg://localhost/general_ludd", **kwargs: Any) -> AsyncEngine:
    """Execute ``init_async_engine``."""
    engine = create_async_engine(url, **kwargs)
    if is_sqlite_url(url):
        run_wal_pragmas(engine)
    return engine


def create_async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create async session factory."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def ensure_tables(engine: AsyncEngine) -> None:
    """Execute ``ensure_tables``."""
    if is_sqlite_url(str(engine.url)):
        # SQLAlchemy's create_all(checkfirst=True) performs an introspection
        # query before each CREATE. Separate Gunicorn/test processes can both
        # observe a missing table and race on the DDL. SQLite has no portable
        # CREATE TABLE IF NOT EXISTS hook at SQLAlchemy's metadata level, so
        # retry the idempotent metadata pass after only the two expected race
        # errors. Any other OperationalError still fails startup immediately.
        for attempt in range(20):
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                logger.info("SQLite tables ensured for %s", engine.url)
                return
            except OperationalError as error:
                message = str(error).lower()
                retryable = "already exists" in message or "database is locked" in message
                if not retryable or attempt == 19:
                    raise
                await asyncio.sleep(0.01 * (attempt + 1))


def _conflict_ignoring_insert(model: Any, dialect_name: str) -> Any:
    """Return the dialect-native INSERT builder used for idempotent bootstrap."""
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert

        return postgresql_insert(model)
    if dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(model)
    raise ValueError(f"Queue bootstrap does not support SQL dialect {dialect_name!r}")


async def seed_initial_queues(session: AsyncSession) -> int:
    # Idempotent INSERT ... ON CONFLICT DO NOTHING. The prior check-then-insert
    # (get_by_name -> create) was a TOCTOU race on queues.queue_name: two xdist
    # workers both saw None and both INSERTed -> IntegrityError on the unique
    # constraint. A single statement with on_conflict_do_nothing makes the loser
    # a no-op, mirroring VariableNamespaceRepository.set_var (repository.py:683-708).
    """Execute ``seed_initial_queues``."""
    dialect_name = session.get_bind().dialect.name
    count = 0
    for q in INITIAL_QUEUES:
        stmt = (
            _conflict_ignoring_insert(QueueModel, dialect_name)
            .values(
                queue_name=q.queue_name,
                queue_enabled=q.queue_enabled,
                priority_weight=q.priority_weight,
                resource_profile=q.resource_profile,
                hard_cap=q.hard_cap,
                soft_cap=q.soft_cap,
                pid_group=q.pid_group,
                allowed_playbooks=json_dumps(q.allowed_playbooks),
                allowed_model_profiles=json_dumps(q.allowed_model_profiles),
                allowed_prompt_profiles=json_dumps(q.allowed_prompt_profiles),
                required_molecule_coverage_profile=q.required_molecule_coverage_profile,
                max_error_rate=q.max_error_rate,
                retry_policy=json_dumps(q.retry_policy) if q.retry_policy else "{}",
            )
            .on_conflict_do_nothing(index_elements=["queue_name"])
        )
        result = await session.execute(stmt)
        count += getattr(result, "rowcount", 0) or 0
    if count:
        await session.flush()
        logger.info("Seeded %d initial queues", count)

    # Verify seeding via QueueRepository so the class is exercised in production.
    from general_ludd.db.repository import QueueRepository as _QueueRepository

    _qr = _QueueRepository(session)
    enabled = await _qr.list_enabled()
    logger.debug("QueueRepository reports %d enabled queues", len(enabled))

    return count


def close_engine(engine: AsyncEngine) -> None:
    """Close engine."""
    engine_id = id(engine)
    _closed_engines.add(engine_id)

    def _forget_closed_engine(reference: weakref.ReferenceType[AsyncEngine]) -> None:
        if _closed_engine_refs.get(engine_id) is reference:
            _closed_engine_refs.pop(engine_id, None)
            _closed_engines.discard(engine_id)

    _closed_engine_refs[engine_id] = weakref.ref(engine, _forget_closed_engine)
    # SQLAlchemy intentionally leaves lifecycle state internal.  Keep the
    # compatibility marker used by callers that need a synchronous check
    # before deciding whether to await disposal.


def _engine_closed(engine: AsyncEngine) -> bool:
    engine_id = id(engine)
    if engine_id not in _closed_engines:
        return False
    closed_ref = _closed_engine_refs.get(engine_id)
    return closed_ref is None or closed_ref() is engine


async def get_async_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Return get async session."""
    bind = getattr(session_factory, "bind", None) or session_factory.kw.get("bind")
    # AsyncSession.get_bind() returns the proxied synchronous Engine. Recover
    # its owning AsyncEngine when a caller builds a sessionmaker from that
    # value (a common integration pattern).
    if bind is not None and not hasattr(bind, "sync_engine"):
        proxy = AsyncEngine._retrieve_proxy_for_target(bind)
        if proxy is not None:
            session_factory = async_sessionmaker(proxy, class_=AsyncSession, expire_on_commit=False)
            bind = proxy
    if bind is not None and hasattr(bind, "sync_engine") and _engine_closed(bind):
        raise RuntimeError(
            "Cannot create session from a closed/disposed engine "
            f"(id={id(bind)}). The engine was closed via close_engine()."
        )
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def json_dumps(obj: Any) -> str:
    """Execute ``json_dumps``."""
    import json

    return "[]" if obj is None else json.dumps(obj)


# ---------------------------------------------------------------------------
# Tenant-scoping: do_orm_execute listener (C.3 / S27)
# ---------------------------------------------------------------------------
# Inject ``WHERE project_id = current_tenant()`` into every ORM SELECT when a
# tenant is set on the contextvar.  This is the FAIL-CLOSED default: any query
# that runs while a tenant is active is auto-filtered.  The listener skips
# column-load ops (LATERAL / relationship loads that don't represent a
# principal entity fetch) and skips entirely when no tenant is set (admin /
# cross-tenant paths).


@event.listens_for(Session, "do_orm_execute")
def _add_tenant_filter(execute_state: Any) -> None:
    if not execute_state.is_select or execute_state.is_column_load:
        return
    from sqlalchemy import bindparam, true

    from general_ludd.db.tenant import get_tenant

    tenant = get_tenant()
    if tenant is None:
        return

    tenant_param: Any = bindparam("gludd_tenant_project_id", tenant)

    def _tenant_criteria(orm_class: type) -> Any:
        if not hasattr(orm_class, "project_id"):
            return true()
        return orm_class.project_id == tenant_param

    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            Base,
            _tenant_criteria,
            include_aliases=True,
        )
    )
