"""Tests for G2 eval harness wiring — run_single, run_benchmark scoring, last_results."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.eval.harness import EvalHarness
from general_ludd.eval.model import ModelEvaluator
from general_ludd.eval.schema import EvalCase, EvalResult
from general_ludd.models.gateway import ModelGateway, ModelResponse

PATCH_TEXT = (
    "--- a/main.py\n+++ b/main.py\n@@ -1,2 +1,2 @@\n"
    " def foo(x):\n-    return x.bar()\n+    return x.bar() if x else None\n"
)


def _make_case(
    case_id: str = "c1",
    description: str = "Fix NPE",
    input_files: dict[str, str] | None = None,
    expected_patch: str = PATCH_TEXT,
) -> EvalCase:
    return EvalCase(
        id=case_id,
        description=description,
        input_files=input_files or {"main.py": "def foo(x): return x.bar()\n"},
        expected_patch=expected_patch,
    )


def _make_evaluator() -> ModelEvaluator:
    gateway = MagicMock(spec=ModelGateway)
    response = MagicMock(spec=ModelResponse)
    response.content = PATCH_TEXT
    gateway.call_model.return_value = response
    return ModelEvaluator(gateway, profile_id="sonnet")


class TestRunSingle:
    def test_run_single_returns_eval_result(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case()

        result = harness.run_single(case)

        assert isinstance(result, EvalResult)
        assert result.case_id == "c1"
        assert result.actual_patch == PATCH_TEXT

    def test_run_single_scores_high_for_perfect_match(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case(expected_patch=PATCH_TEXT)

        result = harness.run_single(case)

        assert result.passed is True
        assert result.score > 0.9

    def test_run_single_no_evaluator_returns_error(self):
        harness = EvalHarness(model="sonnet", evaluator=None)
        case = _make_case()

        result = harness.run_single(case)

        assert result.passed is False
        assert "no evaluator configured" in result.errors

    def test_run_single_records_duration(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case()

        result = harness.run_single(case)

        assert result.duration_ms >= 0


class TestRunBenchmark:
    def test_run_benchmark_uses_composite_score(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case(expected_patch=PATCH_TEXT)

        results = harness.run_benchmark([case])

        assert len(results) == 1
        assert results[0].score > 0.9
        assert results[0].passed is True

    def test_run_benchmark_no_evaluator(self):
        harness = EvalHarness(model="sonnet", evaluator=None)
        case = _make_case()

        results = harness.run_benchmark([case])

        assert len(results) == 1
        assert results[0].passed is False
        assert "no evaluator configured" in results[0].errors

    def test_run_benchmark_stores_last_results(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        case = _make_case()

        harness.run_benchmark([case])

        stored = harness.last_results
        assert len(stored) == 1
        assert stored[0].case_id == "c1"

    def test_last_results_returns_copy_not_reference(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        harness.run_benchmark([_make_case()])

        copy1 = harness.last_results
        copy2 = harness.last_results
        assert copy1 is not copy2

    def test_run_benchmark_multiple_cases(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        cases = [
            _make_case(case_id="c1"),
            _make_case(case_id="c2"),
            _make_case(case_id="c3"),
        ]

        results = harness.run_benchmark(cases)

        assert len(results) == 3
        assert [r.case_id for r in results] == ["c1", "c2", "c3"]
        assert all(r.passed for r in results)


class TestLastResults:
    def test_last_results_empty_initially(self):
        harness = EvalHarness(model="sonnet")
        assert harness.last_results == []

    def test_last_results_persisted_after_benchmark(self):
        evaluator = _make_evaluator()
        harness = EvalHarness(model="sonnet", evaluator=evaluator)
        harness.run_benchmark([
            _make_case(case_id="a"),
            _make_case(case_id="b"),
        ])
        assert len(harness.last_results) == 2
        assert harness.last_results[0].case_id == "a"

    def test_last_results_unchanged_after_no_evaluator_run(self):
        harness = EvalHarness(model="sonnet")
        harness.run_benchmark([_make_case()])
        results = harness.last_results
        assert len(results) == 1
        assert results[0].passed is False
