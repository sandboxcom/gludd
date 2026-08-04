"""Tests for eval scoring functions."""

from __future__ import annotations

from general_ludd.eval.schema import EvalCase
from general_ludd.eval.scorers import (
    check_assertions,
    composite_eval_score,
    compute_patch_similarity,
)


class TestComputePatchSimilarity:
    def test_both_empty(self):
        assert compute_patch_similarity("", "") == 1.0

    def test_expected_empty_actual_nonempty(self):
        assert compute_patch_similarity("", "+x") == 0.0

    def test_actual_empty_expected_nonempty(self):
        assert compute_patch_similarity("+x", "") == 0.0

    def test_identical(self):
        assert compute_patch_similarity("+print(1)", "+print(1)") == 1.0

    def test_completely_different(self):
        similarity = compute_patch_similarity("+print(1)", "+print(2)")
        assert similarity < 1.0
        assert similarity > 0.0

    def test_similar(self):
        s = compute_patch_similarity("+print(1)\n+print(2)", "+print(1)\n+print(3)")
        assert s > 0.8


class TestCheckAssertions:
    def test_empty_assertions(self):
        assert check_assertions({}, "anything") == {}

    def test_patch_contains_found(self):
        result = check_assertions({"patch_contains": "hello"}, "hello world")
        assert result == {"patch_contains": True}

    def test_patch_contains_not_found(self):
        result = check_assertions({"patch_contains": "xyz"}, "hello world")
        assert result == {"patch_contains": False}

    def test_filename_check(self):
        result = check_assertions({"filename": "main.py"}, "--- main.py\n+++")
        assert result == {"filename": True}

    def test_line_count_min_passes(self):
        result = check_assertions({"line_count_min": "3"}, "line1\nline2\nline3\nline4")
        assert result == {"line_count_min": True}

    def test_line_count_min_fails(self):
        result = check_assertions({"line_count_min": "10"}, "line1\nline2")
        assert result == {"line_count_min": False}

    def test_line_count_min_invalid_value(self):
        result = check_assertions({"line_count_min": "abc"}, "line1")
        assert result == {"line_count_min": False}

    def test_unknown_key_defaults_to_contains(self):
        result = check_assertions({"custom_key": "abc"}, "abc here")
        assert result == {"custom_key": True}

    def test_multiple_assertions(self):
        result = check_assertions(
            {"patch_contains": "fix", "line_count_min": "1", "filename": "test.py"},
            "fix the bug in test.py",
        )
        assert result["patch_contains"] is True
        assert result["line_count_min"] is True
        assert result["filename"] is True


class TestCompositeEvalScore:
    def test_perfect_match(self):
        case = EvalCase(id="1", description="d", input_files={}, expected_patch="+x")
        result = composite_eval_score(case, "+x", tokens_used=100, duration_ms=500)
        assert result.passed is True
        assert result.score >= 0.9
        assert result.case_id == "1"
        assert result.actual_patch == "+x"
        assert result.tokens_used == 100
        assert result.duration_ms == 500

    def test_complete_mismatch(self):
        case = EvalCase(id="1", description="d", input_files={}, expected_patch="abcd")
        result = composite_eval_score(case, "wxyz", tokens_used=50, duration_ms=200)
        assert result.passed is False
        assert result.score < 0.7

    def test_with_assertions_all_pass(self):
        case = EvalCase(
            id="2",
            description="d",
            input_files={},
            expected_patch="+print(1)",
            assertions={"patch_contains": "print"},
        )
        result = composite_eval_score(case, "+print(1)", tokens_used=10, duration_ms=100)
        assert result.passed is True

    def test_assertion_failure_in_errors(self):
        case = EvalCase(
            id="3",
            description="d",
            input_files={},
            expected_patch="+print(1)",
            assertions={"patch_contains": "MISSING"},
        )
        result = composite_eval_score(case, "+print(1)", tokens_used=10, duration_ms=100)
        assert any("assertion_failed" in e for e in result.errors)

    def test_low_similarity_in_errors(self):
        case = EvalCase(id="4", description="d", input_files={}, expected_patch="abcdefgh")
        result = composite_eval_score(case, "wxyz", tokens_used=10, duration_ms=100)
        assert any("low_similarity" in e for e in result.errors)
