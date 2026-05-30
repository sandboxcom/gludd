"""Unit tests for todo state machine."""

import pytest

from agentic_harness.schemas.todo import (
    ResourceProfile,
    RiskLevel,
    Todo,
    TodoStatus,
    WorkType,
    validate_transition,
)


class TestTodoStateMachine:
    def test_valid_transition_backlog_to_queued(self):
        assert validate_transition(TodoStatus.BACKLOG, TodoStatus.QUEUED) is True

    def test_valid_transition_queued_to_active(self):
        assert validate_transition(TodoStatus.QUEUED, TodoStatus.ACTIVE) is True

    def test_valid_transition_active_to_awaiting_result(self):
        assert validate_transition(TodoStatus.ACTIVE, TodoStatus.AWAITING_RESULT) is True

    def test_valid_transition_reviewing_return_to_complete(self):
        assert validate_transition(TodoStatus.REVIEWING_RETURN, TodoStatus.COMPLETE) is True

    def test_valid_transition_reviewing_return_to_needs_more_work(self):
        assert validate_transition(TodoStatus.REVIEWING_RETURN, TodoStatus.NEEDS_MORE_WORK) is True

    def test_invalid_transition_backlog_to_complete(self):
        assert validate_transition(TodoStatus.BACKLOG, TodoStatus.COMPLETE) is False

    def test_invalid_transition_complete_to_active(self):
        assert validate_transition(TodoStatus.COMPLETE, TodoStatus.ACTIVE) is False

    def test_invalid_transition_cancelled_to_queued(self):
        assert validate_transition(TodoStatus.CANCELLED, TodoStatus.QUEUED) is False

    def test_todo_transition_method_valid(self):
        todo = Todo(title="test", status=TodoStatus.BACKLOG)
        todo.transition_to(TodoStatus.QUEUED)
        assert todo.status == TodoStatus.QUEUED

    def test_todo_transition_method_invalid(self):
        todo = Todo(title="test", status=TodoStatus.BACKLOG)
        with pytest.raises(ValueError, match="Invalid transition"):
            todo.transition_to(TodoStatus.COMPLETE)

    def test_todo_sets_completed_at_on_complete(self):
        todo = Todo(title="test", status=TodoStatus.REVIEWING_RETURN)
        todo.transition_to(TodoStatus.COMPLETE)
        assert todo.completed_at is not None

    def test_todo_no_completed_at_when_not_complete(self):
        todo = Todo(title="test", status=TodoStatus.BACKLOG)
        assert todo.completed_at is None

    def test_todo_default_fields(self):
        todo = Todo(title="test")
        assert todo.status == TodoStatus.BACKLOG
        assert todo.work_type == WorkType.UNKNOWN
        assert todo.risk_level == RiskLevel.LOW
        assert todo.resource_profile == ResourceProfile.LOW_RESOURCE
        assert todo.version == 1
        assert todo.created_by == "agent"
        assert todo.tags == []
        assert todo.child_todo_ids == []

    def test_todo_custom_fields(self):
        todo = Todo(
            title="implement auth",
            description="Add authentication",
            status=TodoStatus.QUEUED,
            priority=5,
            queue="core",
            tags=["auth", "backend"],
            risk_level=RiskLevel.HIGH,
            work_type=WorkType.CODE,
            resource_profile=ResourceProfile.HYBRID,
        )
        assert todo.title == "implement auth"
        assert todo.priority == 5
        assert todo.risk_level == RiskLevel.HIGH
