"""Structural tests for eval/scorers.py — G2 eval harness scoring functions."""

from __future__ import annotations

from general_ludd.eval.scorers import (
    check_assertions,
    composite_eval_score,
    compute_patch_similarity,
)


class TestComputePatchSimilarity:
    def test_identical_strings(self):
        assert compute_patch_similarity("hello world", "hello world") == 1.0

    def test_completely_different(self):
        result = compute_patch_similarity("abc", "xyz")
        assert result < 1.0

    def test_both_empty(self):
        assert compute_patch_similarity("", "") == 1.0

    def test_one_empty(self):
        assert compute_patch_similarity("hello", "") == 0.0
        assert compute_patch_similarity("", "hello") == 0.0

    def test_partial_match(self):
        result = compute_patch_similarity("hello world", "hello there")
        assert 0.0 < result < 1.0


class TestCheckAssertions:
    def test_patch_contains(self):
        result = check_assertions({"patch_contains": "def test"}, "def test_foo():\n    pass")
        assert result["patch_contains"] is True

    def test_patch_contains_missing(self):
        result = check_assertions({"patch_contains": "missing_func"}, "def test_foo():\n    pass")
        assert result["patch_contains"] is False

    def test_filename(self):
        result = check_assertions({"filename": "test.py"}, "test.py content")
        assert result["filename"] is True

    def test_line_count_min_pass(self):
        result = check_assertions({"line_count_min": "2"}, "line1\nline2\nline3")
        assert result["line_count_min"] is True

    def test_line_count_min_fail(self):
        result = check_assertions({"line_count_min": "5"}, "line1\nline2")
        assert result["line_count_min"] is False

    def test_invalid_line_count(self):
        result = check_assertions({"line_count_min": "not-a-number"}, "line1")
        assert result["line_count_min"] is False

    def test_unknown_key_falls_back_to_contains(self):
        result = check_assertions({"custom_key": "target"}, "this has target in it")
        assert result["custom_key"] is True


class TestCompositeEvalScore:
    def test_returns_eval_result(self):
        from general_ludd.eval.schema import EvalCase
        case = EvalCase(
            id="test-1",
            description="a test",
            input_files={"main.py": ""},
            expected_patch="def test():\n    pass",
            assertions={},
        )
        result = composite_eval_score(case, "def test():\n    pass", tokens_used=100, duration_ms=500)
        assert result.case_id == "test-1"
        assert result.tokens_used == 100
        assert result.duration_ms == 500
        assert result.score >= 0.0

    def test_perfect_match_scores_high(self):
        from general_ludd.eval.schema import EvalCase
        case = EvalCase(
            id="test-2",
            description="perfect match",
            input_files={"main.py": ""},
            expected_patch="exact match",
            assertions={},
        )
        result = composite_eval_score(case, "exact match", tokens_used=0, duration_ms=0)
        assert result.score >= 0.9

    def test_no_match_fails(self):
        from general_ludd.eval.schema import EvalCase
        case = EvalCase(
            id="test-3",
            description="no match",
            input_files={"main.py": ""},
            expected_patch="aaaaaaaaaa",
            assertions={},
        )
        result = composite_eval_score(case, "bbbbbbbbbb", tokens_used=0, duration_ms=0)
        assert not result.passed
        assert result.score < 0.5

    def test_with_assertions_populates_errors(self):
        from general_ludd.eval.schema import EvalCase
        case = EvalCase(
            id="test-4",
            description="has assertion",
            input_files={"main.py": ""},
            expected_patch="content",
            assertions={"patch_contains": "missing_func"},
        )
        result = composite_eval_score(case, "content here", tokens_used=0, duration_ms=0)
        assert len(result.errors) > 0
        assert any("assertion_failed:patch_contains" in e for e in result.errors)
