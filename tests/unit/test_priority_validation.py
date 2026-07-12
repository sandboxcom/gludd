"""H.14: priority upper-bound validation at schema, repository, and self-improve layers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.routers.self_improve import _MAX_PRIORITY, _coerce_priority
from general_ludd.schemas.task_definition import TaskDefinition
from general_ludd.schemas.todo import Todo


class TestTodoPriorityUpperBound:
    def test_reject_above_ceiling(self):
        with pytest.raises(ValidationError, match="priority must not exceed"):
            Todo(title="x", priority=1001)

    def test_reject_far_above_ceiling(self):
        with pytest.raises(ValidationError, match="priority must not exceed"):
            Todo(title="x", priority=999_999)

    def test_accept_at_ceiling(self):
        todo = Todo(title="x", priority=_MAX_PRIORITY)
        assert todo.priority == _MAX_PRIORITY

    def test_accept_within_range(self):
        todo = Todo(title="x", priority=500)
        assert todo.priority == 500

    def test_accept_zero(self):
        todo = Todo(title="x", priority=0)
        assert todo.priority == 0

    def test_accept_one(self):
        todo = Todo(title="x", priority=1)
        assert todo.priority == 1

    def test_reject_negative(self):
        with pytest.raises(ValidationError, match="priority must be non-negative"):
            Todo(title="x", priority=-1)

    def test_negative_one_rejected(self):
        with pytest.raises(ValidationError, match="priority must be non-negative"):
            Todo(title="x", priority=-1)

    def test_ceiling_plus_one_rejected(self):
        with pytest.raises(ValidationError, match="priority must not exceed"):
            Todo(title="x", priority=_MAX_PRIORITY + 1)


class TestTaskDefinitionPriorityUpperBound:
    def test_reject_above_ceiling(self):
        with pytest.raises(ValidationError, match="priority must not exceed"):
            TaskDefinition(name="x", priority=1001)

    def test_accept_at_ceiling(self):
        td = TaskDefinition(name="x", priority=_MAX_PRIORITY)
        assert td.priority == _MAX_PRIORITY

    def test_accept_within_range(self):
        td = TaskDefinition(name="x", priority=42)
        assert td.priority == 42

    def test_reject_negative(self):
        with pytest.raises(ValidationError, match="priority must be non-negative"):
            TaskDefinition(name="x", priority=-1)

    def test_to_todo_preserves_priority(self):
        td = TaskDefinition(name="x", priority=77)
        todo = td.to_todo()
        assert todo.priority == 77


class TestCoercePriorityClamping:
    def test_raw_int_above_ceiling_is_clamped(self):
        assert _coerce_priority(9999) == _MAX_PRIORITY

    def test_raw_int_within_range_passes_through(self):
        assert _coerce_priority(42) == 42

    def test_raw_int_at_ceiling_passes_through(self):
        assert _coerce_priority(_MAX_PRIORITY) == _MAX_PRIORITY

    def test_negative_raw_int_passes_through_unclamped(self):
        assert _coerce_priority(-5) == -5

    def test_label_high_is_within_range(self):
        assert _coerce_priority("high") == 10

    def test_label_critical_is_within_range(self):
        assert _coerce_priority("critical") == 20

    def test_bool_is_treated_as_unset(self):
        assert _coerce_priority(True) == 5

    def test_unknown_label_defaults_to_medium(self):
        assert _coerce_priority("supernova") == 5

    def test_zero_passes_through(self):
        assert _coerce_priority(0) == 0
