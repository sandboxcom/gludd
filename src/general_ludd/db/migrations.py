"""Alembic migration support."""

from __future__ import annotations

import logging
import os
from io import StringIO
from pathlib import Path
from typing import NamedTuple

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

logger = logging.getLogger(__name__)


class MigrationPlan(NamedTuple):
    """Result of a migration dry-run / plan."""

    sql: str
    pending_count: int
    current_rev: str | None
    head_rev: str


def get_alembic_config(url: str = "") -> AlembicConfig:
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
    command.stamp(cfg, "head")


def plan_migration(cfg: AlembicConfig) -> MigrationPlan:
    """Generate the SQL that would be executed for pending migrations.

    A read-only dry-run: produces the upgrade SQL without touching the database.
    Returns a ``MigrationPlan`` with the SQL, pending count, and revision info.
    """
    script = ScriptDirectory.from_config(cfg)
    head_rev = script.get_current_head()

    from alembic.runtime.environment import EnvironmentContext
    from alembic.runtime.migration import MigrationContext

    buffer = StringIO()

    def _dry_run(rev, context):
        return script._upgrade_revs(head_rev, rev)

    with EnvironmentContext(cfg, script, fn=_dry_run, as_sql=True) as ctx:
        ctx.configure(output_buffer=buffer)
        ctx.run_migrations()

    sql = buffer.getvalue()

    current_rev: str | None = None
    pending_count = len(script.get_heads())
    try:
        conn = cfg.attributes.get("connection")
        if conn is None:
            from sqlalchemy import create_engine

            engine = create_engine(cfg.get_main_option("sqlalchemy.url"))
            with engine.connect() as connection:
                ctx = MigrationContext.configure(connection)
                current_rev = ctx.get_current_revision()
                if current_rev is not None and head_rev != current_rev:
                    revisions = script.iterate_revisions(head_rev, current_rev)
                    pending_count = len(list(revisions))
                elif current_rev is None:
                    pending_count = 1
                else:
                    pending_count = 0
        else:
            ctx = MigrationContext.configure(conn)
            current_rev = ctx.get_current_revision()
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
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine

        engine = create_engine(cfg.get_main_option("sqlalchemy.url"))
        with engine.connect() as connection:
            ctx = MigrationContext.configure(connection)
            current_rev = ctx.get_current_revision()
            if current_rev is None:
                return 1 if head_rev else 0
            if head_rev == current_rev:
                return 0
            revisions = list(script.iterate_revisions(head_rev, current_rev))
            return len(revisions)
    except Exception:
        logger.debug("Could not connect to database for pending check", exc_info=True)
        return -1
