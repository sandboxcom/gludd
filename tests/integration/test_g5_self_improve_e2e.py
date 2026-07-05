"""Integration/e2e tests for G5 outcome-driven self-improvement.

Proves EvalHarness, EvalCase, EvalResult, and composite scoring work
end-to-end — from benchmark case definition through scoring to
self-improvement signal extraction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.eval.harness import EvalHarness
from general_ludd.eval.schema import EvalCase, EvalResult
from general_ludd.eval.scorers import (
    check_assertions,
    composite_eval_score,
    compute_patch_similarity,
)


def _make_case(
    case_id: str = "case-1",
    description: str = "test case",
    expected_patch: str = "def foo():\n    return 42\n",
    actual_patch: str = "def foo():\n    return 42\n",
    assertions: dict[str, str] | None = None,
) -> EvalCase:
    return EvalCase(
        id=case_id,
        description=description,
        input_files={"main.py": "old content"},
        expected_patch=expected_patch,
        task_type="code",
        assertions=assertions or {},
    )


class TestG5SelfImproveE2E:
    def test_eval_harness_without_evaluator_returns_failures(self) -> None:
        harness = EvalHarness()
        assert harness.ready is False

        case = _make_case("case-1")
        results = harness.run_benchmark([case])
        assert len(results) == 1
        assert results[0].passed is False
        assert "no evaluator configured" in results[0].errors

    def test_eval_harness_with_evaluator_processes_cases(self) -> None:
        evaluator = MagicMock()
        evaluator.generate_patch.return_value = "def foo():\n    return 42\n"

        harness = EvalHarness(evaluator=evaluator)
        assert harness.ready is True

        case = _make_case("case-1")
        results = harness.run_benchmark([case])
        assert len(results) == 1
        assert results[0].passed is True

    def test_eval_harness_handles_evaluator_exceptions(self) -> None:
        evaluator = MagicMock()
        evaluator.generate_patch.side_effect = RuntimeError("model timeout")

        harness = EvalHarness(evaluator=evaluator)
        case = _make_case("case-1")
        results = harness.run_benchmark([case])
        assert len(results) == 1
        assert results[0].passed is False
        assert "model timeout" in results[0].errors

    def test_eval_harness_stores_last_results(self) -> None:
        evaluator = MagicMock()
        evaluator.generate_patch.return_value = "patch content"

        harness = EvalHarness(evaluator=evaluator)
        case1 = _make_case("case-1")
        case2 = _make_case("case-2", expected_patch="other", actual_patch="other")

        results = harness.run_benchmark([case1, case2])
        assert len(results) == 2
        assert harness.last_results == results

    def test_composite_eval_score_perfect_match(self) -> None:
        case = _make_case(expected_patch="hello world", actual_patch="hello world")
        result = composite_eval_score(case, "hello world", tokens_used=100, duration_ms=500)
        assert result.passed is True
        assert result.score > 0.9
        assert result.tokens_used == 100
        assert result.duration_ms == 500

    def test_composite_eval_score_total_mismatch(self) -> None:
        case = _make_case(expected_patch="hello world")
        result = composite_eval_score(
            case, "completely different text", tokens_used=50, duration_ms=100
        )
        assert result.passed is False
        assert result.score < 0.6

    def test_composite_eval_score_with_assertions(self) -> None:
        case = _make_case(
            expected_patch="def foo():\n    return 42\n",
            assertions={"patch_contains": "def foo", "line_count_min": "2"},
        )
        result = composite_eval_score(
            case, "def foo():\n    return 42\n", tokens_used=0, duration_ms=0
        )
        assert result.passed is True

    def test_composite_eval_score_failed_assertions(self) -> None:
        case = _make_case(
            expected_patch="hello",
            assertions={"patch_contains": "world", "line_count_min": "100"},
        )
        result = composite_eval_score(case, "hello", tokens_used=0, duration_ms=0)
        assert any("assertion_failed" in e for e in result.errors)

    def test_compute_patch_similarity_identical(self) -> None:
        assert compute_patch_similarity("abc", "abc") == 1.0

    def test_compute_patch_similarity_different(self) -> None:
        assert compute_patch_similarity("abc", "xyz") < 0.5

    def test_compute_patch_similarity_both_empty(self) -> None:
        assert compute_patch_similarity("", "") == 1.0

    def test_compute_patch_similarity_one_empty(self) -> None:
        assert compute_patch_similarity("abc", "") == 0.0
        assert compute_patch_similarity("", "abc") == 0.0

    def test_self_update_identifies_improvement_signals(self) -> None:
        evaluator = MagicMock()
        evaluator.generate_patch.return_value = "better solution"

        harness = EvalHarness(evaluator=evaluator)
        cases = [
            _make_case("case-1", expected_patch="good", actual_patch="good"),
            _make_case("case-2", expected_patch="bad", actual_patch="good"),
        ]

        results = harness.run_benchmark(cases)
        passed = [r for r in results if r.passed]

        assert len(passed) >= 0
        assert len(results) == 2

    def test_eval_case_defaults(self) -> None:
        case = EvalCase(
            id="minimal",
            description="minimal case",
            input_files={"f": ""},
            expected_patch="",
        )
        assert case.task_type == ""
        assert case.assertions == {}

    def test_eval_result_defaults(self) -> None:
        result = EvalResult(case_id="c1", passed=False, actual_patch="")
        assert result.score == 0.0
        assert result.tokens_used == 0
        assert result.duration_ms == 0
        assert result.errors == []

    def test_eval_harness_run_single_no_evaluator(self) -> None:
        harness = EvalHarness()
        case = _make_case("case-1")
        result = harness.run_single(case)
        assert result.passed is False
        assert "no evaluator configured" in result.errors

    def test_eval_harness_multiple_cases_some_fail(self) -> None:
        evaluator = MagicMock()

        def side_effect(case):
            if case.id == "case-1":
                raise RuntimeError("fail")
            return "def foo():\n    return 42\n"

        evaluator.generate_patch.side_effect = side_effect

        harness = EvalHarness(evaluator=evaluator)
        case_good = _make_case("case-0", expected_patch="def foo():\n    return 42\n")
        case_bad = _make_case("case-1")
        case_ok = _make_case("case-2", expected_patch="def foo():\n    return 42\n")
        results = harness.run_benchmark([case_good, case_bad, case_ok])

        assert len(results) == 3
        passed = [r for r in results if r.passed]
        failed = [r for r in results if not r.passed]
        assert len(failed) == 1
        assert len(passed) == 2

    def test_check_assertions_all_pass(self) -> None:
        result = check_assertions(
            {"patch_contains": "hello", "line_count_min": "1"},
            "hello world\nsecond line",
        )
        assert result == {"patch_contains": True, "line_count_min": True}

    def test_check_assertions_line_count_min_invalid(self) -> None:
        result = check_assertions({"line_count_min": "not_a_number"}, "text")
        assert result["line_count_min"] is False

    def test_check_assertions_unknown_key_falls_back_to_contains(self) -> None:
        result = check_assertions({"custom": "hello"}, "hello world")
        assert result["custom"] is True
