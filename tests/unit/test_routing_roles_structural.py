"""Structural tests for routing_roles/roles.py — re-export module shape."""

from __future__ import annotations

from enum import StrEnum

import general_ludd.routing_roles.roles as rr


class TestModuleProperties:
    def test_all_exports_taskrole(self):
        assert hasattr(rr, "TaskRole")
        assert "TaskRole" in rr.__all__

    def test_module_is_importable(self):
        import importlib
        mod = importlib.import_module("general_ludd.routing_roles.roles")
        assert mod is not None

    def test_taskrole_is_str_enum(self):
        assert issubclass(rr.TaskRole, StrEnum)
