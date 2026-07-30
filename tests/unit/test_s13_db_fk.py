"""S.13: Verify missing FKs on todo_id and return_id references.
Covers: TodoModel.parent_todo_id, TaskDecisionModel.matched_todo_id,
HumanTodoModel.parent_agent_todo_id (new migration 033), plus
regression guards for existing 006 FKs."""

from __future__ import annotations

import pytest
from sqlalchemy import ForeignKey, create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from general_ludd.db.models import (
    Base,
    HumanTodoModel,
    TaskDecisionModel,
    TaskReturnModel,
    TodoEventModel,
    TodoModel,
)


@pytest.fixture
def engine():
    e = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(e, "connect")
    def _fk_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(e)
    try:
        yield e
    finally:
        e.dispose()


@pytest.fixture
def db_session(engine):
    with Session(engine) as session:
        yield session


def _col_reflects_fk(model, col_name: str) -> ForeignKey | None:
    col = getattr(model.__table__.c, col_name, None)
    if col is None:
        return None
    for fk in col.foreign_keys:
        return fk
    return None


# ── Model-layer structural tests ────────────────────────────────────────────


def test_todo_parent_todo_id_has_fk():
    fk = _col_reflects_fk(TodoModel, "parent_todo_id")
    assert fk is not None, "TodoModel.parent_todo_id missing ForeignKey"
    assert str(fk.column) == "todos.todo_id"
    assert fk.ondelete == "SET NULL"


def test_task_decision_matched_todo_id_has_fk():
    fk = _col_reflects_fk(TaskDecisionModel, "matched_todo_id")
    assert fk is not None, "TaskDecisionModel.matched_todo_id missing ForeignKey"
    assert str(fk.column) == "todos.todo_id"
    assert fk.ondelete == "SET NULL"


# ── Runtime integrity tests ─────────────────────────────────────────────────


def test_parent_todo_id_fk_blocks_orphan_insert(db_session):
    session = db_session
    todo = TodoModel(
        todo_id="S13-001",
        title="orphan test",
        parent_todo_id="NONEXISTENT",
    )
    session.add(todo)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_parent_todo_id_set_null_on_parent_delete(db_session):
    session = db_session
    parent = TodoModel(todo_id="S13-PARENT", title="parent")
    child = TodoModel(todo_id="S13-CHILD", title="child", parent_todo_id="S13-PARENT")
    session.add_all([parent, child])
    session.flush()
    assert child.parent_todo_id == "S13-PARENT"

    session.delete(parent)
    session.flush()
    session.expire(child)
    assert child.parent_todo_id is None


def test_matched_todo_id_fk_blocks_orphan_insert(db_session):
    session = db_session
    parent_ret = TaskReturnModel(
        return_id="S13-RET-001",
        job_id="job-1",
        playbook="test.yml",
        queue="core",
    )
    session.add(parent_ret)
    session.flush()

    decision = TaskDecisionModel(
        return_id="S13-RET-001",
        decision="approve",
        matched_todo_id="NONEXISTENT",
    )
    session.add(decision)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_matched_todo_id_set_null_on_todo_delete(db_session):
    session = db_session
    todo = TodoModel(todo_id="S13-MATCH", title="matched")
    parent_ret = TaskReturnModel(
        return_id="S13-RET-002",
        job_id="job-2",
        playbook="test.yml",
        queue="core",
    )
    session.add_all([todo, parent_ret])
    session.flush()

    decision = TaskDecisionModel(
        return_id="S13-RET-002",
        decision="approve",
        matched_todo_id="S13-MATCH",
    )
    session.add(decision)
    session.flush()
    assert decision.matched_todo_id == "S13-MATCH"

    session.delete(todo)
    session.flush()
    session.expire(decision)
    assert decision.matched_todo_id is None


# ── HumanTodoModel.parent_agent_todo_id FK (migration 033) ────────────────────


def test_human_todo_parent_agent_todo_id_has_fk():
    fk = _col_reflects_fk(HumanTodoModel, "parent_agent_todo_id")
    assert fk is not None, "HumanTodoModel.parent_agent_todo_id missing ForeignKey"
    assert str(fk.column) == "todos.todo_id"
    assert fk.ondelete == "SET NULL"


def test_human_todo_parent_fk_blocks_orphan_insert(db_session):
    session = db_session
    ht = HumanTodoModel(
        id="HT-S13-001",
        title="orphan test",
        agent_id="agent-1",
        category="permission_escalation",
        parent_agent_todo_id="NONEXISTENT",
    )
    session.add(ht)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_human_todo_parent_fk_set_null_on_todo_delete(db_session):
    session = db_session
    todo = TodoModel(todo_id="S13-HT-PARENT", title="blocked parent")
    session.add(todo)
    session.flush()

    ht = HumanTodoModel(
        id="HT-S13-002",
        title="needs human",
        agent_id="agent-1",
        category="permission_escalation",
        parent_agent_todo_id="S13-HT-PARENT",
    )
    session.add(ht)
    session.flush()
    assert ht.parent_agent_todo_id == "S13-HT-PARENT"

    session.delete(todo)
    session.flush()
    session.expire(ht)
    assert ht.parent_agent_todo_id is None


# ── Existing FKs still present (regression guard) ───────────────────────────


def test_existing_fk_task_returns_todo_id():
    fk = _col_reflects_fk(TaskReturnModel, "todo_id")
    assert fk is not None
    assert str(fk.column) == "todos.todo_id"
    assert fk.ondelete == "SET NULL"


def test_existing_fk_task_decisions_return_id():
    fk = _col_reflects_fk(TaskDecisionModel, "return_id")
    assert fk is not None
    assert str(fk.column) == "task_returns.return_id"
    assert fk.ondelete == "CASCADE"


def test_existing_fk_todo_events_todo_id():
    fk = _col_reflects_fk(TodoEventModel, "todo_id")
    assert fk is not None
    assert str(fk.column) == "todos.todo_id"
    assert fk.ondelete == "CASCADE"
