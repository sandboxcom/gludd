"""Tests for eval data schemas — EvalCase and EvalResult dataclasses."""

from __future__ import annotations

from general_ludd.eval.schema import EvalCase, EvalResult


class TestEvalCase:
    def test_default_construction(self):
        case = EvalCase(id="1", description="fix bug", input_files={}, expected_patch="diff")
        assert case.id == "1"
        assert case.description == "fix bug"
        assert case.input_files == {}
        assert case.expected_patch == "diff"
        assert case.task_type == ""
        assert case.assertions == {}

    def test_full_construction(self):
        case = EvalCase(
            id="2",
            description="add feature",
            input_files={"main.py": "print(1)"},
            expected_patch="+print(2)",
            task_type="refactor",
            assertions={"has_import": "True"},
        )
        assert case.task_type == "refactor"
        assert case.assertions == {"has_import": "True"}

    def test_equality(self):
        a = EvalCase(id="1", description="d", input_files={}, expected_patch="p")
        b = EvalCase(id="1", description="d", input_files={}, expected_patch="p")
        assert a == b

    def test_inequality_different_id(self):
        a = EvalCase(id="1", description="d", input_files={}, expected_patch="p")
        b = EvalCase(id="2", description="d", input_files={}, expected_patch="p")
        assert a != b


class TestEvalResult:
    def test_default_construction(self):
        result = EvalResult(case_id="1", passed=True, actual_patch="+x")
        assert result.case_id == "1"
        assert result.passed is True
        assert result.actual_patch == "+x"
        assert result.score == 0.0
        assert result.tokens_used == 0
        assert result.duration_ms == 0
        assert result.errors == []

    def test_failed_result(self):
        result = EvalResult(
            case_id="1",
            passed=False,
            actual_patch="",
            score=0.0,
            errors=["syntax error"],
        )
        assert result.passed is False
        assert result.errors == ["syntax error"]

    def test_equality(self):
        a = EvalResult(case_id="1", passed=True, actual_patch="p")
        b = EvalResult(case_id="1", passed=True, actual_patch="p")
        assert a == b

    def test_inequality_different_case(self):
        a = EvalResult(case_id="1", passed=True, actual_patch="p")
        b = EvalResult(case_id="2", passed=True, actual_patch="p")
        assert a != b
