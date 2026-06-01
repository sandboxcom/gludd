"""Tests for Alembic migration support."""

from __future__ import annotations

import os
from unittest.mock import patch

from general_ludd.db.migrations import get_alembic_config


class TestGetAlembicConfig:
    def test_returns_alembic_config(self):
        cfg = get_alembic_config()
        assert cfg is not None

    def test_script_location_is_set(self):
        cfg = get_alembic_config()
        assert cfg.get_main_option("script_location") is not None
        assert "alembic" in cfg.get_main_option("script_location")

    def test_default_database_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            cfg = get_alembic_config()
            url = cfg.get_main_option("sqlalchemy.url")
            assert url is not None
            assert "sqlite" in url

    def test_custom_database_url_from_env(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@localhost/mydb"}):
            cfg = get_alembic_config()
            url = cfg.get_main_option("sqlalchemy.url")
            assert url == "postgresql://user:pass@localhost/mydb"

    def test_config_file_name_set_when_exists(self):
        cfg = get_alembic_config()
        script = cfg.get_main_option("script_location")
        assert script.endswith("alembic") or "/alembic" in script
