"""WP-D3: structural comparison of ``create_all`` vs ``alembic upgrade head``.

Background (STABILIZATION_PLAN.md WP-D3)
----------------------------------------
Migration ``001_initial_schema`` was documented as drifting from the ORM
models in two ways: missing 8 tables and missing ``project_id`` FK columns.
Subsequent migrations (002-023) added some of the missing tables, but no
comprehensive structural comparison test existed — only a name-level check
(``tests/unit/test_alembic_orm_parity.py``). This module is the missing
structural test.

Method
------
Two SQLite databases are built from EMPTY state:

* DB-A: ``Base.metadata.create_all(engine)`` — exactly what the ORM declares.
* DB-B: ``alembic upgrade head`` from empty — exactly what production
  migrations produce.

Both are then introspected via ``PRAGMA table_info``, ``PRAGMA index_list``,
``PRAGMA index_info``, ``PRAGMA foreign_key_list``, and ``sqlite_master``.
The four tests below assert the two schemas are identical at the table,
column, index, and foreign-key levels.

When any of these tests fail, the failure message IS the migration drift
delta. Fixing the migrations to match the ORM is a separate task — this test
only REVEALS the drift.

Known drift as of this commit (revealed by running this test)
-------------------------------------------------------------
The tables and columns tests pass. The INDEXES and FOREIGN KEYS tests fail:

INDEX drift (5 tables):

* ``benchmark_results`` — ORM declares 4 single-column indexes
  (``model_profile_id``, ``prompt_profile_id``, ``task_role``, ``task_type``)
  that are missing from migrations entirely.
* ``memory_records`` — name-prefix mismatch: ORM uses
  ``ix_memory_records_*``; migration 022 uses ``ix_memory_*``. Also the
  ``ix_memory_records_namespace`` index is present in ORM but absent in
  migration.
* ``model_performance`` — ``ix_model_performance_model_profile_id`` is
  UNIQUE in ORM (driven by the ``unique=True`` on the column) but
  NON-unique in migration 015.
* ``ornith_training_pairs`` — name-prefix mismatch: ORM uses
  ``ix_ornith_training_pairs_*``; migration 014a uses
  ``ix_ornith_pairs_*``.
* ``task_decisions`` — migration 001 declares ``ix_task_decisions_return_id``
  as a plain index, but the ORM models ``return_id`` with
  ``unique=True`` (yielding an implicit autoindex). Plain index vs.
  UNIQUE constraint is a structural difference.

FOREIGN KEY drift (10 tables):

* ``audit_events``, ``benchmark_results``, ``bucket_leases``, ``queues``,
  ``task_decisions``, ``task_returns``, ``todos``, ``variable_namespaces``
  — every project-scoped FK declared with ``ondelete="SET NULL"`` in the
  ORM is created by the migration WITHOUT the ``ondelete`` clause, so
  SQLite records ``NO ACTION`` instead of ``SET NULL``. Deleting a
  ``projects`` row in a migration-only DB raises an integrity error
  rather than nulling the FKs.
* ``todo_events`` — same project_id ``SET NULL`` loss, plus the
  ``todo_id`` FK is declared with ``ondelete="CASCADE"`` in the ORM
  but ``NO ACTION`` in the migration.
* ``variable_values`` — the ``namespace_id`` FK is declared
  ``ondelete="CASCADE"`` in the ORM but ``NO ACTION`` in the migration.

The ``project_id`` ``SET NULL`` drift matches STABILIZATION_PLAN.md WP-D3's
note: migration 001 (and its successors) did not reproduce the
``ondelete`` actions the ORM declares. The index drift is additional,
previously-undocumented drift surfaced by this structural comparison.
"""

from __future__ import annotations

import os
import pathlib

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from general_ludd.db.models import Base


def _run_migrations(db_path: str) -> None:
    """Run ``alembic upgrade head`` against an SQLite DB at *db_path*.

    ``DATABASE_URL`` is explicitly removed from the environment for the
    duration of the migration so alembic uses the URL we set on the Config
    object (the test DB file) rather than any ambient production URL.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    root = pathlib.Path(__file__).resolve().parents[2]
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    saved = os.environ.pop("DATABASE_URL", None)
    try:
        command.upgrade(cfg, "head")
    finally:
        if saved is not None:
            os.environ.update({"DATABASE_URL": saved})


def _introspect(conn: Connection) -> dict:
    """Return a normalized schema snapshot for a SQLite connection.

    Excludes internal tables (``sqlite_*``) and alembic's bookkeeping
    table (``alembic_version``) so the comparison reflects only the
    application schema.

    Shape::

        {
            "tables": set[str],
            "columns": {table: [{"name", "type", "notnull", "default", "pk"}]},
            "indexes": {table: [{"name", "columns", "unique"}]},
            "foreign_keys": {table: [{"referred_table", "constrained_column",
                                      "referred_column", "on_delete", "on_update"}]},
        }
    """
    table_rows = conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' "
            "AND name != 'alembic_version' "
            "ORDER BY name"
        )
    ).fetchall()
    tables = {r[0] for r in table_rows}

    out: dict = {"tables": tables, "columns": {}, "indexes": {}, "foreign_keys": {}}

    for t in sorted(tables):
        col_rows = conn.execute(text(f"PRAGMA table_info('{t}')")).fetchall()
        out["columns"][t] = [
            {
                "name": r[1],
                "type": (r[2] or "").upper(),
                "notnull": r[3],
                "default": r[4],
                "pk": r[5],
            }
            for r in col_rows
        ]

        idx_rows = conn.execute(text(f"PRAGMA index_list('{t}')")).fetchall()
        idx_list = []
        for i in idx_rows:
            idx_name = i[1]
            # Skip implicit autoindexes generated by UNIQUE / PRIMARY KEY
            # constraints — they are byproducts of the constraint set, not
            # independently declared indexes, and their auto-numbering is
            # sensitive to creation order in ways that obscure real drift.
            if idx_name.startswith("sqlite_autoindex_"):
                continue
            info_rows = conn.execute(text(f"PRAGMA index_info('{idx_name}')")).fetchall()
            cols_in_idx = tuple(r[2] for r in info_rows)
            idx_list.append(
                {
                    "name": idx_name,
                    "columns": cols_in_idx,
                    "unique": bool(i[2]),
                }
            )
        out["indexes"][t] = sorted(idx_list, key=lambda x: x["name"])

        fk_rows = conn.execute(text(f"PRAGMA foreign_key_list('{t}')")).fetchall()
        fk_list = [
            {
                "referred_table": r[2],
                "constrained_column": r[3],
                "referred_column": r[4],
                "on_update": r[5],
                "on_delete": r[6],
            }
            for r in fk_rows
        ]
        out["foreign_keys"][t] = sorted(
            fk_list, key=lambda x: (x["referred_table"], x["constrained_column"])
        )

    return out


def _build_create_all_schema() -> dict:
    """Return the schema snapshot produced by ``Base.metadata.create_all``."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        return _introspect(conn)


def _build_migrated_schema(tmp_path: pathlib.Path) -> dict:
    """Return the schema snapshot produced by ``alembic upgrade head``."""
    db_path = str(tmp_path / "wp_d3_migrated.db")
    _run_migrations(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        return _introspect(conn)


@pytest.fixture(scope="module")
def create_all_schema() -> dict:
    return _build_create_all_schema()


@pytest.fixture(scope="module")
def migrated_schema(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict:
    tmp_path = tmp_path_factory.mktemp("wp_d3_migrated")
    return _build_migrated_schema(tmp_path)


class TestCreateAllMatchesAlembicUpgradeHead:
    """The schema produced by ``create_all`` must equal ``alembic upgrade head``.

    Each test below compares one structural dimension. Failures here reveal
    migration drift — the migrations do not reproduce what the ORM declares.
    """

    def test_create_all_matches_alembic_upgrade_head_tables(
        self, create_all_schema: dict, migrated_schema: dict
    ) -> None:
        ca = set(create_all_schema["tables"])
        mig = set(migrated_schema["tables"])
        missing_from_migrations = ca - mig
        extra_in_migrations = mig - ca
        assert missing_from_migrations == set() and extra_in_migrations == set(), (
            "Table-set drift between create_all and alembic upgrade head.\n"
            f"  Tables in ORM/create_all only (missing from migrations): "
            f"{sorted(missing_from_migrations)}\n"
            f"  Tables in migrations only (not in ORM): "
            f"{sorted(extra_in_migrations)}"
        )

    def test_create_all_matches_alembic_upgrade_head_columns(
        self, create_all_schema: dict, migrated_schema: dict
    ) -> None:
        common = set(create_all_schema["columns"]) & set(migrated_schema["columns"])
        diffs: list[str] = []
        for t in sorted(common):
            ca_cols = {c["name"]: c for c in create_all_schema["columns"][t]}
            mig_cols = {c["name"]: c for c in migrated_schema["columns"][t]}
            only_ca = set(ca_cols) - set(mig_cols)
            only_mig = set(mig_cols) - set(ca_cols)
            if only_ca or only_mig:
                diffs.append(
                    f"  {t}: only_create_all={sorted(only_ca)} "
                    f"only_migrations={sorted(only_mig)}"
                )
                continue
            for name in sorted(ca_cols):
                ca_type = ca_cols[name]["type"]
                mig_type = mig_cols[name]["type"]
                if ca_type != mig_type:
                    diffs.append(
                        f"  {t}.{name}: create_all type='{ca_type}' vs "
                        f"migrations type='{mig_type}'"
                    )
        assert not diffs, (
            "Column drift between create_all and alembic upgrade head:\n"
            + "\n".join(diffs)
        )

    def test_create_all_matches_alembic_upgrade_head_indexes(
        self, create_all_schema: dict, migrated_schema: dict
    ) -> None:
        common = set(create_all_schema["indexes"]) & set(migrated_schema["indexes"])
        diffs: list[str] = []
        for t in sorted(common):
            ca_idx = create_all_schema["indexes"][t]
            mig_idx = migrated_schema["indexes"][t]
            ca_keys = {(i["name"], i["columns"], i["unique"]) for i in ca_idx}
            mig_keys = {(i["name"], i["columns"], i["unique"]) for i in mig_idx}
            if ca_keys != mig_keys:
                only_ca = ca_keys - mig_keys
                only_mig = mig_keys - ca_keys
                diffs.append(
                    f"  {t}:\n    only_create_all={sorted(only_ca)}\n"
                    f"    only_migrations={sorted(only_mig)}"
                )
        assert not diffs, (
            "Index drift between create_all and alembic upgrade head:\n"
            + "\n".join(diffs)
        )

    def test_create_all_matches_alembic_upgrade_head_foreign_keys(
        self, create_all_schema: dict, migrated_schema: dict
    ) -> None:
        common = (
            set(create_all_schema["foreign_keys"])
            & set(migrated_schema["foreign_keys"])
        )
        diffs: list[str] = []
        for t in sorted(common):
            ca_fk = create_all_schema["foreign_keys"][t]
            mig_fk = migrated_schema["foreign_keys"][t]
            ca_keys = {
                (
                    fk["referred_table"],
                    fk["constrained_column"],
                    fk["referred_column"],
                    fk["on_delete"],
                    fk["on_update"],
                )
                for fk in ca_fk
            }
            mig_keys = {
                (
                    fk["referred_table"],
                    fk["constrained_column"],
                    fk["referred_column"],
                    fk["on_delete"],
                    fk["on_update"],
                )
                for fk in mig_fk
            }
            if ca_keys != mig_keys:
                only_ca = ca_keys - mig_keys
                only_mig = mig_keys - ca_keys
                diffs.append(
                    f"  {t}:\n    only_create_all={sorted(only_ca)}\n"
                    f"    only_migrations={sorted(only_mig)}"
                )
        assert not diffs, (
            "Foreign-key drift between create_all and alembic upgrade head:\n"
            + "\n".join(diffs)
        )
