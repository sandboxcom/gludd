"""H.14: priority upper/lower-bound validation at the repository layer.

Schema-layer tests are in test_priority_validation.py.
This file tests TodoRepository._validate_create_data directly.
"""

from __future__ import annotations

import pytest

from general_ludd.db.repository import _MAX_PRIORITY, _MIN_PRIORITY, TodoRepository


class TestRepoPriorityUpperBound:
    """Values above _MAX_PRIORITY are clamped to _MAX_PRIORITY."""

    def test_clamp_above_ceiling(self):
        data = {"title": "x", "priority": 2000}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == _MAX_PRIORITY

    def test_clamp_far_above_ceiling(self):
        data = {"title": "x", "priority": 999_999}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == _MAX_PRIORITY

    def test_clamp_ceiling_plus_one(self):
        data = {"title": "x", "priority": _MAX_PRIORITY + 1}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == _MAX_PRIORITY

    def test_accept_at_ceiling(self):
        data = {"title": "x", "priority": _MAX_PRIORITY}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == _MAX_PRIORITY

    def test_accept_within_range(self):
        data = {"title": "x", "priority": 500}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == 500


class TestRepoPriorityLowerBound:
    """Values below _MIN_PRIORITY are clamped to _MIN_PRIORITY."""

    def test_clamp_negative(self):
        data = {"title": "x", "priority": -1}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == _MIN_PRIORITY

    def test_clamp_large_negative(self):
        data = {"title": "x", "priority": -9999}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == _MIN_PRIORITY

    def test_clamp_minus_one(self):
        data = {"title": "x", "priority": -1}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == _MIN_PRIORITY

    def test_accept_zero(self):
        data = {"title": "x", "priority": 0}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == 0

    def test_accept_one(self):
        data = {"title": "x", "priority": 1}
        TodoRepository._validate_create_data(data)
        assert data["priority"] == 1


class TestRepoPriorityNonInteger:
    """Non-integer priority values are rejected with ValueError."""

    def test_reject_float(self):
        data = {"title": "x", "priority": 3.14}
        with pytest.raises(ValueError, match="priority must be an integer"):
            TodoRepository._validate_create_data(data)

    def test_reject_string(self):
        data = {"title": "x", "priority": "high"}
        with pytest.raises(ValueError, match="priority must be an integer"):
            TodoRepository._validate_create_data(data)

    def test_reject_none(self):
        data = {"title": "x", "priority": None}
        with pytest.raises(ValueError, match="priority must be an integer"):
            TodoRepository._validate_create_data(data)

    def test_reject_bool(self):
        data = {"title": "x", "priority": True}
        with pytest.raises(ValueError, match="priority must be an integer"):
            TodoRepository._validate_create_data(data)

    def test_reject_list(self):
        data = {"title": "x", "priority": [5]}
        with pytest.raises(ValueError, match="priority must be an integer"):
            TodoRepository._validate_create_data(data)


class TestRepoPriorityDefault:
    """When priority is omitted, the model default (0) applies."""

    def test_missing_priority_not_modified(self):
        data: dict = {"title": "x"}
        TodoRepository._validate_create_data(data)
        assert "priority" not in data


class TestDbModelPriorityConstraint:
    """H.14: TodoModel.priority has a DB-level CheckConstraint."""

    def test_check_constraint_exists(self):
        from general_ludd.db.models import TodoModel

        table = TodoModel.__table__
        constraints = [c.name for c in table.constraints if isinstance(c, __import__("sqlalchemy").CheckConstraint)]
        assert "ck_todos_priority_range" in constraints

    def test_check_constraint_bounds(self):
        from general_ludd.db.models import TodoModel

        constraint = None
        for c in TodoModel.__table__.constraints:
            if getattr(c, "name", "") == "ck_todos_priority_range":
                constraint = c
        assert constraint is not None
        sql_text = constraint.sqltext.text if hasattr(constraint, "sqltext") else str(constraint)
        assert "priority >= 0" in sql_text.lower()
        assert "priority <= 1000" in sql_text.lower()
