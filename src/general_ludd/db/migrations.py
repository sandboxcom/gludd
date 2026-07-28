"""Alembic migration support."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig


def get_alembic_config(url: str = "") -> AlembicConfig:
    config_path = Path(__file__).parent.parent.parent.parent / "alembic.ini"
    cfg = AlembicConfig()
    if config_path.exists():
        cfg.config_file_name = str(config_path)
    script_location = str(Path(__file__).parent.parent.parent.parent / "alembic")
    cfg.set_main_option("script_location", script_location)
    resolved_url = url or os.environ.get("DATABASE_URL", "sqlite:///./test.db")
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
