"""Structural tests for observability/recorder.py — compute_scores_from_trace, AutoBenchmarkRecorder."""

from __future__ import annotations

from general_ludd.observability.recorder import AutoBenchmarkRecorder, compute_scores_from_trace
from general_ludd.observability.tracer import ExecutionTrace


class TestComputeScoresFromTrace:
    def test_returns_dict_with_expected_keys(self):
        trace = ExecutionTrace()
        result = compute_scores_from_trace(trace, success=True)

        assert isinstance(result, dict)
        assert "completion" in result
        assert "code_quality" in result
        assert "instruction" in result
        assert "token_efficiency" in result

    def test_success_true_gives_completion_1(self):
        trace = ExecutionTrace()
        result = compute_scores_from_trace(trace, success=True)

        assert result["completion"] == 1.0
        assert result["instruction"] == 1.0

    def test_success_false_gives_completion_0(self):
        trace = ExecutionTrace()
        result = compute_scores_from_trace(trace, success=False)

        assert result["completion"] == 0.0
        assert result["instruction"] == 0.5

    def test_token_efficiency_bounded_0_to_1(self):
        trace = ExecutionTrace()
        result = compute_scores_from_trace(trace, success=True)

        assert 0.0 <= result["token_efficiency"] <= 1.0

    def test_code_quality_defaults_to_0_5(self):
        trace = ExecutionTrace()
        result = compute_scores_from_trace(trace, success=True)

        assert result["code_quality"] == 0.5


class TestAutoBenchmarkRecorder:
    def test_instantiate_defaults(self):
        recorder = AutoBenchmarkRecorder()

        assert recorder._repo is None
        assert recorder._trace_buffer is None

    def test_instantiate_with_none_args(self):
        recorder = AutoBenchmarkRecorder(benchmark_repo=None, trace_buffer=None)

        assert recorder._repo is None
        assert recorder._trace_buffer is None
