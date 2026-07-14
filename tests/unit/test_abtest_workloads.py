"""Unit tests for abtest/workloads.py."""

from __future__ import annotations

from general_ludd.abtest.workloads import (
    KIND_IMPORT_MODULE,
    RESULT_SENTINEL,
    import_module_workload,
)


class TestImportModuleWorkload:
    def test_minimal_workload(self):
        spec = import_module_workload("general_ludd.abtest.workloads")
        assert spec["kind"] == KIND_IMPORT_MODULE
        assert spec["module"] == "general_ludd.abtest.workloads"
        assert "expect_attr" not in spec

    def test_with_expect_attr(self):
        spec = import_module_workload(
            "general_ludd.abtest.workloads",
            expect_attr="RESULT_SENTINEL",
        )
        assert spec["kind"] == KIND_IMPORT_MODULE
        assert spec["module"] == "general_ludd.abtest.workloads"
        assert spec["expect_attr"] == "RESULT_SENTINEL"

    def test_returns_dict(self):
        spec = import_module_workload("some.module")
        assert isinstance(spec, dict)

    def test_empty_module_string(self):
        spec = import_module_workload("")
        assert spec["module"] == ""

    def test_expect_attr_none_not_included(self):
        spec = import_module_workload("m", expect_attr=None)
        assert "expect_attr" not in spec


class TestConstants:
    def test_kind_import_module(self):
        assert KIND_IMPORT_MODULE == "import_module"

    def test_result_sentinel(self):
        assert RESULT_SENTINEL == "RESULT_OK"
