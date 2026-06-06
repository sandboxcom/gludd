"""Tests for ExecutionTrace, AutoBenchmarkRecorder, and ModelComparison."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest


class TestExecutionTracer:
    def test_trace_span_has_required_fields(self):
        from general_ludd.observability.tracer import ExecutionSpan

        span = ExecutionSpan(
            trace_id="trace-001",
            span_id="span-001",
            name="classify_task",
            phase="classify",
            started_at=datetime.now(UTC),
        )
        assert span.trace_id == "trace-001"
        assert span.span_id == "span-001"
        assert span.name == "classify_task"
        assert span.phase == "classify"
        assert span.status == "running"
        assert span.input_tokens == 0
        assert span.output_tokens == 0
        assert span.cost_usd == 0.0

    def test_trace_span_complete_sets_duration(self):
        from general_ludd.observability.tracer import ExecutionSpan

        start = datetime.now(UTC)
        span = ExecutionSpan(
            trace_id="t1",
            span_id="s1",
            name="generate",
            phase="generate",
            started_at=start,
        )
        end = start + timedelta(seconds=2.5)
        span.complete(
            status="success",
            ended_at=end,
            output_tokens=500,
            input_tokens=2000,
            cost_usd=0.05,
            model_profile_id="gpt4",
            prompt_profile_id="default",
        )
        assert span.status == "success"
        assert span.duration_ms == 2500.0
        assert span.output_tokens == 500
        assert span.input_tokens == 2000
        assert span.cost_usd == 0.05
        assert span.model_profile_id == "gpt4"

    def test_trace_span_fail_sets_error(self):
        from general_ludd.observability.tracer import ExecutionSpan

        span = ExecutionSpan(
            trace_id="t1",
            span_id="s1",
            name="review",
            phase="review",
            started_at=datetime.now(UTC),
        )
        span.complete(status="error", error_message="model timeout")
        assert span.status == "error"
        assert span.error_message == "model timeout"

    def test_execution_trace_creates_spans(self):
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace(todo_id="TODO-001", work_type="code")
        assert trace.todo_id == "TODO-001"
        assert len(trace.spans) == 0

        span = trace.start_span("classify_task", phase="classify")
        assert len(trace.spans) == 1
        assert span.phase == "classify"
        assert span.status == "running"

    def test_execution_trace_multiple_spans_ordered(self):
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace(todo_id="TODO-002", work_type="refactor")
        s1 = trace.start_span("classify", phase="classify")
        s1.complete(status="success")
        s2 = trace.start_span("select_model", phase="select")
        s2.complete(status="success")
        s3 = trace.start_span("generate", phase="generate")
        s3.complete(status="success")

        assert len(trace.spans) == 3
        assert trace.spans[0].name == "classify"
        assert trace.spans[1].name == "select_model"
        assert trace.spans[2].name == "generate"

    def test_execution_trace_to_dict(self):
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace(todo_id="TODO-003", work_type="bug_fix")
        span = trace.start_span("generate", phase="generate")
        span.complete(
            status="success",
            model_profile_id="claude3",
            output_tokens=400,
            input_tokens=1500,
            cost_usd=0.03,
        )

        d = trace.to_dict()
        assert d["todo_id"] == "TODO-003"
        assert d["work_type"] == "bug_fix"
        assert len(d["spans"]) == 1
        assert d["spans"][0]["model_profile_id"] == "claude3"
        assert d["spans"][0]["output_tokens"] == 400

    def test_execution_trace_total_cost(self):
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace(todo_id="TODO-004")
        s1 = trace.start_span("call1", phase="generate")
        s1.complete(cost_usd=0.05, status="success")
        s2 = trace.start_span("call2", phase="review")
        s2.complete(cost_usd=0.02, status="success")

        assert trace.total_cost_usd == 0.07
        assert trace.total_tokens == s1.output_tokens + s2.output_tokens

    def test_execution_trace_success_rate(self):
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace(todo_id="TODO-005")
        s1 = trace.start_span("ok", phase="generate")
        s1.complete(status="success")
        s2 = trace.start_span("fail", phase="review")
        s2.complete(status="error")

        assert trace.success_rate == 0.5


class TestAutoBenchmarkRecorder:
    @pytest.mark.asyncio
    async def test_record_on_task_complete(self):
        from general_ludd.observability.recorder import AutoBenchmarkRecorder
        from general_ludd.observability.tracer import ExecutionTrace

        repo = AsyncMock()
        repo.record_result = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)

        trace = ExecutionTrace(todo_id="TODO-010", work_type="code")
        span = trace.start_span("generate", phase="generate")
        span.complete(
            status="success",
            model_profile_id="gpt4",
            prompt_profile_id="default_prompt",
            output_tokens=500,
            input_tokens=2000,
            cost_usd=0.05,
        )

        await recorder.record_from_trace(trace, success=True)
        repo.record_result.assert_called_once()
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["model_profile_id"] == "gpt4"
        assert call_kwargs["prompt_profile_id"] == "default_prompt"
        assert call_kwargs["success"] is True
        assert call_kwargs["task_type"] == "code"

    @pytest.mark.asyncio
    async def test_record_on_task_failure(self):
        from general_ludd.observability.recorder import AutoBenchmarkRecorder
        from general_ludd.observability.tracer import ExecutionTrace

        repo = AsyncMock()
        repo.record_result = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)

        trace = ExecutionTrace(todo_id="TODO-011", work_type="refactor")
        span = trace.start_span("generate", phase="generate")
        span.complete(status="error", error_message="timeout")

        await recorder.record_from_trace(trace, success=False)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["success"] is False
        assert call_kwargs["error_message"] == "timeout"

    @pytest.mark.asyncio
    async def test_record_no_spans_skips(self):
        from general_ludd.observability.recorder import AutoBenchmarkRecorder
        from general_ludd.observability.tracer import ExecutionTrace

        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)

        trace = ExecutionTrace(todo_id="TODO-012")
        await recorder.record_from_trace(trace, success=True)
        repo.record_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_no_repo_skips_gracefully(self):
        from general_ludd.observability.recorder import AutoBenchmarkRecorder
        from general_ludd.observability.tracer import ExecutionTrace

        recorder = AutoBenchmarkRecorder(benchmark_repo=None)
        trace = ExecutionTrace(todo_id="TODO-013")
        span = trace.start_span("gen", phase="generate")
        span.complete(status="success")
        await recorder.record_from_trace(trace, success=True)

    def test_compute_scores_from_trace(self):
        from general_ludd.observability.recorder import compute_scores_from_trace
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace(todo_id="TODO-014")
        span = trace.start_span("gen", phase="generate")
        span.complete(
            status="success",
            output_tokens=500,
            input_tokens=2000,
        )
        scores = compute_scores_from_trace(trace, success=True)
        assert scores["completion"] == 1.0
        assert scores["code_quality"] == 0.5
        assert scores["instruction"] == 1.0
        assert scores["token_efficiency"] > 0

    def test_compute_scores_from_trace_failure(self):
        from general_ludd.observability.recorder import compute_scores_from_trace
        from general_ludd.observability.tracer import ExecutionTrace

        trace = ExecutionTrace(todo_id="TODO-015")
        span = trace.start_span("gen", phase="generate")
        span.complete(status="error", error_message="crash")
        scores = compute_scores_from_trace(trace, success=False)
        assert scores["completion"] == 0.0
        assert scores["instruction"] == 0.5


class TestModelComparison:
    @pytest.mark.asyncio
    async def test_compare_models_ranks_by_composite(self):
        from general_ludd.observability.comparison import ModelComparison

        mock_repo = AsyncMock()
        mock_repo.get_aggregate_scores.return_value = [
            {"model_profile_id": "m1", "prompt_profile_id": "p1", "task_type": "code",
             "sample_count": 10, "composite_score": 0.85, "avg_cost": 0.01,
             "avg_completion": 0.9, "avg_code_quality": 0.8, "avg_instruction": 0.9, "avg_token_efficiency": 0.7},
            {"model_profile_id": "m2", "prompt_profile_id": "p2", "task_type": "code",
             "sample_count": 8, "composite_score": 0.72, "avg_cost": 0.05,
             "avg_completion": 0.7, "avg_code_quality": 0.7, "avg_instruction": 0.7, "avg_token_efficiency": 0.8},
            {"model_profile_id": "m1", "prompt_profile_id": "p2", "task_type": "code",
             "sample_count": 5, "composite_score": 0.65, "avg_cost": 0.02,
             "avg_completion": 0.6, "avg_code_quality": 0.6, "avg_instruction": 0.6, "avg_token_efficiency": 0.9},
        ]
        comparison = ModelComparison(benchmark_repo=mock_repo)
        result = await comparison.compare_models(task_type="code")
        assert len(result["rankings"]) == 3
        assert result["rankings"][0]["model_profile_id"] == "m1"
        assert result["rankings"][0]["composite_score"] == 0.85

    @pytest.mark.asyncio
    async def test_compare_models_empty_repo(self):
        from general_ludd.observability.comparison import ModelComparison

        mock_repo = AsyncMock()
        mock_repo.get_aggregate_scores.return_value = []
        comparison = ModelComparison(benchmark_repo=mock_repo)
        result = await comparison.compare_models(task_type="docs")
        assert result["rankings"] == []
        assert result["summary"] == "No benchmark data available"

    @pytest.mark.asyncio
    async def test_compare_model_per_task_type(self):
        from general_ludd.observability.comparison import ModelComparison

        mock_repo = AsyncMock()
        mock_repo.get_aggregate_scores.return_value = [
            {"model_profile_id": "cheap", "prompt_profile_id": "p1", "task_type": "code",
             "sample_count": 15, "composite_score": 0.75, "avg_cost": 0.001,
             "avg_completion": 0.8, "avg_code_quality": 0.7, "avg_instruction": 0.8, "avg_token_efficiency": 0.6},
            {"model_profile_id": "expensive", "prompt_profile_id": "p1", "task_type": "code",
             "sample_count": 12, "composite_score": 0.90, "avg_cost": 0.05,
             "avg_completion": 0.9, "avg_code_quality": 0.9, "avg_instruction": 0.9, "avg_token_efficiency": 0.9},
        ]
        comparison = ModelComparison(benchmark_repo=mock_repo)
        result = await comparison.compare_models(task_type="code")
        rankings = result["rankings"]
        assert rankings[0]["composite_score"] >= rankings[1]["composite_score"]

    @pytest.mark.asyncio
    async def test_compare_models_best_by_cost(self):
        from general_ludd.observability.comparison import ModelComparison

        mock_repo = AsyncMock()
        mock_repo.get_aggregate_scores.return_value = [
            {"model_profile_id": "expensive_high_quality", "prompt_profile_id": "p1", "task_type": "code",
             "sample_count": 10, "composite_score": 0.95, "avg_cost": 0.10,
             "avg_completion": 0.9, "avg_code_quality": 0.9, "avg_instruction": 0.9, "avg_token_efficiency": 0.9},
            {"model_profile_id": "cheap_decent", "prompt_profile_id": "p2", "task_type": "code",
             "sample_count": 10, "composite_score": 0.80, "avg_cost": 0.001,
             "avg_completion": 0.8, "avg_code_quality": 0.8, "avg_instruction": 0.8, "avg_token_efficiency": 0.8},
        ]
        comparison = ModelComparison(benchmark_repo=mock_repo)
        result = await comparison.compare_models(task_type="code", sort_by="cost")
        rankings = result["rankings"]
        assert rankings[0]["model_profile_id"] == "cheap_decent"

    @pytest.mark.asyncio
    async def test_compare_models_no_repo(self):
        from general_ludd.observability.comparison import ModelComparison

        comparison = ModelComparison(benchmark_repo=None)
        result = await comparison.compare_models(task_type="code")
        assert result["rankings"] == []
        assert "No benchmark repository" in result["summary"]

    @pytest.mark.asyncio
    async def test_compare_models_repo_error(self):
        from general_ludd.observability.comparison import ModelComparison

        mock_repo = AsyncMock()
        mock_repo.get_aggregate_scores.side_effect = RuntimeError("DB down")
        comparison = ModelComparison(benchmark_repo=mock_repo)
        result = await comparison.compare_models(task_type="code")
        assert result["rankings"] == []
        assert "Error fetching data" in result["summary"]

    @pytest.mark.asyncio
    async def test_compare_models_below_min_samples(self):
        from general_ludd.observability.comparison import ModelComparison

        mock_repo = AsyncMock()
        mock_repo.get_aggregate_scores.return_value = [
            {"model_profile_id": "m1", "prompt_profile_id": "p1", "task_type": "code",
             "sample_count": 1, "composite_score": 0.95, "avg_cost": 0.01,
             "avg_completion": 0.9, "avg_code_quality": 0.9, "avg_instruction": 0.9, "avg_token_efficiency": 0.9},
        ]
        comparison = ModelComparison(benchmark_repo=mock_repo)
        result = await comparison.compare_models(task_type="code", min_samples=2)
        assert "No models with >= 2 samples" in result["summary"]
        assert result["qualified_combinations"] == 0
