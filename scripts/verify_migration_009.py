"""Focused parity proof for migration 009 (benchmark_results.project_id).

The repo-wide ``make alembic-check`` runs ``alembic upgrade head`` on SQLite,
which fails inside migration ``002`` because that hand-written migration uses a
bare ``op.create_foreign_key`` (an ALTER that SQLite does not support outside
``batch_alter_table``). That is a PRE-EXISTING condition unrelated to migration
009. This script proves 009's ORM/migration parity in isolation:

  1. Build the at-008 baseline: every ORM table from ``create_all`` EXCEPT
     ``benchmark_results``, plus a hand-built ``benchmark_results`` that is the
     ORM table WITHOUT the new ``project_id`` column / index / FK (i.e. the
     schema as it was before 009). This avoids replaying the broken 002 ALTERs.
  2. Run ONLY migration 009's ``upgrade()`` against that baseline (add_column +
     create_index + a batch FK — all SQLite-safe via batch mode).
  3. Reflect the resulting ``benchmark_results`` table and assert its columns,
     the new (project_id, task_type) index, and the project_id FK ondelete=SET
     NULL match the ``BenchmarkResultModel`` ORM definition exactly.
  4. Round-trip the migration's ``downgrade()`` and assert project_id is gone.

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

from general_ludd.db.models import Base, BenchmarkResultModel  # noqa: E402

TABLE = "benchmark_results"
NEW_COLUMN = "project_id"
NEW_INDEX_COLS = ("project_id", "task_type")


def _load_migration():
    path = ROOT / "alembic" / "versions" / "009_add_benchmark_project_id.py"
    spec = importlib.util.spec_from_file_location("migration_009", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(conn: Connection, fn) -> None:
    """Run a migration upgrade/downgrade fn with the alembic op proxy bound."""
    ctx = MigrationContext.configure(
        conn, opts={"render_as_batch": True}
    )
    with Operations.context(ctx):
        fn()


def _build_baseline(conn: Connection) -> None:
    """Create the at-008 schema: all ORM tables, but benchmark_results WITHOUT
    the project_id column / its index / its FK (the schema before 009).

    The benchmark_results table is hand-built from explicit DDL (mirroring
    migration 004 + the column-level index=True flags from the ORM) rather than
    reflecting the ORM table, so it never carries the new project_id column or
    the new (project_id, task_type) index — that is precisely what migration 009
    must add. Other tables come from create_all (they are project_id-independent
    here, and projects/prompt_profiles must exist for the FK targets)."""
    other_tables = [t for t in Base.metadata.sorted_tables if t.name != TABLE]
    Base.metadata.create_all(conn, tables=other_tables)

    conn.exec_driver_sql(
        """
        CREATE TABLE benchmark_results (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            prompt_profile_id VARCHAR(64),
            model_profile_id VARCHAR(64) NOT NULL,
            task_type VARCHAR(64) NOT NULL,
            task_description TEXT NOT NULL DEFAULT '',
            completion_score FLOAT NOT NULL DEFAULT 0.0,
            code_quality_score FLOAT NOT NULL DEFAULT 0.0,
            instruction_adherence_score FLOAT NOT NULL DEFAULT 0.0,
            token_efficiency_score FLOAT NOT NULL DEFAULT 0.0,
            time_seconds FLOAT NOT NULL DEFAULT 0.0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd FLOAT NOT NULL DEFAULT 0.0,
            success BOOLEAN NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            raw_output TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(prompt_profile_id) REFERENCES prompt_profiles (id) ON DELETE SET NULL
        )
        """
    )
    # Pre-009 indexes (column-level index=True + the two composites). Crucially,
    # NO project_id column or (project_id, task_type) index exists yet.
    for ddl in (
        "CREATE INDEX ix_benchmark_results_prompt_profile_id ON benchmark_results (prompt_profile_id)",
        "CREATE INDEX ix_benchmark_results_model_profile_id ON benchmark_results (model_profile_id)",
        "CREATE INDEX ix_benchmark_results_task_type ON benchmark_results (task_type)",
        "CREATE INDEX ix_benchmark_task_model ON benchmark_results (task_type, model_profile_id)",
        "CREATE INDEX ix_benchmark_task_prompt ON benchmark_results (task_type, prompt_profile_id)",
    ):
        conn.exec_driver_sql(ddl)


def main() -> int:
    migration = _load_migration()
    assert migration.revision == "009", migration.revision
    assert migration.down_revision == "008", migration.down_revision

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")

        # 1. Baseline at-008.
        _build_baseline(conn)

        insp = inspect(conn)
        pre_cols = {c["name"] for c in insp.get_columns(TABLE)}
        if NEW_COLUMN in pre_cols:
            print("FAIL: baseline already has project_id — bad baseline build")
            return 1

        # 2. Apply ONLY migration 009's upgrade.
        _run(conn, migration.upgrade)

        # 3. Reflect and compare against the ORM model.
        insp = inspect(conn)
        mig_cols = {c["name"] for c in insp.get_columns(TABLE)}
        orm_cols = {c.name for c in BenchmarkResultModel.__table__.columns}
        if mig_cols != orm_cols:
            print(
                f"FAIL: column mismatch\n  migration: {sorted(mig_cols)}\n"
                f"  orm:       {sorted(orm_cols)}"
            )
            return 1
        if NEW_COLUMN not in mig_cols:
            print("FAIL: migration 009 did not add project_id")
            return 1

        # New (project_id, task_type) index present.
        mig_index_cols = {
            tuple(ix["column_names"]) for ix in insp.get_indexes(TABLE)
        }
        if NEW_INDEX_COLS not in mig_index_cols:
            print(
                f"FAIL: missing (project_id, task_type) index. got {mig_index_cols}"
            )
            return 1
        # Every ORM-declared index must still exist (batch rebuild preserves them).
        orm_index_cols = {
            tuple(c.name for c in ix.columns)
            for ix in BenchmarkResultModel.__table__.indexes
        }
        for col in BenchmarkResultModel.__table__.columns:
            if col.index:
                orm_index_cols.add((col.name,))
        missing = orm_index_cols - mig_index_cols
        if missing:
            print(
                f"FAIL: indexes present in ORM but missing from migration: {missing}"
            )
            print(f"  migration indexes: {mig_index_cols}")
            return 1

        # project_id FK with ondelete=SET NULL.
        fks = {
            tuple(fk["constrained_columns"]): fk.get("options", {}).get("ondelete")
            for fk in insp.get_foreign_keys(TABLE)
        }
        if fks.get(("project_id",)) != "SET NULL":
            print(
                f"FAIL: project_id FK ondelete should be SET NULL, "
                f"got {fks.get(('project_id',))}"
            )
            return 1

        # 4. Downgrade round-trip.
        _run(conn, migration.downgrade)
        insp = inspect(conn)
        post_cols = {c["name"] for c in insp.get_columns(TABLE)}
        if NEW_COLUMN in post_cols:
            print("FAIL: downgrade did not drop project_id")
            return 1

    print("OK: migration 009 <-> BenchmarkResultModel parity verified")
    print(f"  columns: {sorted(orm_cols)}")
    print("  FK: project_id=SET NULL; (project_id, task_type) index present")
    print("  downgrade drops project_id, its index, and the FK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
