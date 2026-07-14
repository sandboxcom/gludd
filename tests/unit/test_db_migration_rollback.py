"""DB migration rollback: alembic upgrade/downgrade round-trip tests.

Covers the migration infrastructure for configuration correctness, version chain
integrity, upgrade/downgrade parity, and full round-trip safety using both mocked
operations and real SQLite databases.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import pathlib
import tempfile
from typing import Any, cast
from unittest.mock import patch

from general_ludd.db.migrations import get_alembic_config

_VERSIONS_DIR = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_migration(name: str):
    """Load an alembic/versions/<name>.py module via importlib.

    Version filenames start with a digit so they are not importable as normal
    modules.
    """
    src = _VERSIONS_DIR / name
    spec = importlib.util.spec_from_file_location(f"migration_{src.stem}", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


def _migration_filenames():
    """Yield (filename, stem) for every .py file in the versions dir."""
    return [(p.name, p.stem) for p in sorted(_VERSIONS_DIR.glob("*.py"))]


def _collect_revisions() -> list[dict]:
    """Return every migration's revision + down_revision from the versions dir."""
    revs: list[dict] = []
    for fname, _stem in _migration_filenames():
        mod = _load_migration(fname)
        revs.append(
            {
                "filename": fname,
                "revision": getattr(mod, "revision", None),
                "down_revision": getattr(mod, "down_revision", None),
            }
        )
    return revs


def _parse_source_count(source: str, patterns: list[str]) -> int:
    """Count how many times any of *patterns* appears as a standalone word in *source*.

    This is used to verify upgrade/downgrade parity in migration source code
    without executing the migration at all.
    """
    count = 0
    for line in source.splitlines():
        stripped = line.strip()
        for pat in patterns:
            if pat in stripped:
                count += 1
    return count


# ---------------------------------------------------------------------------
# 1. Version chain integrity
# ---------------------------------------------------------------------------

class TestVersionChainIntegrity:
    def test_all_migrations_have_revision(self):
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            assert hasattr(mod, "revision"), f"{fname} missing 'revision'"
            assert isinstance(mod.revision, str), f"{fname} revision not a str"

    def test_all_migrations_have_down_revision(self):
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            assert hasattr(mod, "down_revision"), f"{fname} missing 'down_revision'"

    def test_chain_is_linear_via_script(self):
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(get_alembic_config())
        for rev in script.walk_revisions():
            assert len(rev.nextrev) <= 1, (
                f"revision {rev.revision} has multiple children {rev.nextrev}"
            )

    def test_single_head(self):
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(get_alembic_config())
        heads = script.get_heads()
        assert len(heads) == 1, f"expected single head, got {heads}"

    def test_down_revisions_form_contiguous_chain(self):
        revs = _collect_revisions()
        by_rev = {r["revision"]: r for r in revs if r["revision"]}

        for r in revs:
            if r["down_revision"] is None:
                continue
            parent = by_rev.get(r["down_revision"])
            assert parent is not None, (
                f"{r['filename']}: down_revision={r['down_revision']} not found"
            )

    def test_all_migrations_are_importable(self):
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            assert hasattr(mod, "upgrade"), f"{fname} missing upgrade()"
            assert hasattr(mod, "downgrade"), f"{fname} missing downgrade()"


# ---------------------------------------------------------------------------
# 2. Upgrade / downgrade parity (mocked op)
# ---------------------------------------------------------------------------

class TestMigration016RoundTrip:
    _FILENAME = "016_add_escalation_and_remediation.py"

    def test_upgrade_creates_tables(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        created = [c.args[0] for c in mock_op.create_table.call_args_list]
        assert "permission_escalation_request" in created
        assert "remediation_actions" in created

    def test_downgrade_drops_tables(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        dropped = [c.args[0] for c in mock_op.drop_table.call_args_list]
        assert "permission_escalation_request" in dropped
        assert "remediation_actions" in dropped

    def test_downgrade_reverses_upgrade_table_order(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        created = [c.args[0] for c in mock_op.create_table.call_args_list]
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        dropped = [c.args[0] for c in mock_op.drop_table.call_args_list]
        assert created[::-1] == dropped, (
            f"downgrade must reverse upgrade order; created={created}, dropped={dropped}"
        )

    def test_index_count_parity(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        created_idx = len(mock_op.create_index.call_args_list)
        with patch.object(mod, "op") as mock_op2:
            mod.downgrade()
        dropped_idx = len(mock_op2.drop_index.call_args_list)
        assert created_idx == dropped_idx, (
            f"index count mismatch: upgrade created {created_idx}, downgrade dropped {dropped_idx}"
        )
        assert created_idx > 0, "expected at least one index"


class TestMigration010RoundTrip:
    _FILENAME = "010_add_todo_scheduling_columns.py"

    def test_upgrade_adds_eight_columns(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        added = [c.args[0] for c in mock_op.add_column.call_args_list]
        assert len(added) == 8, f"expected 8 columns, got {len(added)}: {added}"

    def test_downgrade_drops_eight_columns(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        dropped = [c.args[0] for c in mock_op.drop_column.call_args_list]
        assert len(dropped) == 8, f"expected 8 columns, got {len(dropped)}: {dropped}"

    def test_upgrade_adds_one_index(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        assert len(mock_op.create_index.call_args_list) == 1

    def test_downgrade_drops_one_index(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        assert len(mock_op.drop_index.call_args_list) == 1


class TestMigration011RoundTrip:
    _FILENAME = "011_add_bucket_leases_expires_at_index.py"

    def test_upgrade_creates_index(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        created = mock_op.create_index.call_args_list
        assert len(created) == 1
        assert "ix_bucket_leases_expires_at" in created[0].args[0]

    def test_downgrade_drops_index(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        dropped = mock_op.drop_index.call_args_list
        assert len(dropped) == 1
        assert "ix_bucket_leases_expires_at" in dropped[0].args[0]


# ---------------------------------------------------------------------------
# 3. Full-chain upgrade + downgrade via real SQLite
# ---------------------------------------------------------------------------

class TestFullChainRoundTrip:
    """Round-trip the entire migration chain using a real SQLite database."""

    def test_upgrade_to_head_then_downgrade_to_base(self):
        from alembic import command
        from sqlalchemy import inspect

        get_alembic_config()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            url = f"sqlite:///{db_path}"
            db_cfg = get_alembic_config(url=url)

            # Guard: requires confirmation for destructive downgrade past 001
            command.upgrade(db_cfg, "head")

            # Verify tables exist after upgrade
            db_cfg.get_main_option("sqlalchemy.url")
            from sqlalchemy import create_engine

            eng = create_engine(url)
            with eng.connect():
                tables = inspect(eng).get_table_names()
            assert len(tables) > 0, "head upgrade produced no tables"

            # Downgrade to base (requires ALEMBIC_DOWNGRADE_CONFIRMED for 001)
            with patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
                command.downgrade(db_cfg, "base")

            # Verify all tables are gone
            with eng.connect():
                tables = inspect(eng).get_table_names()
            assert tables == ["alembic_version"], (
                f"base downgrade left tables: {tables}"
            )
            eng.dispose()
        finally:
            os.unlink(db_path)

    def test_upgrade_is_idempotent(self):
        from alembic import command
        from sqlalchemy import inspect

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            url = f"sqlite:///{db_path}"
            db_cfg = get_alembic_config(url=url)

            command.upgrade(db_cfg, "head")

            from sqlalchemy import create_engine

            eng = create_engine(url)
            first_tables = inspect(eng).get_table_names()

            # Run upgrade again — must be a no-op
            command.upgrade(db_cfg, "head")
            second_tables = inspect(eng).get_table_names()

            assert first_tables == second_tables, (
                f"idempotent upgrade changed table list: "
                f"first={first_tables}, second={second_tables}"
            )
            eng.dispose()
        finally:
            os.unlink(db_path)

    def test_stamp_head_then_stamp_base(self):
        from alembic import command

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            url = f"sqlite:///{db_path}"
            db_cfg = get_alembic_config(url=url)

            command.stamp(db_cfg, "head")

            from alembic.script import ScriptDirectory

            script = ScriptDirectory.from_config(db_cfg)
            head = script.get_heads()[0]

            from alembic.runtime.migration import MigrationContext
            from sqlalchemy import create_engine

            eng = create_engine(url)
            with eng.connect() as conn:
                ctx = MigrationContext.configure(conn)
                current = ctx.get_current_revision()
            assert current == head, (
                f"stamp head did not set current revision; expected {head}, got {current}"
            )

            command.stamp(db_cfg, "base")
            with eng.connect() as conn:
                ctx = MigrationContext.configure(conn)
                current = ctx.get_current_revision()
            assert current is None, (
                f"stamp base did not clear revision; got {current}"
            )
            eng.dispose()
        finally:
            os.unlink(db_path)

    def test_stamp_then_upgrade_no_errors(self):
        from alembic import command
        from sqlalchemy import inspect

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            url = f"sqlite:///{db_path}"
            db_cfg = get_alembic_config(url=url)

            # Upgrade to a mid-point revision, then upgrade the rest
            command.upgrade(db_cfg, "010")
            command.upgrade(db_cfg, "head")

            from sqlalchemy import create_engine

            eng = create_engine(url)
            tables = inspect(eng).get_table_names()
            assert "todos" in tables, "upgrade from mid-stamp missing todos"
            eng.dispose()
        finally:
            os.unlink(db_path)

    def test_one_revision_up_down_roundtrip(self):
        """Upgrade and downgrade a single revision individually."""
        from alembic import command
        from sqlalchemy import inspect

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            url = f"sqlite:///{db_path}"
            db_cfg = get_alembic_config(url=url)

            # Start at 009, upgrade to 010, then downgrade back to 009
            command.upgrade(db_cfg, "009")
            command.upgrade(db_cfg, "010")

            from sqlalchemy import create_engine

            eng = create_engine(url)
            tables_up = inspect(eng).get_table_names()
            assert "todos" in tables_up

            command.downgrade(db_cfg, "009")
            tables_down = inspect(eng).get_table_names()
            assert tables_up == tables_down, (
                f"single-revision round-trip changed tables: "
                f"up={tables_up}, down={tables_down}"
            )
            eng.dispose()
        finally:
            os.unlink(db_path)

    def test_002_sqlite_upgrade_then_downgrade(self):
        """Round-trip the first 3 revisions (001->002->003->002->001)."""
        from alembic import command
        from sqlalchemy import inspect

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            url = f"sqlite:///{db_path}"
            db_cfg = get_alembic_config(url=url)

            command.upgrade(db_cfg, "003")

            from sqlalchemy import create_engine

            eng = create_engine(url)
            tables_003 = inspect(eng).get_table_names()
            assert len(tables_003) > 0

            # Verify plan_artifact column exists after 003 upgrade
            cols_003 = {c["name"] for c in inspect(eng).get_columns("todos")}
            assert "plan_artifact" in cols_003

            command.downgrade(db_cfg, "002")
            # Column removed but tables unchanged (003 only adds a column)
            cols_002 = {c["name"] for c in inspect(eng).get_columns("todos")}
            assert "plan_artifact" not in cols_002, (
                "downgrade 003->002 should remove plan_artifact column"
            )

            # Downgrade back to 001
            with patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
                command.downgrade(db_cfg, "001")
            tables_001 = inspect(eng).get_table_names()
            assert len(tables_001) > 0

            # Full downgrade to base
            with patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
                command.downgrade(db_cfg, "base")
            tables_base = inspect(eng).get_table_names()
            assert tables_base == ["alembic_version"], (
                f"base downgrade left tables: {tables_base}"
            )
            eng.dispose()
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# 4. Config round-trips
# ---------------------------------------------------------------------------

class TestConfigRoundTrip:
    def test_config_stale_cache_detected(self):
        """Config changes (DATABASE_URL) propagate to new callers."""
        cfg1 = get_alembic_config(url="sqlite:///one.db")
        cfg2 = get_alembic_config(url="sqlite:///two.db")
        u1 = cfg1.get_main_option("sqlalchemy.url")
        u2 = cfg2.get_main_option("sqlalchemy.url")
        assert u1 != u2, "subsequent get_alembic_config calls must honour different urls"

    def test_env_var_propagation(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://a:b@c/d"}):
            cfg = get_alembic_config()
            assert "postgresql" in cfg.get_main_option("sqlalchemy.url")

    def test_explicit_url_trumps_env(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://a:b@c/d"}):
            cfg = get_alembic_config(url="sqlite:///override.db")
            assert "override.db" in cfg.get_main_option("sqlalchemy.url")


# ---------------------------------------------------------------------------
# 5. Migration file structural audit
# ---------------------------------------------------------------------------

class TestAllMigrationsUpgradeDowngradeParity:
    """Every migration's upgrade() and downgrade() must have balanced operations."""

    _OP_PAIRS: tuple[tuple[str, str], ...] = (
        ("create_table", "drop_table"),
        ("add_column", "drop_column"),
        ("create_index", "drop_index"),
        ("create_unique_constraint", "drop_constraint"),
        ("create_foreign_key", "drop_constraint"),
    )

    def test_every_migration_has_upgrade_and_downgrade(self):
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            assert callable(getattr(mod, "upgrade", None)), (
                f"{fname}: upgrade must be callable"
            )
            assert callable(getattr(mod, "downgrade", None)), (
                f"{fname}: downgrade must be callable"
            )

    def test_create_table_migrations_have_corresponding_drop_table(self):
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
                mod.upgrade()
            create_count = len(mock_op.create_table.call_args_list)
            with patch.object(mod, "op") as mock_op2, \
                 patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}), \
                 contextlib.suppress(Exception):
                mod.downgrade()
            drop_count = len(mock_op2.drop_table.call_args_list)
            assert create_count == drop_count, (
                f"{fname}: upgrade creates {create_count} tables, "
                f"downgrade drops {drop_count}"
            )

    def test_add_column_migrations_have_corresponding_drop_column(self):
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
                mod.upgrade()
            add_count = len(mock_op.add_column.call_args_list)
            if add_count == 0 and mock_op.batch_alter_table.called:
                batch_mock = mock_op.batch_alter_table.return_value.__enter__.return_value
                add_count = len(batch_mock.add_column.call_args_list)
            with patch.object(mod, "op") as mock_op2, \
                 patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}), \
                 contextlib.suppress(Exception):
                mod.downgrade()
            drop_count = len(mock_op2.drop_column.call_args_list)
            if drop_count == 0 and mock_op2.batch_alter_table.called:
                batch_mock = mock_op2.batch_alter_table.return_value.__enter__.return_value
                drop_count = len(batch_mock.drop_column.call_args_list)
            assert add_count == drop_count, (
                f"{fname}: upgrade adds {add_count} columns, "
                f"downgrade drops {drop_count}"
            )

    def test_create_index_migrations_have_corresponding_drop_index(self):
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
                mod.upgrade()
            create_count = len(mock_op.create_index.call_args_list)
            with patch.object(mod, "op") as mock_op2, \
                 patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}), \
                 contextlib.suppress(Exception):
                mod.downgrade()
            drop_count = len(mock_op2.drop_index.call_args_list)
            if drop_count == 0 and mock_op2.batch_alter_table.called:
                batch_mock = mock_op2.batch_alter_table.return_value.__enter__.return_value
                drop_count = len(batch_mock.drop_index.call_args_list)
            assert create_count == drop_count, (
                f"{fname}: upgrade creates {create_count} indexes, "
                f"downgrade drops {drop_count}"
            )
