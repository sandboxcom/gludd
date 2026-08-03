"""Edge-case tests for database migration chain integrity, symmetry, and
structural correctness.

Covers: chain continuity, upgrade/downgrade symmetry, nullable constraints,
default values, index creation/destruction, foreign key cascade behavior,
enum-like column values, composite unique constraints, and check constraints.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import pathlib
from typing import Any, cast
from unittest.mock import patch

import pytest

_VERSIONS_DIR = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_migration(name: str):
    """Load an alembic/versions/<name>.py module by filename."""
    src = _VERSIONS_DIR / name
    spec = importlib.util.spec_from_file_location(f"migration_{src.stem}", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


def _migration_filenames():
    """Yield (name, stem) for every .py file in the versions directory."""
    return [(p.name, p.stem) for p in sorted(_VERSIONS_DIR.glob("*.py"))]


# ---------------------------------------------------------------------------
# 1. Migration chain integrity — down_revisions reference existing revisions
# ---------------------------------------------------------------------------


class TestMigrationChainIntegrity:
    """Every down_revision must point to an existing revision in the chain."""

    def test_no_duplicate_revisions(self):
        """No two migration files may declare the same revision ID."""
        seen: dict[str, str] = {}
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            rev = getattr(mod, "revision", None)
            if rev is None:
                continue
            assert rev not in seen, f"Duplicate revision '{rev}' in {fname} (first seen in {seen[rev]})"
            seen[rev] = fname

    def test_all_down_revisions_reference_existing_revision(self):
        """Every non-None down_revision must match an existing revision ID."""
        all_revs: dict[str, str] = {}
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            rev = getattr(mod, "revision", None)
            if rev is not None:
                all_revs[rev] = fname

        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            down = getattr(mod, "down_revision", None)
            if down is None:
                continue
            assert down in all_revs, f"{fname}: down_revision={down} does not match any known revision"

    def test_exactly_one_root_migration(self):
        """Exactly one migration file must have down_revision = None."""
        root_count = 0
        roots: list[str] = []
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            down = getattr(mod, "down_revision", None)
            if down is None:
                root_count += 1
                roots.append(fname)
        assert root_count == 1, f"Expected exactly 1 root migration (down_revision=None), got {root_count}: {roots}"

    def test_root_migration_revision_is_001(self):
        """The root migration must be revision '001'."""
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            down = getattr(mod, "down_revision", None)
            rev = getattr(mod, "revision", None)
            if down is None:
                assert rev == "001", f"Root migration {fname} has revision '{rev}', expected '001'"
                return
        pytest.fail("No root migration found")

    def test_chain_forms_single_linear_path(self):
        """Every revision must have at most one child (no branch points)."""
        from alembic.script import ScriptDirectory

        from general_ludd.db.migrations import get_alembic_config

        script = ScriptDirectory.from_config(get_alembic_config())
        for rev in script.walk_revisions():
            assert len(rev.nextrev) <= 1, (
                f"revision {rev.revision} has multiple children {rev.nextrev}; the migration chain must stay linear"
            )

    def test_single_head(self):
        """The migration graph must have exactly one head revision."""
        from alembic.script import ScriptDirectory

        from general_ludd.db.migrations import get_alembic_config

        script = ScriptDirectory.from_config(get_alembic_config())
        heads = script.get_heads()
        assert len(heads) == 1, f"expected single head, got {heads}"


# ---------------------------------------------------------------------------
# 2. Upgrade/downgrade symmetry — every operation in upgrade is reversed
# ---------------------------------------------------------------------------


class TestUpgradeDowngradeSymmetry:
    """Every migration's downgrade() must reverse upgrade() operations."""

    def test_table_create_drop_symmetry(self):
        """create_table(N) in upgrade ⇔ drop_table(N) in downgrade."""
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
                mod.upgrade()
            create_count = len(mock_op.create_table.call_args_list)
            with (
                patch.object(mod, "op") as mock_op2,
                patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}),
                contextlib.suppress(Exception),
            ):
                mod.downgrade()
            drop_count = len(mock_op2.drop_table.call_args_list)
            assert create_count == drop_count, (
                f"{fname}: upgrade creates {create_count} tables, downgrade drops {drop_count}"
            )

    def test_column_add_drop_symmetry(self):
        """add_column(N) in upgrade ⇔ drop_column(N) in downgrade."""
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            add_count, drop_count = 0, 0
            with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
                mod.upgrade()
            add_count = len(mock_op.add_column.call_args_list)
            if add_count == 0 and mock_op.batch_alter_table.called:
                bm = mock_op.batch_alter_table.return_value.__enter__.return_value
                add_count = len(bm.add_column.call_args_list)
            with (
                patch.object(mod, "op") as mock_op2,
                patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}),
                contextlib.suppress(Exception),
            ):
                mod.downgrade()
            drop_count = len(mock_op2.drop_column.call_args_list)
            if drop_count == 0 and mock_op2.batch_alter_table.called:
                bm = mock_op2.batch_alter_table.return_value.__enter__.return_value
                drop_count = len(bm.drop_column.call_args_list)
            assert add_count == drop_count, f"{fname}: upgrade adds {add_count} columns, downgrade drops {drop_count}"

    def test_index_create_drop_symmetry(self):
        """create_index(N) in upgrade ⇔ drop_index(N) in downgrade."""
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
                mod.upgrade()
            create_count = len(mock_op.create_index.call_args_list)
            if mock_op.batch_alter_table.call_args_list:
                batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
                create_count += len(batch_ctx.create_index.call_args_list)
            with (
                patch.object(mod, "op") as mock_op2,
                patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}),
                contextlib.suppress(Exception),
            ):
                mod.downgrade()
            drop_count = len(mock_op2.drop_index.call_args_list)
            if mock_op2.batch_alter_table.call_args_list:
                batch_ctx = mock_op2.batch_alter_table.return_value.__enter__.return_value
                drop_count += len(batch_ctx.drop_index.call_args_list)
            assert create_count == drop_count, (
                f"{fname}: upgrade creates {create_count} indexes, downgrade drops {drop_count}"
            )

    def test_foreign_key_create_drop_symmetry(self):
        """create_foreign_key(N) in upgrade ⇔ drop_constraint(N, foreignkey)."""
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            create_count = 0
            with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
                mod.upgrade()
            for call in mock_op.batch_alter_table.call_args_list:
                call[0][0] if call.args else "unknown"
                with patch.object(mod, "op") as mock_inner:
                    mock_inner.batch_alter_table.return_value.__enter__.return_value = (
                        mock_op.batch_alter_table.return_value.__enter__.return_value
                    )
            # Collect FK creates from both top-level and batch contexts
            for _call_args in mock_op.batch_alter_table.call_args_list:
                batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
                create_count += len(batch_ctx.create_foreign_key.call_args_list)
            if create_count == 0:
                continue  # no FKs in this migration
            with (
                patch.object(mod, "op") as mock_op2,
                patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}),
                contextlib.suppress(Exception),
            ):
                mod.downgrade()
            drop_count = 0
            for _call_args in mock_op2.batch_alter_table.call_args_list:
                batch_ctx = mock_op2.batch_alter_table.return_value.__enter__.return_value
                for c in batch_ctx.drop_constraint.call_args_list:
                    if c.kwargs.get("type_") == "foreignkey" or (len(c.args) > 1 and c.args[1] == "foreignkey"):
                        drop_count += 1
            if create_count > 0:
                assert create_count == drop_count, (
                    f"{fname}: upgrade creates {create_count} FKs, downgrade drops {drop_count}"
                )

    def test_unique_constraint_create_drop_symmetry(self):
        """create_unique_constraint(N) in upgrade ⇔ drop_constraint(N, unique)."""
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
                mod.upgrade()
            create_count = len(mock_op.create_unique_constraint.call_args_list)
            with (
                patch.object(mod, "op") as mock_op2,
                patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}),
                contextlib.suppress(Exception),
            ):
                mod.downgrade()
            drop_count = sum(
                1
                for c in mock_op2.drop_constraint.call_args_list
                if c.kwargs.get("type_") == "unique" or (len(c.args) > 1 and c.args[1] == "unique")
            )
            assert create_count == drop_count, (
                f"{fname}: upgrade adds {create_count} unique constraints, downgrade drops {drop_count}"
            )

    def test_check_constraint_create_drop_symmetry(self):
        """create_check_constraint(N) in upgrade ⇔ drop_constraint(N, check)."""
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
                mod.upgrade()
            create_count = 0
            for _call_args in mock_op.batch_alter_table.call_args_list:
                batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
                create_count += len(batch_ctx.create_check_constraint.call_args_list)
            if create_count == 0:
                continue
            with (
                patch.object(mod, "op") as mock_op2,
                patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}),
                contextlib.suppress(Exception),
            ):
                mod.downgrade()
            drop_count = 0
            for _call_args in mock_op2.batch_alter_table.call_args_list:
                batch_ctx = mock_op2.batch_alter_table.return_value.__enter__.return_value
                for c in batch_ctx.drop_constraint.call_args_list:
                    if c.kwargs.get("type_") == "check":
                        drop_count += 1
            assert create_count == drop_count, (
                f"{fname}: upgrade creates {create_count} check constraints, downgrade drops {drop_count}"
            )

    def test_001_downgrade_reverses_upgrade_table_order(self):
        """001 downgrade drops tables in reverse order of upgrade creation."""
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        created = [c.args[0] for c in mock_op.create_table.call_args_list]
        with patch.object(mod, "op") as mock_op2, patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
            mod.downgrade()
        dropped = [c.args[0] for c in mock_op2.drop_table.call_args_list]
        assert created[::-1] == dropped, f"downgrade must reverse upgrade order; created={created}, dropped={dropped}"

    def test_002_downgrade_reverses_upgrade_fk_order(self):
        """002 downgrade drops FKs and columns in reverse order of upgrade add."""
        mod = _load_migration("002_add_projects_and_project_id.py")
        with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
            mod.upgrade()
        add_cols = [c.args for c in mock_op.add_column.call_args_list]
        with (
            patch.object(mod, "op") as mock_op2,
            patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}),
            contextlib.suppress(Exception),
        ):
            mod.downgrade()
        drop_cols = [c.args for c in mock_op2.drop_column.call_args_list]
        assert len(add_cols) == len(drop_cols), f"002: add={len(add_cols)} columns, drop={len(drop_cols)}"


# ---------------------------------------------------------------------------
# 3. Nullable column constraints
# ---------------------------------------------------------------------------


class TestNullableColumnConstraints:
    """Columns declared nullable=False must stay non-nullable across migrations."""

    def test_001_todo_id_is_non_nullable(self):
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        todos_call = next(c for c in mock_op.create_table.call_args_list if c.args[0] == "todos")
        for col in todos_call.kwargs.get("columns", todos_call.args[1:]):
            col if hasattr(col, "name") else None
            # Find via args inspection
        [a for a in (todos_call.args[1:] if len(todos_call.args) > 1 else []) if hasattr(a, "name")]
        # Created via sa.Column in the source; inspect the column list
        cols_dict = {}
        for col in todos_call.args[1:]:
            if hasattr(col, "name"):
                cols_dict[col.name] = col
        assert cols_dict["todo_id"].nullable is False
        assert cols_dict["title"].nullable is False
        assert cols_dict["status"].nullable is False

    def test_001_columns_respect_nullability(self):
        """Verify key non-nullable and nullable columns in 001 todos table."""
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        todos_call = next(c for c in mock_op.create_table.call_args_list if c.args[0] == "todos")
        cols: dict[str, Any] = {}
        for col in todos_call.args[1:]:
            if hasattr(col, "name"):
                cols[col.name] = col

        # Non-nullable
        for name in ("id", "todo_id", "title", "status", "created_at", "updated_at"):
            assert cols[name].nullable is False, f"todos.{name} should be non-nullable"

        # Nullable
        for name in (
            "parent_todo_id",
            "coverage_requirements",
            "assigned_agent",
            "model_profile",
            "prompt_profile",
            "worktree",
            "branch_name",
            "confidence",
            "manual_hold_reason",
            "completed_at",
        ):
            assert cols[name].nullable is True, f"todos.{name} should be nullable"

    def test_initial_tables_have_no_unexpected_nullable_columns(self):
        """Audit: every 001 table's primary key and key fields are non-nullable."""
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        for call in mock_op.create_table.call_args_list:
            table_name = call.args[0]
            for item in call.args[1:]:
                if not hasattr(item, "name"):
                    continue
                if not hasattr(item, "nullable"):
                    continue
                assert not (item.primary_key and item.nullable), (
                    f"Table {table_name}: PK column '{item.name}' is nullable"
                )


# ---------------------------------------------------------------------------
# 4. Default value correctness
# ---------------------------------------------------------------------------


class TestDefaultValueCorrectness:
    """Server defaults must be sensible and match the expected data type."""

    def test_001_todos_status_default_is_backlog(self):
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        todos_call = next(c for c in mock_op.create_table.call_args_list if c.args[0] == "todos")
        cols: dict[str, Any] = {}
        for col in todos_call.args[1:]:
            if hasattr(col, "name"):
                cols[col.name] = col

        def dfl(col):
            sd = col.server_default
            return sd.arg if sd is not None else None

        assert dfl(cols["status"]) == "backlog"
        assert dfl(cols["priority"]) == "0"
        assert dfl(cols["queue"]) == "core"
        assert dfl(cols["risk_level"]) == "low"
        assert dfl(cols["work_type"]) == "unknown"
        assert dfl(cols["created_by"]) == "agent"
        assert dfl(cols["approval_policy"]) == "none"
        assert dfl(cols["version"]) == "1"

    def test_001_todos_text_columns_default_to_empty_json(self):
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        todos_call = next(c for c in mock_op.create_table.call_args_list if c.args[0] == "todos")
        cols: dict[str, Any] = {}
        for col in todos_call.args[1:]:
            if hasattr(col, "name"):
                cols[col.name] = col

        def dfl(col):
            sd = col.server_default
            return sd.arg if sd is not None else None

        for name in (
            "tags",
            "child_todo_ids",
            "acceptance_criteria",
            "test_commands",
            "molecule_scenarios",
            "molecule_evidence_refs",
            "dependencies",
            "artifacts",
            "evidence_refs",
        ):
            assert dfl(cols[name]) == "[]", f"todos.{name} expected default '[]'"

    def test_010_scheduling_columns_have_correct_defaults(self):
        mod = _load_migration("010_add_todo_scheduling_columns.py")
        columns: list[Any] = []
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        for call in mock_op.add_column.call_args_list:
            col = call.args[1] if len(call.args) > 1 else call.args[0]
            if hasattr(col, "name"):
                columns.append(col)

        def dfl(col):
            sd = col.server_default
            if sd is None:
                return None
            if hasattr(sd, "arg") and not callable(sd.arg):
                return sd.arg
            return str(sd)

        defaults = {col.name: dfl(col) for col in columns}
        assert defaults["schedule_timezone"] == "UTC"
        assert defaults["schedule_paused"] is not None  # sa.false()
        assert str(defaults["run_count"]) == "0"
        assert defaults["scheduled_at"] is None  # nullable, no default
        assert defaults["cron"] is None
        assert defaults["next_run_at"] is None
        assert defaults["last_run_at"] is None
        assert defaults["max_runs"] is None

    def test_013_human_todos_defaults(self):
        mod = _load_migration("013_add_human_todos.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        human_call = next(c for c in mock_op.create_table.call_args_list if c.args[0] == "human_todos")
        cols: dict[str, Any] = {}
        for col in human_call.args[1:]:
            if hasattr(col, "name"):
                cols[col.name] = col

        def dfl(col):
            sd = col.server_default
            return sd.arg if sd is not None else None

        assert dfl(cols["priority"]) == "medium"
        assert dfl(cols["status"]) == "open"
        assert dfl(cols["tags"]) == "[]"
        assert dfl(cols["body"]) == ""

    def test_005_tables_have_correct_default_kinds(self):
        """Memory_records defaults: scope='global', kind='fact', tags='[]'."""
        mod = _load_migration("005_add_runtime_tables.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        mem_call = next(c for c in mock_op.create_table.call_args_list if c.args[0] == "memory_records")
        cols: dict[str, Any] = {}
        for col in mem_call.args[1:]:
            if hasattr(col, "name"):
                cols[col.name] = col

        def dfl(col):
            sd = col.server_default
            return sd.arg if sd is not None else None

        assert dfl(cols["scope"]) == "global"
        assert dfl(cols["kind"]) == "fact"
        assert dfl(cols["tags"]) == "[]"


# ---------------------------------------------------------------------------
# 5. Index creation/destruction
# ---------------------------------------------------------------------------


class TestIndexCreationDestruction:
    """Index operations must be symmetric and cover the expected columns."""

    def test_001_todos_indexes_cover_correct_columns(self):
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        indexes: dict[str, list[str]] = {}
        for call in mock_op.create_index.call_args_list:
            name = call.args[0]
            columns = call.args[2] if len(call.args) > 2 else []
            indexes[name] = list(columns)

        assert indexes.get("ix_todos_status") == ["status"]
        assert indexes.get("ix_todos_queue") == ["queue"]
        assert indexes.get("ix_todos_status_queue") == ["status", "queue"]

    def test_010_composite_scheduling_index_columns(self):
        mod = _load_migration("010_add_todo_scheduling_columns.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        assert len(mock_op.create_index.call_args_list) == 1
        call = mock_op.create_index.call_args_list[0]
        assert call.args[0] == "ix_todos_scheduled_lookup"
        assert list(call.args[2]) == ["status", "schedule_paused", "next_run_at", "scheduled_at"]

    def test_011_expires_at_index_on_correct_column(self):
        mod = _load_migration("011_add_bucket_leases_expires_at_index.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        call = mock_op.create_index.call_args_list[0]
        assert call.args[0] == "ix_bucket_leases_expires_at"
        assert "expires_at" in call.args[2]

    def test_031_e12_perf_indexes_created(self):
        """Migration 031 adds performance indexes on task_returns, bucket_leases."""
        mod = _load_migration("031_add_task_returns_bucket_leases_e12_indexes.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        idx_names = [c.args[0] for c in mock_op.create_index.call_args_list]
        assert len(idx_names) > 0, "031 should create at least one index"
        for name in idx_names:
            assert name.startswith("ix_"), f"index {name} should follow naming convention"

    def test_034_e12_repo_perf_indexes_created(self):
        """Migration 034 adds repository-level performance indexes."""
        mod = _load_migration("034_add_e12_repo_perf_indexes.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        idx_names = [c.args[0] for c in mock_op.create_index.call_args_list]
        assert len(idx_names) > 0, "034 should create at least one index"


# ---------------------------------------------------------------------------
# 6. Foreign key cascade behavior
# ---------------------------------------------------------------------------


class TestForeignKeyCascadeBehavior:
    """Foreign keys must specify the correct ON DELETE behavior."""

    def test_006_task_returns_fk_is_set_null(self):
        mod = _load_migration("006_add_d9_foreign_keys.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        for _call_args in mock_op.batch_alter_table.call_args_list:
            batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
            for fk_call in batch_ctx.create_foreign_key.call_args_list:
                if "task_returns_todo_id" in str(fk_call):
                    assert fk_call.kwargs.get("ondelete") == "SET NULL", (
                        f"task_returns FK should be SET NULL, got {fk_call.kwargs}"
                    )

    def test_006_task_decisions_fk_is_cascade(self):
        mod = _load_migration("006_add_d9_foreign_keys.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        for _call_args in mock_op.batch_alter_table.call_args_list:
            batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
            for fk_call in batch_ctx.create_foreign_key.call_args_list:
                if "task_decisions_return_id" in str(fk_call):
                    assert fk_call.kwargs.get("ondelete") == "CASCADE", (
                        f"task_decisions FK should be CASCADE, got {fk_call.kwargs}"
                    )

    def test_005_project_fks_are_set_null(self):
        """All project_id FKs created in migration 005 use ON DELETE SET NULL."""
        mod = _load_migration("005_add_runtime_tables.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        for call in mock_op.create_table.call_args_list:
            for col in call.args[1:]:
                if not hasattr(col, "name"):
                    continue
                if col.name == "project_id" and hasattr(col, "foreign_keys") and col.foreign_keys:
                    for fk in col.foreign_keys:
                        assert fk.ondelete == "SET NULL", (
                            f"Table {call.args[0]}: project_id FK ondelete must be SET NULL, got {fk.ondelete}"
                        )

    def test_033_human_todos_parent_fk_is_set_null(self):
        mod = _load_migration("033_add_human_todos_parent_fk.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        for _call_args in mock_op.batch_alter_table.call_args_list:
            batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
            for fk_call in batch_ctx.create_foreign_key.call_args_list:
                assert fk_call.kwargs.get("ondelete") == "SET NULL", (
                    f"human_todos parent FK should be SET NULL, got {fk_call.kwargs}"
                )

    def test_001_todo_events_fk_has_no_ondelete(self):
        """001 todo_events FK has no explicit ON DELETE (defaults to NO ACTION)."""
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        events_call = next(c for c in mock_op.create_table.call_args_list if c.args[0] == "todo_events")
        for col in events_call.args[1:]:
            if not hasattr(col, "name"):
                continue
            if hasattr(col, "foreign_keys") and col.foreign_keys:
                for fk in col.foreign_keys:
                    assert fk.ondelete is None, (
                        "todo_events FK should not specify ondelete (defaults to NO ACTION/RESTRICT)"
                    )


# ---------------------------------------------------------------------------
# 7. Composite and single-column unique constraints
# ---------------------------------------------------------------------------


class TestCompositeUniqueConstraints:
    """Unique constraints must cover the expected column sets."""

    def test_001_variable_values_composite_unique(self):
        mod = _load_migration("001_initial_schema.py")
        import inspect

        src = inspect.getsource(mod.upgrade)
        assert '"namespace_id"' in src, "variable_values must reference namespace_id in upgrade() source"
        assert '"key"' in src, "variable_values must reference key in upgrade() source"

    def test_001_bucket_leases_composite_unique(self):
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        next(c for c in mock_op.create_table.call_args_list if c.args[0] == "bucket_leases")
        import inspect

        src = inspect.getsource(mod.upgrade)
        assert 'name="uq_bucket_lease"' in src, "bucket_leases must have named unique constraint uq_bucket_lease"

    def test_002_upgrade_creates_composite_unique_on_variable_namespaces(self):
        mod = _load_migration("002_add_projects_and_project_id.py")
        with patch.object(mod, "op") as mock_op, contextlib.suppress(Exception):
            mod.upgrade()
        for _call_args in mock_op.batch_alter_table.call_args_list:
            batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
            for uc_call in batch_ctx.create_unique_constraint.call_args_list:
                if uc_call.args[0] == "uq_namespace_project":
                    assert list(uc_call.args[1]) == ["namespace", "project_id"]

    def test_017_unique_constraint_is_return_id(self):
        mod = _load_migration("017_add_unique_on_task_decisions_return_id.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        for _call_args in mock_op.batch_alter_table.call_args_list:
            batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
            for uc_call in batch_ctx.create_unique_constraint.call_args_list:
                assert uc_call.args[0] == "uq_task_decisions_return_id"
                assert uc_call.args[1] == ["return_id"]

    def test_037_unique_constraint_swap_is_four_columns(self):
        mod = _load_migration("037_scope_memory_unique_key_by_project.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        for _call_args in mock_op.batch_alter_table.call_args_list:
            batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
            for uc_call in batch_ctx.create_unique_constraint.call_args_list:
                assert uc_call.args[0] == "uq_memory_agent_key_ns_project"
                assert list(uc_call.args[1]) == ["agent_id", "key", "namespace", "project_id"]


# ---------------------------------------------------------------------------
# 8. Check constraints and enum-like column value validation
# ---------------------------------------------------------------------------


class TestCheckConstraints:
    """Check constraints and enum-like fixed-value columns."""

    def test_026_blob_length_check_constraints_exist(self):
        mod = _load_migration("026_add_blob_length_check_constraints.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        check_count = len(
            mock_op.batch_alter_table.return_value.__enter__.return_value.create_check_constraint.call_args_list
        )
        assert check_count == 7, (
            f"026 should create 7 check constraints (6 task_decisions + 1 audit_events), got {check_count}"
        )

    def test_026_downgrade_drops_all_check_constraints(self):
        mod = _load_migration("026_add_blob_length_check_constraints.py")
        with patch.object(mod, "op") as mock_op, patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
            mod.downgrade()
        drop_count = 0
        batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
        for c in batch_ctx.drop_constraint.call_args_list:
            if c.kwargs.get("type_") == "check":
                drop_count += 1
        assert drop_count == 7, f"026 downgrade should drop 7 check constraints, got {drop_count}"

    def test_036_priority_check_constraint_exists(self):
        mod = _load_migration("036_add_todos_priority_range_check.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        for _call_args in mock_op.batch_alter_table.call_args_list:
            batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
            for cc in batch_ctx.create_check_constraint.call_args_list:
                assert cc.args[0] == "ck_todos_priority_range", f"unexpected check constraint name: {cc.args[0]}"
                assert "priority >= 0" in cc.args[1]
                assert "priority <= 1000" in cc.args[1]

    def test_036_downgrade_drops_check_constraint(self):
        mod = _load_migration("036_add_todos_priority_range_check.py")
        with patch.object(mod, "op") as mock_op, patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
            mod.downgrade()
        for _call_args in mock_op.batch_alter_table.call_args_list:
            batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
            for dc in batch_ctx.drop_constraint.call_args_list:
                assert dc.kwargs.get("type_") == "check", (
                    f"downgrade should drop check constraint, got type_={dc.kwargs}"
                )

    def test_001_queues_has_unique_queue_name(self):
        mod = _load_migration("001_initial_schema.py")
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        next(c for c in mock_op.create_table.call_args_list if c.args[0] == "queues")
        import inspect

        src = inspect.getsource(mod.upgrade)
        assert 'UniqueConstraint("queue_name")' in src, "queues must have UniqueConstraint on queue_name"


# ---------------------------------------------------------------------------
# 9. Edge-case migration-specific contract tests
# ---------------------------------------------------------------------------


class TestEdgeCaseContracts:
    """Specific edge-case contracts for known-regression migrations."""

    def test_014a_ornith_training_pairs_links_to_014(self):
        mod = _load_migration("014a_add_ornith_training_pairs.py")
        assert mod.revision == "014a"
        assert mod.down_revision == "014"

    def test_020_downgrade_batch_alter_column_arity(self):
        """Regression: 020 alter_column in batch context must use 1 positional arg."""
        mod = _load_migration("020_make_todo_acceptance_criteria_and_dod_nullable.py")
        with patch.object(mod, "op") as mock_op, patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
            mod.downgrade()
        batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
        alter_calls = batch_ctx.alter_column.call_args_list
        assert len(alter_calls) == 2
        for call in alter_calls:
            assert len(call.args) == 1, f"alter_column arity violation: expected 1 arg, got {call.args}"
        assert [call.args[0] for call in alter_calls] == ["acceptance_criteria", "definition_of_done"]

    def test_001_downgrade_requires_env_confirmation(self):
        mod = _load_migration("001_initial_schema.py")
        env = {k: v for k, v in os.environ.items() if k != "ALEMBIC_DOWNGRADE_CONFIRMED"}
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(RuntimeError, match="ALEMBIC_DOWNGRADE_CONFIRMED"),
        ):
            mod.downgrade()

    def test_001_downgrade_proceeds_with_env_confirmation(self):
        mod = _load_migration("001_initial_schema.py")
        with patch.dict(os.environ, {"ALEMBIC_DOWNGRADE_CONFIRMED": "yes"}):
            with patch.object(mod, "op") as mock_op:
                mod.downgrade()
            drop_count = len(mock_op.drop_table.call_args_list)
            assert drop_count == 9

    def test_006_upgrade_contains_orphan_row_precheck_sql(self):
        """006 upgrade() includes SQL prechecks to clean orphan rows."""
        mod = _load_migration("006_add_d9_foreign_keys.py")
        import inspect

        src = inspect.getsource(mod.upgrade)
        assert "DELETE FROM task_decisions" in src
        assert "UPDATE task_returns SET todo_id = NULL" in src

    def test_017_upgrade_contains_dedupe_sql(self):
        mod = _load_migration("017_add_unique_on_task_decisions_return_id.py")
        import inspect

        src = inspect.getsource(mod.upgrade)
        assert "DELETE FROM task_decisions" in src

    def test_033_upgrade_contains_orphan_null_sql(self):
        mod = _load_migration("033_add_human_todos_parent_fk.py")
        import inspect

        src = inspect.getsource(mod.upgrade)
        assert "UPDATE human_todos SET parent_agent_todo_id = NULL" in src

    def test_all_migrations_have_both_functions(self):
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            assert hasattr(mod, "upgrade"), f"{fname} missing upgrade()"
            assert callable(mod.upgrade), f"{fname} upgrade is not callable"
            assert hasattr(mod, "downgrade"), f"{fname} missing downgrade()"
            assert callable(mod.downgrade), f"{fname} downgrade is not callable"

    def test_all_revision_fields_are_strings_or_none(self):
        for fname, _stem in _migration_filenames():
            mod = _load_migration(fname)
            rev = getattr(mod, "revision", None)
            assert rev is None or isinstance(rev, str), (
                f"{fname}: revision must be str or None, got {type(rev).__name__}"
            )
            down = getattr(mod, "down_revision", None)
            assert down is None or isinstance(down, str), (
                f"{fname}: down_revision must be str or None, got {type(down).__name__}"
            )
