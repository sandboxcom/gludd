"""Tests for outcome-driven self-improvement (G5)."""

from __future__ import annotations

from general_ludd.eval.schema import EvalResult
from general_ludd.self_improve.outcomes import (
    OutcomeAnalyzer,
    analyze_eval_results,
)


class TestOutcomeAnalyzer:
    def test_analyze_no_data_returns_empty_suggestions(self):
        analyzer = OutcomeAnalyzer()
        result = analyzer.analyze()

        assert result == {"status": "no_data", "suggestions": []}

    def test_analyze_accepts_outcomes_and_stores_them(self):
        analyzer = OutcomeAnalyzer()
        outcomes = [
            {"task_id": "a", "status": "completed", "task_type": "bugfix",
             "model": "sonnet", "passed": True, "duration_s": 12.5},
            {"task_id": "b", "status": "failed", "task_type": "bugfix",
             "model": "sonnet", "passed": True, "duration_s": 3.0},
        ]

        result = analyzer.analyze(outcomes)

        assert result["status"] == "analyzed"
        assert result["suggestions"] == []
        assert len(analyzer._outcomes) == 2

    def test_init_respects_min_samples(self):
        analyzer = OutcomeAnalyzer(min_samples=5)
        assert analyzer.min_samples == 5

    def test_init_defaults_min_samples(self):
        analyzer = OutcomeAnalyzer()
        assert analyzer.min_samples == OutcomeAnalyzer.DEFAULT_MIN_SAMPLES

    def test_analyze_accumulates_across_calls(self):
        analyzer = OutcomeAnalyzer()
        analyzer.analyze([{"task_id": "a"}])
        result = analyzer.analyze([{"task_id": "b"}])

        assert len(analyzer._outcomes) == 2
        assert result["status"] == "analyzed"

    def test_analyze_with_eval_outcomes_flags_low_pass_rate(self):
        analyzer = OutcomeAnalyzer()
        outcomes = [
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 100, "duration_ms": 500},
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 200, "duration_ms": 600},
            {"task_type": "bugfix", "model": "sonnet", "passed": True,
             "tokens_used": 150, "duration_ms": 550},
        ]

        result = analyzer.analyze(outcomes, threshold=0.5)

        assert result["status"] == "analyzed"
        suggestions = result["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["task_type"] == "bugfix"
        assert suggestions[0]["model"] == "sonnet"
        assert suggestions[0]["pass_rate"] == 0.3333
        assert suggestions[0]["sample_count"] == 3

    def test_analyze_does_not_flag_above_threshold(self):
        analyzer = OutcomeAnalyzer()
        outcomes = [
            {"task_type": "bugfix", "model": "sonnet", "passed": True,
             "tokens_used": 100, "duration_ms": 500},
            {"task_type": "bugfix", "model": "sonnet", "passed": True,
             "tokens_used": 200, "duration_ms": 600},
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 150, "duration_ms": 550},
        ]

        result = analyzer.analyze(outcomes, threshold=0.5)

        suggestions = result["suggestions"]
        assert len(suggestions) == 0

    def test_analyze_mixed_task_types_flags_only_low_groups(self):
        analyzer = OutcomeAnalyzer()
        outcomes = [
            # bugfix: 1/4 passed = 0.25 — below 0.5
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 100, "duration_ms": 500},
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 100, "duration_ms": 500},
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 100, "duration_ms": 500},
            {"task_type": "bugfix", "model": "sonnet", "passed": True,
             "tokens_used": 100, "duration_ms": 500},
            # refactor: 3/3 passed = 1.0 — above 0.5
            {"task_type": "refactor", "model": "haiku", "passed": True,
             "tokens_used": 50, "duration_ms": 200},
            {"task_type": "refactor", "model": "haiku", "passed": True,
             "tokens_used": 60, "duration_ms": 250},
            {"task_type": "refactor", "model": "haiku", "passed": True,
             "tokens_used": 55, "duration_ms": 220},
        ]

        result = analyzer.analyze(outcomes, threshold=0.5)

        suggestions = result["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["task_type"] == "bugfix"
        assert suggestions[0]["model"] == "sonnet"
        assert suggestions[0]["pass_rate"] == 0.25

    def test_analyze_computes_avg_tokens_and_duration(self):
        analyzer = OutcomeAnalyzer()
        outcomes = [
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 100, "duration_ms": 400},
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 200, "duration_ms": 600},
        ]

        result = analyzer.analyze(outcomes, threshold=1.0)

        s = result["suggestions"][0]
        assert s["avg_tokens"] == 150.0
        assert s["avg_duration_ms"] == 500.0

    def test_analyze_multiple_models_same_task_type(self):
        analyzer = OutcomeAnalyzer()
        outcomes = [
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 100, "duration_ms": 500},
            {"task_type": "bugfix", "model": "sonnet", "passed": False,
             "tokens_used": 100, "duration_ms": 500},
            {"task_type": "bugfix", "model": "opus", "passed": True,
             "tokens_used": 200, "duration_ms": 800},
            {"task_type": "bugfix", "model": "opus", "passed": True,
             "tokens_used": 200, "duration_ms": 800},
        ]

        result = analyzer.analyze(outcomes, threshold=0.5)

        suggestions = result["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["model"] == "sonnet"
        assert suggestions[0]["pass_rate"] == 0.0

    def test_outcomes_missing_fields_default_to_zero(self):
        analyzer = OutcomeAnalyzer()
        outcomes = [
            {"task_type": "bugfix", "model": "sonnet", "passed": False},
            {"task_type": "bugfix", "model": "sonnet", "passed": False},
        ]

        result = analyzer.analyze(outcomes, threshold=1.0)

        s = result["suggestions"][0]
        assert s["avg_tokens"] == 0.0
        assert s["avg_duration_ms"] == 0.0


class TestAnalyzeEvalResults:
    def test_flags_low_pass_rate_group(self):
        results = [
            EvalResult(case_id="c1", passed=False, actual_patch=""),
            EvalResult(case_id="c2", passed=False, actual_patch=""),
            EvalResult(case_id="c3", passed=True, actual_patch="",
                       tokens_used=100, duration_ms=500),
        ]
        case_task_types = {"c1": "bugfix", "c2": "bugfix", "c3": "bugfix"}

        result = analyze_eval_results(results, case_task_types,
                                      model="sonnet", threshold=0.5)

        assert result["status"] == "analyzed"
        suggestions = result["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["task_type"] == "bugfix"
        assert suggestions[0]["model"] == "sonnet"
        assert suggestions[0]["pass_rate"] == 0.3333

    def test_does_not_flag_above_threshold(self):
        results = [
            EvalResult(case_id="c1", passed=True, actual_patch=""),
            EvalResult(case_id="c2", passed=True, actual_patch=""),
            EvalResult(case_id="c3", passed=False, actual_patch=""),
        ]
        case_task_types = {"c1": "bugfix", "c2": "bugfix", "c3": "bugfix"}

        result = analyze_eval_results(results, case_task_types,
                                      model="sonnet", threshold=0.5)

        assert result["suggestions"] == []

    def test_mixed_task_types_flags_only_low_groups(self):
        results = [
            EvalResult(case_id="c1", passed=False, actual_patch=""),
            EvalResult(case_id="c2", passed=False, actual_patch=""),
            EvalResult(case_id="c3", passed=False, actual_patch=""),
            EvalResult(case_id="c4", passed=True, actual_patch=""),
            EvalResult(case_id="c5", passed=True, actual_patch=""),
            EvalResult(case_id="c6", passed=True, actual_patch=""),
            EvalResult(case_id="c7", passed=True, actual_patch=""),
        ]
        case_task_types = {
            "c1": "bugfix", "c2": "bugfix", "c3": "bugfix", "c4": "bugfix",
            "c5": "refactor", "c6": "refactor", "c7": "refactor",
        }

        result = analyze_eval_results(results, case_task_types,
                                      model="sonnet", threshold=0.5)

        suggestions = result["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["task_type"] == "bugfix"
        assert suggestions[0]["pass_rate"] == 0.25

    def test_computes_avg_tokens_and_duration(self):
        results = [
            EvalResult(case_id="c1", passed=False, actual_patch="",
                       tokens_used=100, duration_ms=400),
            EvalResult(case_id="c2", passed=False, actual_patch="",
                       tokens_used=200, duration_ms=600),
        ]
        case_task_types = {"c1": "bugfix", "c2": "bugfix"}

        result = analyze_eval_results(results, case_task_types,
                                      model="sonnet", threshold=1.0)

        s = result["suggestions"][0]
        assert s["avg_tokens"] == 150.0
        assert s["avg_duration_ms"] == 500.0

    def test_unknown_case_id_defaults_to_unknown_task_type(self):
        results = [
            EvalResult(case_id="nonexistent", passed=False, actual_patch=""),
        ]
        case_task_types: dict[str, str] = {}

        result = analyze_eval_results(results, case_task_types,
                                      model="sonnet", threshold=1.0)

        s = result["suggestions"][0]
        assert s["task_type"] == "unknown"

    def test_empty_results_returns_no_data(self):
        result = analyze_eval_results([], {}, model="sonnet")
        assert result["status"] == "analyzed"
        assert result["suggestions"] == []
