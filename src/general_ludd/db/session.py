"""SQLAlchemy database engine and session management.

Supports SQLite (default) with WAL mode and PostgreSQL.
SQLite is used out-of-the-box with no external database required.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base, QueueModel
from general_ludd.schemas.queue import INITIAL_QUEUES

logger = logging.getLogger(__name__)


def get_default_db_path() -> Path:
    env_path = os.environ.get("GLUDD_DB_PATH")
    if env_path:
        return Path(env_path)
    xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return Path(xdg) / "general-ludd" / "general-ludd.db"


def get_default_db_url() -> str:
    path = get_default_db_path()
    return f"sqlite+aiosqlite:///{path}"


def is_sqlite_url(url: str | None) -> bool:
    if not url:
        return False
    return "sqlite" in url


def run_wal_pragmas(engine: AsyncEngine) -> None:
    if not is_sqlite_url(str(engine.url)):
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn: Any, _connection_record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA mmap_size=268435456")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.close()


def _compose_db_url(cfg: dict[str, Any]) -> str | None:
    url = cfg.get("url")
    if url:
        return url
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
    cfg = config or {}
    url = _compose_db_url(cfg)
    if not url:
        url = get_default_db_url()
    engine = create_async_engine(url)
    if is_sqlite_url(str(engine.url)):
        run_wal_pragmas(engine)
        db_path = str(engine.url).replace("sqlite+aiosqlite:///", "")
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return engine


def init_async_engine(url: str = "postgresql+psycopg://localhost/general_ludd", **kwargs: Any) -> AsyncEngine:
    engine = create_async_engine(url, **kwargs)
    if is_sqlite_url(url):
        run_wal_pragmas(engine)
    return engine


def create_async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ensure_tables(engine: AsyncEngine) -> None:
    if is_sqlite_url(str(engine.url)):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("SQLite tables ensured for %s", engine.url)


async def seed_initial_queues(session: AsyncSession) -> int:
    count = 0
    for q in INITIAL_QUEUES:
        existing = await session.execute(
            text("SELECT queue_name FROM queues WHERE queue_name=:name"),
            {"name": q.queue_name},
        )
        if existing.scalar() is None:
            model = QueueModel(
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
            session.add(model)
            count += 1
    if count:
        await session.flush()
        logger.info("Seeded %d initial queues", count)
    return count


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj) if obj else "[]"
