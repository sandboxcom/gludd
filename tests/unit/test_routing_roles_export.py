"""Structural tests for routing_roles/roles.py — TaskRole re-export."""

from __future__ import annotations

from general_ludd.routing_roles.roles import TaskRole


class TestTaskRoleReExport:
    def test_taskrole_is_importable(self):
        assert TaskRole is not None

    def test_taskrole_is_an_enum(self):
        from enum import StrEnum
        assert issubclass(TaskRole, StrEnum)

    def test_taskrole_has_expected_members(self):
        assert TaskRole.PLANNER.value == "planner"
        assert TaskRole.CODER.value == "coder"
        assert TaskRole.REVIEWER.value == "reviewer"
        assert TaskRole.EDITOR.value == "editor"
        assert TaskRole.COMPACTOR.value == "compactor"
        assert TaskRole.ENUMERATOR.value == "enumerator"

    def test_taskrole_members_count(self):
        members = list(TaskRole)
        assert len(members) == 6

    def test_re_exported_from_benchmark(self):
        from general_ludd.schemas.benchmark import TaskRole as BenchmarkTaskRole
        assert TaskRole is BenchmarkTaskRole
