"""Structural tests for routing_roles/roles.py — TaskRole re-export."""

from __future__ import annotations

from enum import StrEnum

from general_ludd.routing_roles.roles import TaskRole
from general_ludd.schemas.benchmark import TaskRole as OriginalTaskRole


class TestTaskRoleReExport:
    def test_taskrole_is_importable(self):
        assert TaskRole is not None

    def test_taskrole_is_same_class(self):
        assert TaskRole is OriginalTaskRole

    def test_taskrole_is_str_enum(self):
        assert issubclass(TaskRole, StrEnum)

    def test_taskrole_expected_members(self):
        assert TaskRole.PLANNER.value == "planner"
        assert TaskRole.CODER.value == "coder"
        assert TaskRole.REVIEWER.value == "reviewer"
        assert TaskRole.EDITOR.value == "editor"
        assert TaskRole.COMPACTOR.value == "compactor"
        assert TaskRole.ENUMERATOR.value == "enumerator"

    def test_taskrole_member_count(self):
        members = list(TaskRole)
        assert len(members) == 6
