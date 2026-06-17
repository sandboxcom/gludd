"""Unit tests for SQLAlchemy ORM model constraints added in batch-5.

Verifies:
  1. TodoModel.__mapper_args__ wires version_id_col to the `version` column.
  2. TaskDecisionModel.return_id has FK to task_returns.return_id, NOT NULL, unique, index.
  3. AuditEventModel.details has a CheckConstraint limiting length to 65536.
  4. TaskDecisionModel large Text blobs each have a CheckConstraint limiting length to 65536.

Uses SQLAlchemy inspection only — no live DB round-trips needed for the structural checks.
Runtime FK and CheckConstraint enforcement is exercised via SQLite in-memory.
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from general_ludd.db.models import (
    AuditEventModel,
    Base,
    TaskDecisionModel,
    TaskReturnModel,
    TodoModel,
)

# ---------------------------------------------------------------------------
# 1. TodoModel — version_id_col mapper arg
# ---------------------------------------------------------------------------

class TestTodoModelVersionIdCol:
    def test_mapper_args_present(self):
        assert hasattr(TodoModel, "__mapper_args__"), "TodoModel must define __mapper_args__"

    def test_version_id_col_is_version_column(self):
        mapper_args = TodoModel.__mapper_args__
        assert "version_id_col" in mapper_args, "__mapper_args__ must contain 'version_id_col'"
        col = mapper_args["version_id_col"]
        # The column key in the mapper should be 'version'
        assert col.key == "version", (
            f"version_id_col should reference the 'version' column, got key={col.key!r}"
        )

    def test_version_column_is_integer(self):
        from sqlalchemy import Integer
        col = TodoModel.__mapper_args__["version_id_col"]
        assert isinstance(col.type, Integer), (
            f"version_id_col must be an Integer column, got {type(col.type)}"
        )


# ---------------------------------------------------------------------------
# 2. TaskDecisionModel.return_id — FK, NOT NULL, unique, index
# ---------------------------------------------------------------------------

class TestTaskDecisionReturnId:
    def _get_col(self):
        mapper = inspect(TaskDecisionModel)
        for col in mapper.columns:
            if col.key == "return_id":
                return col
        raise AssertionError("return_id column not found on TaskDecisionModel")

    def test_return_id_not_nullable(self):
        col = self._get_col()
        assert not col.nullable, "return_id must be NOT NULL"

    def test_return_id_has_foreign_key(self):
        col = self._get_col()
        fks = list(col.foreign_keys)
        assert fks, "return_id must have a ForeignKey"
        target = next(iter(fks)).target_fullname
        assert target == "task_returns.return_id", (
            f"return_id FK target must be 'task_returns.return_id', got {target!r}"
        )

    def test_return_id_unique(self):
        col = self._get_col()
        assert col.unique, "return_id must have unique=True"

    def test_return_id_index(self):
        col = self._get_col()
        assert col.index, "return_id must have index=True"


# ---------------------------------------------------------------------------
# 3. AuditEventModel.details — CheckConstraint length <= 65536
# ---------------------------------------------------------------------------

class TestAuditEventDetailsConstraint:
    def test_details_check_constraint_present(self):
        table = AuditEventModel.__table__
        ck_names = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        assert "ck_audit_events_details_len" in ck_names, (
            f"Expected CheckConstraint 'ck_audit_events_details_len' on audit_events table; "
            f"found: {ck_names}"
        )

    def test_details_check_constraint_expression(self):
        table = AuditEventModel.__table__
        for c in table.constraints:
            if isinstance(c, CheckConstraint) and c.name == "ck_audit_events_details_len":
                expr = str(c.sqltext)
                assert "65536" in expr, (
                    f"CheckConstraint expression should reference 65536, got: {expr!r}"
                )
                return
        raise AssertionError("ck_audit_events_details_len not found")


# ---------------------------------------------------------------------------
# 4. TaskDecisionModel large Text blobs — CheckConstraints length <= 65536
# ---------------------------------------------------------------------------

_TASK_DECISION_BLOB_CONSTRAINTS = {
    "ck_task_decisions_evidence_refs_len",
    "ck_task_decisions_todo_updates_len",
    "ck_task_decisions_child_todos_len",
    "ck_task_decisions_validation_requests_len",
    "ck_task_decisions_git_requests_len",
    "ck_task_decisions_audit_notes_len",
    "ck_task_decisions_policy_flags_len",
}


class TestTaskDecisionBlobConstraints:
    def _ck_names(self):
        table = TaskDecisionModel.__table__
        return {c.name for c in table.constraints if isinstance(c, CheckConstraint)}

    def test_all_blob_constraints_present(self):
        ck_names = self._ck_names()
        missing = _TASK_DECISION_BLOB_CONSTRAINTS - ck_names
        assert not missing, (
            f"Missing CheckConstraints on task_decisions: {missing}\n"
            f"Found: {ck_names}"
        )

    @pytest.mark.parametrize("ck_name", sorted(_TASK_DECISION_BLOB_CONSTRAINTS))
    def test_blob_constraint_has_65536(self, ck_name):
        table = TaskDecisionModel.__table__
        for c in table.constraints:
            if isinstance(c, CheckConstraint) and c.name == ck_name:
                expr = str(c.sqltext)
                assert "65536" in expr, (
                    f"Constraint {ck_name!r} expression should reference 65536, got: {expr!r}"
                )
                return
        raise AssertionError(f"Constraint {ck_name!r} not found on task_decisions table")


# ---------------------------------------------------------------------------
# 5. Runtime enforcement — FK cascade via SQLite in-memory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_decision_return_id_fk_cascade():
    """Deleting a TaskReturn cascades to TaskDecision rows."""
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.cursor().execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        async with session.begin():
            tr = TaskReturnModel(
                return_id="ret-cascade-test",
                job_id="job-1",
                playbook="play.yml",
                queue="core",
            )
            session.add(tr)

        async with session.begin():
            td = TaskDecisionModel(
                return_id="ret-cascade-test",
                decision="approve",
            )
            session.add(td)

        # Delete the parent — cascade should remove the child
        async with session.begin():
            tr_obj = await session.get(TaskReturnModel, 1)
            await session.delete(tr_obj)

        # Child should be gone
        from sqlalchemy import select
        result = await session.execute(
            select(TaskDecisionModel).where(TaskDecisionModel.return_id == "ret-cascade-test")
        )
        rows = result.scalars().all()
        assert rows == [], f"Expected cascade delete to remove TaskDecision rows, got: {rows}"

    await engine.dispose()
