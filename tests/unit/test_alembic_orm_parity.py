"""Security: Ensure every ORM model has a corresponding Alembic migration.

Running ``alembic upgrade head`` on a fresh database must produce a schema
that contains every table declared in ``general_ludd.db.models``.  A table
that exists only via ``Base.metadata.create_all()`` (and not in any migration)
is an alembic drift — production deploys that only run migrations will lose
that table on the next schema change, which is a data-loss security finding.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Engine

from general_ludd.db.models import Base


def _collect_orm_tablenames() -> set[str]:
    """Return the set of ``__tablename__`` values from every ORM model."""
    return set(Base.metadata.tables.keys())


def _run_migrations(db_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run ``alembic upgrade head`` against an SQLite DB at *db_path*."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    # Build config from scratch so the URL points at the test DB file,
    # not the hardcoded alembic.ini target.
    root = pathlib.Path(__file__).resolve().parents[2]
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    # DATABASE_URL env-var override (D-37): must NOT interfere with the
    # explicit test URL, so remove it for the duration of the migration run.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    command.upgrade(cfg, "head")


@pytest.fixture
def migrated_db_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Create a fresh SQLite DB, run all migrations, return its path."""
    db_path = str(tmp_path / "test_parity.db")
    _run_migrations(db_path, monkeypatch)
    return db_path


@pytest.fixture
def migrated_engine(migrated_db_path: str) -> Iterator[Engine]:
    """Provide a migrated engine and close its SQLite pool after each test."""
    engine = create_engine(f"sqlite:///{migrated_db_path}")
    try:
        yield engine
    finally:
        engine.dispose()


class TestAlembicOrmParity:
    """Every ORM ``__tablename__`` must appear after ``alembic upgrade head``."""

    def test_all_orm_tables_exist_in_migrations(self, migrated_engine: Engine) -> None:
        inspector = sa_inspect(migrated_engine)
        migrated_tables = set(inspector.get_table_names())

        # alembic_version is a bookkeeping table, not an ORM model.
        migrated_tables.discard("alembic_version")

        orm_tablenames = _collect_orm_tablenames()

        missing_from_migrations = orm_tablenames - migrated_tables
        extra_in_migrations = migrated_tables - orm_tablenames

        assert missing_from_migrations == set(), (
            f"ORM models with NO migration: {sorted(missing_from_migrations)}. "
            f"These tables are only creatable via Base.metadata.create_all() — "
            f"an alembic drift that will cause data loss on production deploys."
        )
        assert extra_in_migrations == set(), (
            f"Tables in migrations but NOT in ORM: {sorted(extra_in_migrations)}. "
            f"[Unexpected — if intentional, add the model; otherwise remove from migration.]"
        )

    def test_migration_count_matches_orm_count(self, migrated_engine: Engine) -> None:
        inspector = sa_inspect(migrated_engine)
        migrated_tables = set(inspector.get_table_names())
        migrated_tables.discard("alembic_version")

        orm_tablenames = _collect_orm_tablenames()

        assert len(migrated_tables) == len(orm_tablenames), (
            f"Table count mismatch: {len(migrated_tables)} in migrations vs "
            f"{len(orm_tablenames)} in ORM. "
            f"Migrated: {sorted(migrated_tables)}. ORM: {sorted(orm_tablenames)}."
        )

    def test_no_missing_columns_per_model(self, migrated_engine: Engine) -> None:
        """Each ORM model's columns must exist in the migrated table."""
        inspector = sa_inspect(migrated_engine)

        for tablename, table in Base.metadata.tables.items():
            if tablename not in inspector.get_table_names():
                continue  # handled by test_all_orm_tables_exist_in_migrations

            migrated_columns = {col["name"] for col in inspector.get_columns(tablename)}
            orm_columns = {col.name for col in table.columns}

            missing_cols = orm_columns - migrated_columns
            assert missing_cols == set(), (
                f"Table '{tablename}' missing columns in migration: "
                f"{sorted(missing_cols)}."
            )

    def test_migrations_are_idempotent(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running ``alembic upgrade head`` twice must not error."""
        db_path = str(tmp_path / "test_idempotent.db")
        _run_migrations(db_path, monkeypatch)
        # Second run must succeed silently (no new tables to create).
        _run_migrations(db_path, monkeypatch)

        # Verify the connection is still valid after double upgrade.
        # On SQLite, a broken migration would leave the file inconsistent
        # and the next open would fail.
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    __import__("sqlalchemy").text(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                )
                tables = {row[0] for row in result}
        finally:
            engine.dispose()
        orm_tablenames = _collect_orm_tablenames()
        missing = orm_tablenames - tables
        assert missing == set(), (
            f"After idempotent upgrade: missing tables {sorted(missing)}"
        )
