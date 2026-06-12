"""W3.5 (M8/H18): single-worker clamp + SQLite-only enforcement.

Multi-worker gunicorn spawns N event loops + N in-memory stores against one
SQLite file — there is no cross-process claim coordination, so N>1 is dishonest.
Until/unless Postgres is actually wired (it is not: create_all and stamp_head are
SQLite-only, alembic.ini hardcodes sqlite), the honest behavior is:

- default workers = 1
- refuse N>1 when the DB is SQLite, with a clear error
- refuse a non-SQLite DB URL with a clear "SQLite only" error
"""

from __future__ import annotations

import pytest

from general_ludd.db.session import init_engine_from_config


class TestSqliteOnlyEnforcement:
    def test_sqlite_url_is_accepted(self):
        engine = init_engine_from_config({"url": "sqlite+aiosqlite://"})
        assert "sqlite" in str(engine.url)

    def test_default_is_sqlite(self):
        engine = init_engine_from_config({})
        assert "sqlite" in str(engine.url)

    def test_postgres_url_is_refused(self):
        with pytest.raises(ValueError, match=r"SQLite only|sqlite only|not supported"):
            init_engine_from_config({"url": "postgresql+psycopg://localhost/gludd"})

    def test_postgres_host_config_is_refused(self):
        with pytest.raises(ValueError, match=r"SQLite only|sqlite only|not supported"):
            init_engine_from_config({"host": "db.example.com", "name": "gludd"})


class TestSingleWorkerClamp:
    def test_clamp_workers_defaults_to_one(self):
        from general_ludd.cli import _clamp_workers_for_sqlite
        assert _clamp_workers_for_sqlite(None) == 1

    def test_clamp_workers_one_is_ok(self):
        from general_ludd.cli import _clamp_workers_for_sqlite
        assert _clamp_workers_for_sqlite(1) == 1

    def test_clamp_workers_refuses_more_than_one_with_sqlite(self):
        from general_ludd.cli import _clamp_workers_for_sqlite
        # N>1 with SQLite is clamped to 1 (honest single-writer behavior).
        assert _clamp_workers_for_sqlite(4) == 1
