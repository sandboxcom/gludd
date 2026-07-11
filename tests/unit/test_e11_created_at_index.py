"""E11 (PERF-2): ``task_decisions.created_at`` index tests.

Migration 025 adds ``ix_task_decisions_created_at`` and the ORM model
declares it in ``__table_args__`` so ``Base.metadata.create_all`` also
gets the index. These tests verify both layers.
"""

from typing import Any, cast
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy import create_engine

from general_ludd.db.models import Base
from general_ludd.db.models import TaskDecisionModel


_IX_NAME = "ix_task_decisions_created_at"


def _load_migration_025():
    import importlib.util
    import pathlib

    src = (
        pathlib.Path(__file__).parent.parent.parent
        / "alembic"
        / "versions"
        / "025_add_task_decisions_and_todos_perf_indexes.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_025_add_task_decisions_and_todos_perf_indexes", src
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


class TestTaskDecisionModelCreatedAtIndex:
    """ORM layer: ``TaskDecisionModel.__table_args__`` declares the index."""

    def test___table_args___includes_created_at_index(self):
        indexes = {idx.name: idx for idx in TaskDecisionModel.__table_args__ if hasattr(idx, "name")}
        assert _IX_NAME in indexes, (
            f"TaskDecisionModel.__table_args__ must include {_IX_NAME}; "
            f"found: {sorted(indexes)}"
        )

    def test_created_at_indexed_via_table_meta(self):
        table = TaskDecisionModel.__table__
        idx_names = {idx.name for idx in table.indexes}
        assert _IX_NAME in idx_names, (
            f"{_IX_NAME} must appear in table.indexes; found: {sorted(idx_names)}"
        )


class TestMigration025CreatedAtIndex:
    """Alembic layer: migration 025 creates ``ix_task_decisions_created_at``."""

    def test_revision_links_to_024(self):
        mod = _load_migration_025()
        assert mod.revision == "025"
        assert mod.down_revision == "024"

    def test_upgrade_creates_index(self):
        mod = _load_migration_025()
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        create_calls = [c.args[0] for c in mock_op.create_index.call_args_list]
        assert _IX_NAME in create_calls, (
            f"upgrade() did not create {_IX_NAME}; "
            f"create_index calls={create_calls}"
        )

    def test_upgrade_index_on_correct_table_and_column(self):
        mod = _load_migration_025()
        with patch.object(mod, "op") as mock_op:
            mod.upgrade()
        idx_call = next(
            c
            for c in mock_op.create_index.call_args_list
            if c.args[0] == _IX_NAME
        )
        assert idx_call.kwargs.get("table_name") == "task_decisions" or (
            len(idx_call.args) > 2 and idx_call.args[1] == "task_decisions"
        )
        assert "created_at" in idx_call.args[2], (
            f"index columns={idx_call.args[2]} does not include created_at"
        )

    def test_downgrade_drops_index(self):
        mod = _load_migration_025()
        with patch.object(mod, "op") as mock_op:
            mod.downgrade()
        drop_calls = [c.args[0] for c in mock_op.drop_index.call_args_list]
        assert _IX_NAME in drop_calls, (
            f"downgrade() did not drop {_IX_NAME}; "
            f"drop_index calls={drop_calls}"
        )


class TestCreatedAtIndexQueryPlan:
    """Integration: EXPLAIN QUERY PLAN confirms the index is used."""

    def test_order_by_created_at_desc_uses_index(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            plan_rows = conn.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT * FROM task_decisions "
                    "ORDER BY created_at DESC LIMIT 50"
                )
            ).fetchall()
        engine.dispose()

        plan_text = "\n".join(str(row) for row in plan_rows)
        assert not any("SCAN TABLE" in str(row) for row in plan_rows), (
            "EXPLAIN QUERY PLAN shows full table scan instead of index use:\n"
            + plan_text
        )
        assert any(
            "USING INDEX" in str(row) or "USING COVERING INDEX" in str(row)
            for row in plan_rows
        ), (
            "EXPLAIN QUERY PLAN does not show index use:\n" + plan_text
        )
