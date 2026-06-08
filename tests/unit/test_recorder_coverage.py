from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.observability.recorder import AutoBenchmarkRecorder, compute_scores_from_trace
from general_ludd.observability.tracer import ExecutionSpan, ExecutionTrace


def _make_span(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: float = 0.01,
    model_profile_id: str = "model-1",
    prompt_profile_id: str | None = "prompt-1",
    duration_ms: float = 500.0,
    error_message: str | None = None,
) -> ExecutionSpan:
    span = ExecutionSpan(
        trace_id="trace-abc",
        span_id="span-1",
        name="generate",
        phase="generate",
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    span.output_tokens = output_tokens
    span.input_tokens = input_tokens
    span.cost_usd = cost_usd
    span.model_profile_id = model_profile_id
    span.prompt_profile_id = prompt_profile_id
    span.duration_ms = duration_ms
    span.error_message = error_message
    return span


def _make_trace(spans: list[ExecutionSpan] | None = None) -> ExecutionTrace:
    trace = ExecutionTrace(todo_id="todo-1", work_type="code", trace_id="trace-abc")
    if spans is not None:
        trace.spans = spans
    return trace


class TestComputeScoresFromTrace:
    def test_success_scores(self) -> None:
        trace = _make_trace([_make_span(input_tokens=500)])
        scores = compute_scores_from_trace(trace, success=True)
        assert scores["completion"] == 1.0
        assert scores["code_quality"] == 0.5
        assert scores["instruction"] == 1.0

    def test_failure_scores(self) -> None:
        trace = _make_trace([_make_span(input_tokens=500)])
        scores = compute_scores_from_trace(trace, success=False)
        assert scores["completion"] == 0.0
        assert scores["instruction"] == 0.5

    def test_token_efficiency_with_low_tokens(self) -> None:
        trace = _make_trace([_make_span(input_tokens=500)])
        scores = compute_scores_from_trace(trace, success=True)
        assert scores["token_efficiency"] == min(1.0, 1000.0 / 500.0)

    def test_token_efficiency_with_high_tokens(self) -> None:
        trace = _make_trace([_make_span(input_tokens=10000)])
        scores = compute_scores_from_trace(trace, success=True)
        assert scores["token_efficiency"] == min(1.0, 1000.0 / 10000.0)

    def test_token_efficiency_with_zero_tokens(self) -> None:
        trace = _make_trace([_make_span(input_tokens=0)])
        scores = compute_scores_from_trace(trace, success=True)
        assert scores["token_efficiency"] == min(1.0, 1000.0 / 1.0)

    def test_multiple_spans_tokens_summed(self) -> None:
        trace = _make_trace([_make_span(input_tokens=300), _make_span(input_tokens=200)])
        scores = compute_scores_from_trace(trace, success=True)
        assert scores["token_efficiency"] == min(1.0, 1000.0 / 500.0)


class TestAutoBenchmarkRecorderInit:
    def test_stores_repo(self) -> None:
        repo = MagicMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        assert recorder._repo is repo

    def test_default_repo_is_none(self) -> None:
        recorder = AutoBenchmarkRecorder()
        assert recorder._repo is None


@pytest.mark.asyncio
class TestAutoBenchmarkRecorderRecord:
    async def test_skips_when_no_repo(self) -> None:
        recorder = AutoBenchmarkRecorder(benchmark_repo=None)
        trace = _make_trace([_make_span()])
        await recorder.record_from_trace(trace, success=True)

    async def test_skips_when_no_spans(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        trace = _make_trace([])
        await recorder.record_from_trace(trace, success=True)
        repo.record_result.assert_not_called()

    async def test_records_with_scores(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        span = _make_span()
        trace = _make_trace([span])
        await recorder.record_from_trace(trace, success=True)
        repo.record_result.assert_awaited_once()
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["success"] is True
        assert call_kwargs["scores"]["completion"] == 1.0
        assert call_kwargs["model_profile_id"] == "model-1"
        assert call_kwargs["task_type"] == "code"

    async def test_adjusts_code_quality_from_test_results(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        trace = _make_trace([_make_span()])
        test_results = {"total": 10, "passed": 8}
        await recorder.record_from_trace(trace, success=True, test_results=test_results)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["scores"]["code_quality"] == 0.8

    async def test_code_quality_unchanged_when_no_test_results(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        trace = _make_trace([_make_span()])
        await recorder.record_from_trace(trace, success=True, test_results=None)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["scores"]["code_quality"] == 0.5

    async def test_code_quality_unchanged_when_total_zero(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        trace = _make_trace([_make_span()])
        test_results = {"total": 0, "passed": 0}
        await recorder.record_from_trace(trace, success=True, test_results=test_results)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["scores"]["code_quality"] == 0.5

    async def test_passes_error_message_from_last_span(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        span = _make_span(error_message="boom")
        trace = _make_trace([span])
        await recorder.record_from_trace(trace, success=False)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["error_message"] == "boom"

    async def test_passes_empty_error_when_none(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        span = _make_span(error_message=None)
        trace = _make_trace([span])
        await recorder.record_from_trace(trace, success=True)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["error_message"] == ""

    async def test_handles_repo_exception(self) -> None:
        repo = AsyncMock()
        repo.record_result.side_effect = RuntimeError("db down")
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        trace = _make_trace([_make_span()])
        await recorder.record_from_trace(trace, success=True)

    async def test_passes_time_seconds(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        span = _make_span(duration_ms=2500.0)
        trace = _make_trace([span])
        await recorder.record_from_trace(trace, success=True)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["time_seconds"] == 2.5

    async def test_passes_zero_time_when_duration_zero(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        span = _make_span(duration_ms=0.0)
        trace = _make_trace([span])
        await recorder.record_from_trace(trace, success=True)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["time_seconds"] == 0.0

    async def test_uses_unknown_model_when_none(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        span = _make_span(model_profile_id=None)
        trace = _make_trace([span])
        await recorder.record_from_trace(trace, success=True)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["model_profile_id"] == "unknown"

    async def test_uses_last_span_for_model(self) -> None:
        repo = AsyncMock()
        recorder = AutoBenchmarkRecorder(benchmark_repo=repo)
        span1 = _make_span(model_profile_id="first")
        span2 = _make_span(model_profile_id="second")
        trace = _make_trace([span1, span2])
        await recorder.record_from_trace(trace, success=True)
        call_kwargs = repo.record_result.call_args[1]
        assert call_kwargs["model_profile_id"] == "second"
