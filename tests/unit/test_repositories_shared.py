"""Tests for dialect-specific repository statement construction."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import Insert as PostgreSQLInsert
from sqlalchemy.dialects.sqlite import Insert as SQLiteInsert
from sqlalchemy.engine.interfaces import Dialect

from general_ludd.db.models import FeatureModel
from general_ludd.db.repositories.shared import dialect_insert


@pytest.mark.parametrize(
    ("dialect_name", "database_url", "insert_type", "parameter_marker"),
    [
        ("sqlite", "sqlite://", SQLiteInsert, "?"),
        ("postgresql", "postgresql+psycopg://", PostgreSQLInsert, "%("),
    ],
)
def test_dialect_insert_builds_native_upsert(
    dialect_name: str,
    database_url: str,
    insert_type: type[SQLiteInsert] | type[PostgreSQLInsert],
    parameter_marker: str,
) -> None:
    """Each supported backend receives its native ON CONFLICT builder."""
    engine = create_engine(database_url)
    try:
        dialect: Dialect = engine.dialect
        insert = dialect_insert(FeatureModel, dialect_name).values(
            name="dialect-upsert",
            description="initial",
        )
        statement = insert.on_conflict_do_update(
            index_elements=["name"],
            set_={"description": "updated"},
        )

        compiled = str(statement.compile(dialect=dialect))

        assert isinstance(insert, insert_type)
        assert "ON CONFLICT (name) DO UPDATE SET description" in compiled
        assert parameter_marker in compiled
    finally:
        engine.dispose()


def test_dialect_insert_rejects_unknown_backend() -> None:
    """No repository silently emits SQLite SQL for an unknown backend."""
    with pytest.raises(ValueError, match="Repository upserts do not support SQL dialect 'mysql'"):
        dialect_insert(FeatureModel, "mysql")
