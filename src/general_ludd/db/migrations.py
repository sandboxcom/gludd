"""Alembic migration support."""

from __future__ import annotations

import logging
import os
from io import StringIO
from pathlib import Path
from typing import Any, NamedTuple

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.environment import EnvironmentContext
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)


class MigrationPlan(NamedTuple):
    """Result of a migration dry-run / plan."""

    sql: str
    pending_count: int
    current_rev: str | None
    head_rev: str


def get_alembic_config(url: str = "") -> AlembicConfig:
    """Build a synchronous Alembic configuration for the application database.

    Args:
        url: Optional SQLAlchemy URL. When omitted, ``DATABASE_URL`` or the
            local SQLite development database is used.

    Returns:
        A configuration with the repository migration path and any async
        SQLite driver normalized to its synchronous equivalent.
    """
    config_path = Path(__file__).parent.parent.parent.parent / "alembic.ini"
    cfg = AlembicConfig()
    if config_path.exists():
        cfg.config_file_name = str(config_path)
    script_location = str(Path(__file__).parent.parent.parent.parent / "alembic")
    cfg.set_main_option("script_location", script_location)
    resolved_url = url or os.environ.get("DATABASE_URL", "sqlite:///./test.db")
    # Alembic executes synchronous migrations; translate SQLAlchemy's async
    # SQLite driver URL used by the application to its sync equivalent.
    resolved_url = resolved_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    cfg.set_main_option("sqlalchemy.url", resolved_url)
    return cfg


def stamp_head(cfg: AlembicConfig) -> None:
    """Stamp the configured database without leaking an async driver to Alembic.

    The bundled Alembic environment uses SQLAlchemy's synchronous
    ``engine_from_config``.  Application engines use ``aiosqlite``, so reuse the
    same SQLite path through its synchronous driver for the duration of the
    migration command and restore the caller's configuration afterwards.
    """
    configured_url = cfg.get_main_option("sqlalchemy.url")
    if configured_url and configured_url.startswith("sqlite+aiosqlite:"):
        sync_url = configured_url.replace("sqlite+aiosqlite:", "sqlite:", 1)
        cfg.set_main_option("sqlalchemy.url", sync_url)
        try:
            command.stamp(cfg, "head")
        finally:
            cfg.set_main_option("sqlalchemy.url", configured_url)
        return
    command.stamp(cfg, "head")


def plan_migration(cfg: AlembicConfig) -> MigrationPlan:
    """Generate the SQL that would be executed for pending migrations.

    A read-only dry-run that produces upgrade SQL without touching the database.
    SQLite batch migrations instead return an explicit diagnostic because Alembic
    requires a live connection to reflect the table before rendering that SQL.
    The result also includes the pending count and revision information.
    """
    script = ScriptDirectory.from_config(cfg)
    head_rev_raw = script.get_current_head()
    if head_rev_raw is None:
        raise RuntimeError("No head revision found; run alembic upgrade head first")
    head_rev: str = head_rev_raw

    buffer = StringIO()
    db_url = cfg.get_main_option("sqlalchemy.url") or ""

    def _dry_run(rev: str, context: MigrationContext) -> list[tuple[str, str]] | Any:
        return script._upgrade_revs(head_rev, rev)

    try:
        with EnvironmentContext(cfg, script, fn=_dry_run, as_sql=True) as env_ctx:
            env_ctx.configure(
                url=db_url,
                output_buffer=buffer,
                literal_binds=True,
                dialect_opts={"paramstyle": "named"},
            )
            env_ctx.run_migrations()
    except CommandError as exc:
        message = str(exc)
        sqlite_batch_offline = db_url.startswith("sqlite:") and (
            "batch mode with dialect sqlite requires a live database connection" in message
        )
        if not sqlite_batch_offline:
            raise
        # Alembic cannot render SQLite move-and-copy migrations offline unless
        # every historical batch operation supplies a full copy_from table.
        # Keep this planner read-only and explicit instead of silently executing
        # DDL or rendering SQL for the wrong dialect.
        buffer.write(
            "-- SQLite offline SQL unavailable: batch migrations require live "
            "table reflection; inspect pending_count/head_rev and validate on a "
            "disposable database.\n"
        )

    sql = buffer.getvalue()

    current_rev: str | None = None
    pending_count = len(script.get_heads())
    try:
        conn = cfg.attributes.get("connection")
        if conn is None:
            engine = create_engine(db_url)
            try:
                with engine.connect() as connection:
                    mig_ctx = MigrationContext.configure(connection)
                    current_rev = mig_ctx.get_current_revision()
                    if current_rev is not None and head_rev != current_rev:
                        revisions = script.iterate_revisions(head_rev, current_rev)
                        pending_count = len(list(revisions))
                    elif current_rev is None:
                        pending_count = 1
                    else:
                        pending_count = 0
            finally:
                engine.dispose()
        else:
            mig_ctx = MigrationContext.configure(conn)
            current_rev = mig_ctx.get_current_revision()
            if current_rev is not None and head_rev != current_rev:
                revisions = script.iterate_revisions(head_rev, current_rev)
                pending_count = len(list(revisions))
            elif current_rev is None:
                pending_count = 1
            else:
                pending_count = 0
    except Exception:
        logger.debug("Could not connect to database for current revision", exc_info=True)
        current_rev = None
        pending_count = 0 if not sql else len(script.get_heads())

    return MigrationPlan(
        sql=sql,
        pending_count=pending_count,
        current_rev=current_rev,
        head_rev=head_rev,
    )


def check_pending(cfg: AlembicConfig) -> int:
    """Return the number of pending migrations that would be applied.

    A read-only check: reads the current database revision and counts how many
    migrations are between it and ``head``.  Returns 0 when the database is at
    head (no pending migrations).

    Does NOT modify the database.
    """
    script = ScriptDirectory.from_config(cfg)
    head_rev = script.get_current_head()

    try:
        db_url = cfg.get_main_option("sqlalchemy.url") or ""
        engine = create_engine(db_url)
        try:
            with engine.connect() as connection:
                mig_ctx = MigrationContext.configure(connection)
                current_rev = mig_ctx.get_current_revision()
                if current_rev is None:
                    return 1 if head_rev else 0
                if head_rev == current_rev:
                    return 0
                revisions = list(script.iterate_revisions(head_rev, current_rev))
                return len(revisions)
        finally:
            engine.dispose()
    except Exception:
        logger.debug("Could not connect to database for pending check", exc_info=True)
        return -1
