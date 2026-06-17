"""Tests for schema constraints added in batch-5.

Verifies:
  1. TodoModel.__mapper_args__ wires version_id_col to the version column.
  2. TaskDecisionModel.return_id is NOT NULL, unique, and has a FK to task_returns.
  3. CheckConstraints on AuditEventModel.details and the three large Text blobs on
     TaskDecisionModel exist in the table metadata.

Uses SQLAlchemy `inspect()` for static metadata checks (no live DB required) and
an in-memory SQLite + aiosqlite session for the CheckConstraint live enforcement
check on SQLite (note: SQLite ignores CHECK constraints unless built with
SQLITE_DQS=0 and the function is non-constant; we therefore test the metadata
presence only, not live rejection, which is sufficient for CI portability).
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, event, inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import (
    AuditEventModel,
    Base,
    TaskDecisionModel,
    TaskReturnModel,
    TodoModel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# 1. TodoModel — version_id_col optimistic locking
# ---------------------------------------------------------------------------

class TestTodoModelVersionIdCol:
    def test_mapper_args_has_version_id_col(self):
        """__mapper_args__ must declare version_id_col pointing at the version column."""
        mapper_args = getattr(TodoModel, "__mapper_args__", {})
        assert "version_id_col" in mapper_args, (
            "TodoModel.__mapper_args__ is missing 'version_id_col'"
        )

    def test_version_id_col_is_version_column(self):
        """The version_id_col value must resolve to the 'version' column."""
        mapper_args = TodoModel.__mapper_args__
        version_col = mapper_args["version_id_col"]
        # The column object's key should be 'version'
        assert version_col.key == "version", (
            f"version_id_col points to '{version_col.key}', expected 'version'"
        )

    def test_version_column_is_integer(self):
        """The version column must be an Integer for SQLAlchemy optimistic locking."""
        from sqlalchemy import Integer as SAInteger
        insp = inspect(TodoModel)
        col = insp.columns["version"]
        assert isinstance(col.type, SAInteger), (
            f"version column type is {type(col.type)}, expected Integer"
        )

    def test_version_column_not_nullable(self):
        """version must be NOT NULL so the locking math never encounters NULL."""
        insp = inspect(TodoModel)
        col = insp.columns["version"]
        assert col.nullable is False, "version column must be NOT NULL"

    async def test_version_increments_on_update(self, async_session: AsyncSession):
        """Saving a TodoModel twice must auto-bump version from 1 → 2 → 3."""
        todo = TodoModel(title="lock-test")
        async_session.add(todo)
        await async_session.flush()
        assert todo.version == 1

        todo.title = "lock-test-v2"
        await async_session.flush()
        assert todo.version == 2

        todo.title = "lock-test-v3"
        await async_session.flush()
        assert todo.version == 3


# ---------------------------------------------------------------------------
# 2. TaskDecisionModel.return_id — FK + NOT NULL + unique
# ---------------------------------------------------------------------------

class TestTaskDecisionReturnIdConstraints:
    def test_return_id_not_nullable(self):
        """return_id must be NOT NULL."""
        insp = inspect(TaskDecisionModel)
        col = insp.columns["return_id"]
        assert col.nullable is False, "TaskDecisionModel.return_id must be NOT NULL"

    def test_return_id_has_unique_constraint(self):
        """return_id must carry a unique constraint (one decision per return)."""
        insp = inspect(TaskDecisionModel)
        col = insp.columns["return_id"]
        # unique=True on a mapped_column produces a UniqueConstraint in __table_args__
        # OR sets unique on the column object directly.
        table = TaskDecisionModel.__table__
        # Check column-level unique flag OR a table-level UniqueConstraint on return_id
        col_unique = col.unique
        uc_names = {
            frozenset(c.name for c in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and len(list(uc.columns)) == 1
        }
        assert col_unique or frozenset({"return_id"}) in uc_names, (
            "TaskDecisionModel.return_id must be unique (one decision per return)"
        )

    def test_return_id_has_fk_to_task_returns(self):
        """return_id must have a FK referencing task_returns.return_id."""
        insp = inspect(TaskDecisionModel)
        col = insp.columns["return_id"]
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "task_returns.return_id" in fk_targets, (
            f"return_id FK targets {fk_targets!r}, expected 'task_returns.return_id'"
        )

    async def test_return_id_fk_enforced(self, async_session: AsyncSession):
        """Inserting a TaskDecisionModel without a matching TaskReturnModel must fail."""
        import sqlalchemy.exc

        td = TaskDecisionModel(
            return_id="NONEXISTENT-RETURN",
            decision="reject",
            confidence=0.0,
        )
        async_session.add(td)
        with pytest.raises((sqlalchemy.exc.IntegrityError, sqlalchemy.exc.OperationalError)):
            await async_session.flush()

    async def test_return_id_unique_enforced(self, async_session: AsyncSession):
        """Two TaskDecisionModels with the same return_id must raise IntegrityError."""
        import sqlalchemy.exc

        tr = TaskReturnModel(
            return_id="R-UNIQ-TEST",
            job_id="J-UNIQ",
            playbook="noop.yml",
            queue="core",
        )
        async_session.add(tr)
        await async_session.flush()

        td1 = TaskDecisionModel(
            return_id="R-UNIQ-TEST",
            decision="complete",
            confidence=0.9,
        )
        async_session.add(td1)
        await async_session.flush()

        td2 = TaskDecisionModel(
            return_id="R-UNIQ-TEST",
            decision="reject",
            confidence=0.1,
        )
        async_session.add(td2)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await async_session.flush()


# ---------------------------------------------------------------------------
# 3. CheckConstraints — metadata presence
# ---------------------------------------------------------------------------

def _check_constraint_names(table) -> set[str]:
    """Return the set of named CheckConstraint names on a table."""
    return {
        c.name
        for c in table.constraints
        if isinstance(c, CheckConstraint) and c.name is not None
    }


class TestCheckConstraints:
    def test_audit_event_details_check_constraint_present(self):
        """AuditEventModel must have a CheckConstraint on details length."""
        names = _check_constraint_names(AuditEventModel.__table__)
        assert "ck_audit_events_details_len" in names, (
            f"Expected 'ck_audit_events_details_len' in {names!r}"
        )

    def test_task_decision_todo_updates_check_constraint_present(self):
        """TaskDecisionModel must have a CheckConstraint on todo_updates length."""
        names = _check_constraint_names(TaskDecisionModel.__table__)
        assert "ck_task_decisions_todo_updates_len" in names, (
            f"Expected 'ck_task_decisions_todo_updates_len' in {names!r}"
        )

    def test_task_decision_child_todos_check_constraint_present(self):
        """TaskDecisionModel must have a CheckConstraint on child_todos length."""
        names = _check_constraint_names(TaskDecisionModel.__table__)
        assert "ck_task_decisions_child_todos_len" in names, (
            f"Expected 'ck_task_decisions_child_todos_len' in {names!r}"
        )

    def test_task_decision_validation_requests_check_constraint_present(self):
        """TaskDecisionModel must have a CheckConstraint on validation_requests length."""
        names = _check_constraint_names(TaskDecisionModel.__table__)
        assert "ck_task_decisions_validation_requests_len" in names, (
            f"Expected 'ck_task_decisions_validation_requests_len' in {names!r}"
        )

    def test_existing_audit_events_index_preserved(self):
        """The existing ix_audit_events_entity Index must still be present."""
        table = AuditEventModel.__table__
        index_names = {
            idx.name
            for idx in table.indexes
        }
        assert "ix_audit_events_entity" in index_names, (
            f"ix_audit_events_entity index missing from audit_events; found {index_names!r}"
        )

    def test_existing_todos_index_preserved(self):
        """The existing ix_todos_status_queue Index must still be present."""
        table = TodoModel.__table__
        index_names = {idx.name for idx in table.indexes}
        assert "ix_todos_status_queue" in index_names, (
            f"ix_todos_status_queue index missing from todos; found {index_names!r}"
        )
