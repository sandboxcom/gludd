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
The five tests below assert the two schemas are identical at the table,
column, index, foreign-key, and CHECK-constraint levels.

When any of these tests fail, the failure message IS the migration drift
delta. Fixing the migrations to match the ORM is a separate task — this test
only REVEALS the drift.

Current state
-------------
All five tests pass. Historical drift was resolved by migrations:

* **Index drift** (``benchmark_results``, ``memory_records``,
  ``model_performance``, ``ornith_training_pairs``, ``task_decisions``) —
  resolved by migration 024, which added missing indexes and corrected
  name-prefix mismatches and uniqueness constraints to match the ORM.
* **Foreign key drift** (``audit_events``, ``benchmark_results``,
  ``bucket_leases``, ``queues``, ``task_decisions``, ``task_returns``,
  ``todos``, ``variable_namespaces``, ``todo_events``, ``variable_values``)
  — resolved by migrations 017 and 026, which re-added ``ondelete``
  clauses (``SET NULL`` / ``CASCADE``) to match the ORM declarations.

A failure in any of the five tests now indicates a **new** migration-model
mismatch — i.e. a migration or model change that introduced drift after the
historical fixes above were applied.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from general_ludd.db.models import Base

# Matches `CONSTRAINT <name> CHECK (` in a CREATE TABLE statement. The
# condition itself (which may contain nested parens, e.g. `length(col) <= N`)
# is extracted separately by walking paren-depth from the end of this match —
# a single regex cannot balance arbitrarily nested parens.
_CHECK_CONSTRAINT_HEADER_RE = re.compile(
    r"CONSTRAINT\s+(\S+)\s+CHECK\s*\(", re.IGNORECASE
)


def _extract_check_constraints(create_table_sql: str) -> set[tuple[str, str]]:
    """Return ``{(constraint_name, normalized_condition), ...}`` from DDL text.

    ``sqlite_master.sql`` for a table gives the exact ``CREATE TABLE`` text
    (whether produced by ``create_all`` or by an alembic batch-recreate), so
    parsing it directly is the only way to see CHECK constraint conditions —
    ``PRAGMA table_info`` does not expose them. Conditions like
    ``length(col) <= N`` contain nested parens, so the closing paren of the
    CHECK clause is found by balance-counting rather than a greedy regex.
    """
    out: set[tuple[str, str]] = set()
    for match in _CHECK_CONSTRAINT_HEADER_RE.finditer(create_table_sql):
        name = match.group(1)
        start = match.end()  # just past the CHECK clause's opening "("
        depth = 1
        i = start
        while i < len(create_table_sql) and depth > 0:
            ch = create_table_sql[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        cond = create_table_sql[start : i - 1]
        normalized = " ".join(cond.split())
        out.add((name, normalized))
    return out


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
            "check_constraints": {table: {(name, normalized_condition), ...}},
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

    out: dict = {
        "tables": tables,
        "columns": {},
        "indexes": {},
        "foreign_keys": {},
        "check_constraints": {},
    }

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

        create_sql_row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name = :t"),
            {"t": t},
        ).fetchone()
        create_sql = (create_sql_row[0] or "") if create_sql_row else ""
        out["check_constraints"][t] = _extract_check_constraints(create_sql)

    return out


def _build_create_all_schema() -> dict:
    """Return the schema snapshot produced by ``Base.metadata.create_all``."""
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            return _introspect(conn)
    finally:
        engine.dispose()


def _build_migrated_schema(tmp_path: pathlib.Path) -> dict:
    """Return the schema snapshot produced by ``alembic upgrade head``."""
    db_path = str(tmp_path / "wp_d3_migrated.db")
    _run_migrations(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            return _introspect(conn)
    finally:
        engine.dispose()


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

    def test_create_all_matches_alembic_upgrade_head_check_constraints(
        self, create_all_schema: dict, migrated_schema: dict
    ) -> None:
        common = (
            set(create_all_schema["check_constraints"])
            & set(migrated_schema["check_constraints"])
        )
        diffs: list[str] = []
        for t in sorted(common):
            ca_keys = create_all_schema["check_constraints"][t]
            mig_keys = migrated_schema["check_constraints"][t]
            if ca_keys != mig_keys:
                only_ca = ca_keys - mig_keys
                only_mig = mig_keys - ca_keys
                diffs.append(
                    f"  {t}:\n    only_create_all={sorted(only_ca)}\n"
                    f"    only_migrations={sorted(only_mig)}"
                )
        assert not diffs, (
            "CHECK-constraint drift between create_all and alembic upgrade head:\n"
            + "\n".join(diffs)
        )
