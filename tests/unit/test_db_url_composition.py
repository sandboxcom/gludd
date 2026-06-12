from __future__ import annotations

import os
from unittest.mock import patch

from general_ludd.db.session import _compose_db_url, init_engine_from_config

# W3.5 (M8/H18): general_ludd is SQLite only. These tests verify the URL
# COMPOSITION logic (`_compose_db_url`) directly — the composer still builds a
# Postgres URL from host/port/etc, but `init_engine_from_config` then refuses any
# non-SQLite URL (see test_single_worker_sqlite.py). So composition is tested at
# the composer; engine construction is tested with SQLite.


class TestDatabaseUrlComposition:
    def test_url_field_takes_priority(self):
        cfg = {"url": "postgresql+psycopg://u:p@db.example.com:5432/mydb", "host": "ignored"}
        assert _compose_db_url(cfg) == "postgresql+psycopg://u:p@db.example.com:5432/mydb"

    def test_compose_from_host_port_name_user(self):
        cfg = {
            "host": "db.example.com", "port": 5433,
            "name": "mydb", "user": "admin", "password": "secret",
        }
        url_str = _compose_db_url(cfg) or ""
        assert "db.example.com" in url_str
        assert "5433" in url_str
        assert "mydb" in url_str

    def test_compose_without_password(self):
        cfg = {"host": "db.example.com", "port": 5432, "name": "gludd", "user": "gludd"}
        assert "db.example.com" in (_compose_db_url(cfg) or "")

    def test_compose_with_defaults(self):
        cfg = {"host": "localhost"}
        assert "localhost" in (_compose_db_url(cfg) or "")

    def test_empty_config_falls_back_to_sqlite(self):
        engine = init_engine_from_config({})
        assert "sqlite" in str(engine.url)

    def test_none_config_falls_back_to_sqlite(self):
        engine = init_engine_from_config(None)
        assert "sqlite" in str(engine.url)

    def test_database_url_env_var_override(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://env:pass@envhost:5432/envdb"}):
            assert "envhost" in (_compose_db_url({}) or "")

    def test_database_url_env_var_beats_config_fields(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://env:pass@envhost:5432/envdb"}):
            cfg = {"host": "ignored", "port": 9999}
            url_str = _compose_db_url(cfg) or ""
            assert "envhost" in url_str
            assert "9999" not in url_str
