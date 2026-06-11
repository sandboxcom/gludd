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
    command.stamp(cfg, "head")
