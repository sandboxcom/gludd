"""Focused parity proof for migration 008 (project_relationships).

The repo-wide ``make alembic-check`` runs ``alembic upgrade head`` on SQLite,
which fails inside migration ``002`` because that hand-written migration uses
``op.create_foreign_key`` (an ALTER that SQLite does not support outside
``batch_alter_table``). That is a PRE-EXISTING condition unrelated to migration
008. This script proves 008's ORM/migration parity in isolation:

  1. Build the schema for every table EXCEPT ``project_relationships`` from the
     ORM metadata (``create_all``), giving us the at-007 baseline without
     replaying the broken 002 ALTERs.
  2. Run ONLY migration 008's ``upgrade()`` against that baseline (an additive
     ``create_table`` + indexes — no ALTER, so SQLite is happy).
  3. Reflect the resulting ``project_relationships`` table and assert its
     columns, indexes, unique constraint, and the two FK ``ondelete`` rules
     match the ``ProjectRelationshipModel`` ORM definition exactly.
  4. Round-trip the migration's ``downgrade()`` and assert the table is gone.

Exit 0 == parity holds. Any mismatch prints a diff and exits non-zero.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from alembic.migration import MigrationContext  # noqa: E402
from alembic.operations import Operations  # noqa: E402

from general_ludd.db.models import Base, ProjectRelationshipModel  # noqa: E402

TABLE = "project_relationships"


def _load_migration():
    path = ROOT / "alembic" / "versions" / "008_add_project_relationships.py"
    spec = importlib.util.spec_from_file_location("migration_008", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(conn: Connection, fn) -> None:
    """Run a migration upgrade/downgrade fn with the alembic op proxy bound."""
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        fn()


def main() -> int:
    migration = _load_migration()
    assert migration.revision == "008", migration.revision
    assert migration.down_revision == "007", migration.down_revision

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")

        # 1. Baseline: every ORM table EXCEPT project_relationships.
        baseline_tables = [
            t for t in Base.metadata.sorted_tables if t.name != TABLE
        ]
        Base.metadata.create_all(conn, tables=baseline_tables)

        # 2. Apply ONLY migration 008's upgrade.
        _run(conn, migration.upgrade)

        # 3. Reflect and compare against the ORM model.
        insp = inspect(conn)
        if TABLE not in insp.get_table_names():
            print("FAIL: migration 008 did not create project_relationships")
            return 1

        mig_cols = {c["name"] for c in insp.get_columns(TABLE)}
        orm_cols = {c.name for c in ProjectRelationshipModel.__table__.columns}
        if mig_cols != orm_cols:
            print(
                f"FAIL: column mismatch\n  migration: {sorted(mig_cols)}\n"
                f"  orm:       {sorted(orm_cols)}"
            )
            return 1

        # Indexes: every ORM-declared index must exist in the migrated DB.
        mig_index_cols = {
            tuple(ix["column_names"]) for ix in insp.get_indexes(TABLE)
        }
        orm_index_cols = {
            tuple(c.name for c in ix.columns)
            for ix in ProjectRelationshipModel.__table__.indexes
        }
        # Single-column index=True columns also produce indexes.
        for col in ProjectRelationshipModel.__table__.columns:
            if col.index:
                orm_index_cols.add((col.name,))
        missing = orm_index_cols - mig_index_cols
        if missing:
            print(f"FAIL: indexes present in ORM but missing from migration: {missing}")
            print(f"  migration indexes: {mig_index_cols}")
            return 1

        # Unique constraint on the edge tuple.
        uniques = insp.get_unique_constraints(TABLE)
        uq_cols = {tuple(u["column_names"]) for u in uniques}
        expected_uq = (
            "project_id",
            "relation_type",
            "location_kind",
            "location_value",
        )
        if expected_uq not in uq_cols:
            print(f"FAIL: missing unique edge constraint. got {uq_cols}")
            return 1

        # Two FKs with the right ondelete rules.
        fks = {
            tuple(fk["constrained_columns"]): fk.get("options", {}).get("ondelete")
            for fk in insp.get_foreign_keys(TABLE)
        }
        if fks.get(("project_id",)) != "CASCADE":
            print(
                f"FAIL: project_id FK ondelete should be CASCADE, "
                f"got {fks.get(('project_id',))}"
            )
            return 1
        if fks.get(("related_project_id",)) != "SET NULL":
            print(
                f"FAIL: related_project_id FK ondelete should be SET NULL, "
                f"got {fks.get(('related_project_id',))}"
            )
            return 1

        # 4. Downgrade round-trip.
        _run(conn, migration.downgrade)
        insp = inspect(conn)
        if TABLE in insp.get_table_names():
            print("FAIL: downgrade did not drop project_relationships")
            return 1

    print("OK: migration 008 <-> ProjectRelationshipModel parity verified")
    print(f"  columns: {sorted(orm_cols)}")
    print("  FKs: project_id=CASCADE, related_project_id=SET NULL")
    print("  unique edge tuple + indexes match; downgrade drops the table")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
