from __future__ import annotations

import os
from unittest.mock import patch

from general_ludd.db.session import init_engine_from_config


class TestDatabaseUrlComposition:
    def test_url_field_takes_priority(self):
        cfg = {"url": "postgresql+psycopg://u:p@db.example.com:5432/mydb", "host": "ignored"}
        engine = init_engine_from_config(cfg)
        assert "db.example.com" in str(engine.url)

    def test_compose_from_host_port_name_user(self):
        cfg = {"host": "db.example.com", "port": 5433, "name": "mydb", "user": "admin", "password": "secret"}
        engine = init_engine_from_config(cfg)
        url_str = str(engine.url)
        assert "db.example.com" in url_str
        assert "5433" in url_str
        assert "mydb" in url_str

    def test_compose_without_password(self):
        cfg = {"host": "db.example.com", "port": 5432, "name": "gludd", "user": "gludd"}
        engine = init_engine_from_config(cfg)
        assert "db.example.com" in str(engine.url)

    def test_compose_with_defaults(self):
        cfg = {"host": "localhost"}
        engine = init_engine_from_config(cfg)
        assert "localhost" in str(engine.url)

    def test_empty_config_falls_back_to_sqlite(self):
        engine = init_engine_from_config({})
        assert "sqlite" in str(engine.url)

    def test_none_config_falls_back_to_sqlite(self):
        engine = init_engine_from_config(None)
        assert "sqlite" in str(engine.url)

    def test_database_url_env_var_override(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://env:pass@envhost:5432/envdb"}):
            engine = init_engine_from_config({})
            assert "envhost" in str(engine.url)

    def test_database_url_env_var_beats_config_fields(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://env:pass@envhost:5432/envdb"}):
            cfg = {"host": "ignored", "port": 9999}
            engine = init_engine_from_config(cfg)
            assert "envhost" in str(engine.url)
            assert "9999" not in str(engine.url)
