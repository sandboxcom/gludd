"""Unit tests for routing_roles.roles re-export module."""

from __future__ import annotations

from general_ludd.routing_roles.roles import TaskRole
from general_ludd.schemas.benchmark import TaskRole as OrigTaskRole


class TestTaskRoleReexport:
    def test_reexported_taskrole_is_same_class(self):
        assert TaskRole is OrigTaskRole

    def test_reexported_taskrole_has_all_members(self):
        expected = {"PLANNER", "CODER", "REVIEWER", "EDITOR", "COMPACTOR", "ENUMERATOR"}
        actual = set(TaskRole.__members__.keys())
        assert actual == expected

    def test_taskrole_values_match(self):
        assert TaskRole.PLANNER == "planner"
        assert TaskRole.CODER == "coder"
        assert TaskRole.REVIEWER == "reviewer"

    def test_taskrole_is_str_enum(self):
        from enum import StrEnum

        assert issubclass(TaskRole, StrEnum)

    def test_taskrole_string_coercion(self):
        assert str(TaskRole.PLANNER) == "planner"
        assert f"{TaskRole.CODER}" == "coder"
