"""Tests for observability.tracer: ExecutionSpan and ExecutionTrace."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from general_ludd.observability.tracer import ExecutionSpan, ExecutionTrace


class TestExecutionSpan:
    def test_span_initializes_with_required_fields(self):
        now = datetime.now(UTC)
        span = ExecutionSpan(
            trace_id="trace-1",
            span_id="span-1",
            name="test-span",
            phase="generate",
            started_at=now,
        )
        assert span.trace_id == "trace-1"
        assert span.span_id == "span-1"
        assert span.name == "test-span"
        assert span.phase == "generate"
        assert span.status == "running"
        assert span.duration_ms == 0.0

    def test_complete_sets_status_and_timing(self):
        now = datetime.now(UTC)
        span = ExecutionSpan(
            trace_id="trace-1",
            span_id="span-1",
            name="t",
            phase="p",
            started_at=now - timedelta(seconds=2),
        )
        span.complete(status="success", output_tokens=100, input_tokens=50, cost_usd=0.01)
        assert span.status == "success"
        assert span.output_tokens == 100
        assert span.input_tokens == 50
        assert span.cost_usd == 0.01
        assert span.duration_ms >= 1990

    def test_complete_defaults_ended_at_to_now(self):
        span = ExecutionSpan(
            trace_id="t1", span_id="s1", name="t", phase="p", started_at=datetime.now(UTC)
        )
        span.complete()
        assert span.ended_at is not None
        assert span.duration_ms >= 0

    def test_complete_sets_error_message(self):
        span = ExecutionSpan(
            trace_id="t1", span_id="s1", name="t", phase="p", started_at=datetime.now(UTC)
        )
        span.complete(status="error", error_message="something broke")
        assert span.status == "error"
        assert span.error_message == "something broke"

    def test_complete_sets_model_and_prompt_profile_ids(self):
        span = ExecutionSpan(
            trace_id="t1", span_id="s1", name="t", phase="p", started_at=datetime.now(UTC)
        )
        span.complete(model_profile_id="mp-1", prompt_profile_id="pp-1")
        assert span.model_profile_id == "mp-1"
        assert span.prompt_profile_id == "pp-1"

    def test_to_dict_returns_expected_keys(self):
        now = datetime.now(UTC)
        span = ExecutionSpan(
            trace_id="trace-1",
            span_id="span-1",
            name="test",
            phase="generate",
            started_at=now,
        )
        span.complete()
        d = span.to_dict()
        assert d["trace_id"] == "trace-1"
        assert d["span_id"] == "span-1"
        assert d["name"] == "test"
        assert d["phase"] == "generate"
        assert d["status"] == "success"
        assert "started_at" in d
        assert "ended_at" in d
        assert "duration_ms" in d

    def test_to_dict_ended_at_none_when_not_completed(self):
        now = datetime.now(UTC)
        span = ExecutionSpan(
            trace_id="t1", span_id="s1", name="t", phase="p", started_at=now
        )
        d = span.to_dict()
        assert d["ended_at"] is None

    def test_metadata_defaults_to_empty_dict(self):
        span = ExecutionSpan(
            trace_id="t1", span_id="s1", name="t", phase="p", started_at=datetime.now(UTC)
        )
        assert span.metadata == {}


class TestExecutionTrace:
    def test_trace_defaults(self):
        trace = ExecutionTrace(todo_id="todo-1")
        assert trace.todo_id == "todo-1"
        assert trace.work_type == "code"
        assert trace.project_id is None
        assert trace.spans == []

    def test_trace_custom_values(self):
        trace = ExecutionTrace(
            todo_id="todo-2",
            work_type="review",
            trace_id="custom-id",
            project_id="proj-1",
        )
        assert trace.trace_id == "custom-id"
        assert trace.todo_id == "todo-2"
        assert trace.work_type == "review"
        assert trace.project_id == "proj-1"

    def test_trace_generates_trace_id_when_none(self):
        trace = ExecutionTrace(todo_id="t1")
        assert trace.trace_id.startswith("trace-")
        assert len(trace.trace_id) > 6

    def test_start_span_creates_span_linked_to_trace(self):
        trace = ExecutionTrace(todo_id="t1")
        span = trace.start_span("my-span", phase="review")
        assert isinstance(span, ExecutionSpan)
        assert span.trace_id == trace.trace_id
        assert span.name == "my-span"
        assert span.phase == "review"
        assert len(trace.spans) == 1
        assert trace.spans[0] is span

    def test_start_span_default_phase(self):
        trace = ExecutionTrace(todo_id="t1")
        span = trace.start_span("test")
        assert span.phase == "generate"

    def test_total_cost_usd_sums_spans(self):
        trace = ExecutionTrace(todo_id="t1")
        s1 = trace.start_span("a")
        s1.complete(cost_usd=1.5)
        s2 = trace.start_span("b")
        s2.complete(cost_usd=2.5)
        assert trace.total_cost_usd == 4.0

    def test_total_tokens_sums_output_tokens(self):
        trace = ExecutionTrace(todo_id="t1")
        s1 = trace.start_span("a")
        s1.complete(output_tokens=100)
        s2 = trace.start_span("b")
        s2.complete(output_tokens=200)
        assert trace.total_tokens == 300

    def test_total_input_tokens_sums_input_tokens(self):
        trace = ExecutionTrace(todo_id="t1")
        s1 = trace.start_span("a")
        s1.complete(input_tokens=50)
        s2 = trace.start_span("b")
        s2.complete(input_tokens=75)
        assert trace.total_input_tokens == 125

    def test_success_rate_empty_spans(self):
        trace = ExecutionTrace(todo_id="t1")
        assert trace.success_rate == 0.0

    def test_success_rate_all_success(self):
        trace = ExecutionTrace(todo_id="t1")
        for _ in range(4):
            s = trace.start_span("test")
            s.complete(status="success")
        assert trace.success_rate == 1.0

    def test_success_rate_mixed(self):
        trace = ExecutionTrace(todo_id="t1")
        s1 = trace.start_span("a")
        s1.complete(status="success")
        s2 = trace.start_span("b")
        s2.complete(status="error")
        assert trace.success_rate == 0.5

    def test_to_dict_includes_computed_fields(self):
        trace = ExecutionTrace(todo_id="t1")
        trace.start_span("a").complete(cost_usd=1.0, output_tokens=10, input_tokens=5)
        trace.start_span("b").complete(cost_usd=2.0, output_tokens=20, input_tokens=10)
        d = trace.to_dict()
        assert d["todo_id"] == "t1"
        assert d["work_type"] == "code"
        assert d["total_cost_usd"] == 3.0
        assert d["total_tokens"] == 30
        assert d["success_rate"] == 1.0
        assert d["span_count"] == 2
        assert len(d["spans"]) == 2
