"""Alembic migration support."""

from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config as AlembicConfig


def get_alembic_config() -> AlembicConfig:
    config_path = Path(__file__).parent.parent.parent.parent / "alembic.ini"
    cfg = AlembicConfig()
    if config_path.exists():
        cfg.config_file_name = str(config_path)
    script_location = str(Path(__file__).parent.parent.parent.parent / "alembic")
    cfg.set_main_option("script_location", script_location)
    cfg.set_main_option("sqlalchemy.url", os.environ.get("DATABASE_URL", "sqlite:///./test.db"))
    return cfg
