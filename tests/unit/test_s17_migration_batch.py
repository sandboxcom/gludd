"""S.17 -- Migration SQLite batch-wrapper compatibility tests (002-005)."""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any, ClassVar, cast
from unittest.mock import patch


def _load_migration(filename: str):
    src = pathlib.Path(__file__).parent.parent.parent / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"migration_{src.stem}", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


class TestMigration002Batch:
    """002 adds project_id columns + FKs — already uses batch_alter_table extensively."""

    _FILENAME = "002_add_projects_and_project_id.py"

    def test_revision_chain(self):
        mod = _load_migration(self._FILENAME)
        assert mod.revision == "002"
        assert mod.down_revision == "001"

    def test_upgrade_uses_batch_alter_table(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        batch_calls = mock_op.batch_alter_table.call_args_list
        assert len(batch_calls) >= 7, (
            f"002 should use batch_alter_table for FK constraints; got {len(batch_calls)} calls"
        )

    def test_downgrade_uses_batch_alter_table(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        batch_calls = mock_op.batch_alter_table.call_args_list
        assert len(batch_calls) >= 8, (
            f"002 downgrade should use batch_alter_table for constraint drops; "
            f"got {len(batch_calls)} calls"
        )

    def test_sqlite_compatible_no_bare_alter(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
            mod.downgrade()
        assert mock_op.alter_column.call_count == 0, (
            "002 should not use bare alter_column (not SQLite compatible)"
        )


class TestMigration003Batch:
    """003 adds plan_artifact column on todos — fixed to use batch_alter_table."""

    _FILENAME = "003_add_plan_artifact.py"

    def test_revision_chain(self):
        mod = _load_migration(self._FILENAME)
        assert mod.revision == "003"
        assert mod.down_revision == "002"

    def test_upgrade_uses_batch_alter_table(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        batch_calls = mock_op.batch_alter_table.call_args_list
        assert len(batch_calls) == 1, (
            f"003 upgrade should use 1 batch_alter_table for add_column; "
            f"got {len(batch_calls)}"
        )
        batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
        batch_ctx.add_column.assert_called_once()

    def test_downgrade_uses_batch_alter_table(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        batch_calls = mock_op.batch_alter_table.call_args_list
        assert len(batch_calls) == 1, (
            f"003 downgrade should use 1 batch_alter_table for drop_column; "
            f"got {len(batch_calls)}"
        )
        batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
        batch_ctx.drop_column.assert_called_once()

    def test_no_bare_add_column(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        assert mock_op.add_column.call_count == 0, (
            "003 should use batch_op.add_column, not bare op.add_column"
        )

    def test_no_bare_drop_column(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        assert mock_op.drop_column.call_count == 0, (
            "003 should use batch_op.drop_column, not bare op.drop_column"
        )

    def test_table_name_is_todos(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
            mod.downgrade()
        for call in mock_op.batch_alter_table.call_args_list:
            assert call.args[0] == "todos", (
                f"003 batch_alter_table should operate on 'todos'; got {call.args[0]}"
            )


class TestMigration004Batch:
    """004 creates prompt_profiles + benchmark_results — only native CREATE/DROP ops."""

    _FILENAME = "004_add_benchmark_tables.py"

    def test_revision_chain(self):
        mod = _load_migration(self._FILENAME)
        assert mod.revision == "004"
        assert mod.down_revision == "003"

    def test_upgrade_only_native_operations(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        assert mock_op.create_table.call_count == 2, (
            "004 should create 2 tables (prompt_profiles, benchmark_results)"
        )
        assert mock_op.create_index.call_count == 2, (
            "004 should create 2 indexes"
        )
        assert mock_op.alter_column.call_count == 0, (
            "004 should not use bare alter_column"
        )
        assert mock_op.add_column.call_count == 0, (
            "004 should not use bare add_column"
        )
        assert mock_op.drop_column.call_count == 0, (
            "004 should not use bare drop_column"
        )

    def test_downgrade_only_native_operations(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        assert mock_op.drop_index.call_count == 2, (
            "004 downgrade should drop 2 indexes"
        )
        assert mock_op.drop_table.call_count == 2, (
            "004 downgrade should drop 2 tables"
        )
        assert mock_op.alter_column.call_count == 0
        assert mock_op.add_column.call_count == 0
        assert mock_op.drop_column.call_count == 0

    def test_no_alter_operations_in_source(self):
        src = (
            pathlib.Path(__file__).parent.parent.parent
            / "alembic" / "versions" / "004_add_benchmark_tables.py"
        )
        text = src.read_text()
        assert "add_column(" not in text, "004 source should not contain add_column"
        assert "drop_column(" not in text, "004 source should not contain drop_column"
        assert "alter_column(" not in text, "004 source should not contain alter_column"


class TestMigration005Batch:
    """005 adds runtime tables and ALTERs prompt_profiles — uses batch_alter_table."""

    _FILENAME = "005_add_runtime_tables.py"

    def test_revision_chain(self):
        mod = _load_migration(self._FILENAME)
        assert mod.revision == "005"
        assert mod.down_revision == "004"

    def test_upgrade_batch_for_alter(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        assert mock_op.create_table.call_count == 5, (
            "005 should create 5 tables"
        )
        batch_calls = mock_op.batch_alter_table.call_args_list
        assert len(batch_calls) == 1, (
            f"005 should use batch_alter_table for prompt_profiles ALTER; "
            f"got {len(batch_calls)} calls"
        )
        first_args = batch_calls[0].args
        assert first_args[0] == "prompt_profiles", (
            f"005 batch_alter_table should target prompt_profiles; got {first_args[0]}"
        )
        batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
        batch_ctx.add_column.assert_called_once()

    def test_downgrade_batch_for_alter(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        batch_calls = mock_op.batch_alter_table.call_args_list
        assert len(batch_calls) == 1, (
            f"005 downgrade should use batch_alter_table for prompt_profiles ALTER; "
            f"got {len(batch_calls)} calls"
        )
        batch_ctx = mock_op.batch_alter_table.return_value.__enter__.return_value
        batch_ctx.drop_column.assert_called_once()

    def test_no_bare_alter_column(self):
        mod = _load_migration(self._FILENAME)
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
            mod.downgrade()
        assert mock_op.alter_column.call_count == 0, (
            "005 should not use bare alter_column"
        )


class TestMigration002to005ChainContinuity:
    """Verify the revision chain is unbroken 002→003→004→005."""

    _EXPECTED_CHAIN: ClassVar[dict[str, tuple[str, str]]] = {
        "002_add_projects_and_project_id.py": ("002", "001"),
        "003_add_plan_artifact.py": ("003", "002"),
        "004_add_benchmark_tables.py": ("004", "003"),
        "005_add_runtime_tables.py": ("005", "004"),
    }

    def test_chain_links(self):
        for filename, (rev, down) in self._EXPECTED_CHAIN.items():
            mod = _load_migration(filename)
            assert mod.revision == rev, f"{filename}: expected revision {rev}, got {mod.revision}"
            assert mod.down_revision == down, (
                f"{filename}: expected down_revision {down}, got {mod.down_revision}"
            )
