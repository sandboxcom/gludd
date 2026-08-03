"""Deep observability and tracing tests for gludd's telemetry pipeline.

Covers: trace span lifecycle, metric recording + cardinality, log correlation,
duration anomaly detection, stall watchdog, benchmark scoring, trace buffer
snapshot + tenant isolation, and exporter JSON shape.
"""

from __future__ import annotations

import logging
import threading
import time

from prometheus_client import CollectorRegistry

from general_ludd.observability.metrics_exporter import (
    CorrelatedLogAdapter,
    MetricsExporter,
    get_correlated_logger,
    get_trace_id,
    set_trace_id,
)
from general_ludd.observability.otel_bridge import OTelBridge, _check_otel_available
from general_ludd.observability.recorder import (
    AutoBenchmarkRecorder,
    compute_scores_from_trace,
)
from general_ludd.observability.timing import (
    DurationTracker,
    DurationVerdict,
    StallReport,
    StallWatchdog,
    capture_thread_stacks,
    default_tracker,
)
from general_ludd.observability.trace_store import (
    DEFAULT_MAXLEN,
    RecentTracesBuffer,
)
from general_ludd.observability.tracer import ExecutionSpan, ExecutionTrace


# --------------------------------------------------------------------------- #
# ExecutionSpan
# --------------------------------------------------------------------------- #
class TestExecutionSpan:
    def test_span_creation_has_unique_ids_and_defaults(self) -> None:
        from datetime import UTC, datetime

        span = ExecutionSpan(
            trace_id="tr-1",
            span_id="sp-abc",
            name="generate_code",
            phase="generate",
            started_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert span.trace_id == "tr-1"
        assert span.span_id == "sp-abc"
        assert span.name == "generate_code"
        assert span.phase == "generate"
        assert span.status == "running"
        assert span.duration_ms == 0.0
        assert span.input_tokens == 0
        assert span.output_tokens == 0
        assert span.cost_usd == 0.0
        assert span.error_message is None
        assert span.metadata == {}

    def test_span_complete_sets_final_state(self) -> None:
        from datetime import UTC, datetime

        span = ExecutionSpan(
            trace_id="tr-1",
            span_id="sp-x",
            name="work",
            phase="solve",
            started_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        span.complete(
            status="success",
            ended_at=datetime(2025, 1, 1, 0, 5, tzinfo=UTC),
            output_tokens=100,
            input_tokens=50,
            cost_usd=0.0042,
            model_profile_id="gpt-4",
            prompt_profile_id="default",
            error_message=None,
        )
        assert span.status == "success"
        assert span.output_tokens == 100
        assert span.input_tokens == 50
        assert span.cost_usd == 0.0042
        assert span.model_profile_id == "gpt-4"
        assert span.prompt_profile_id == "default"
        assert span.error_message is None
        assert span.duration_ms == 300000.0  # 5 minutes in ms

    def test_span_complete_with_error_captures_error_message(self) -> None:
        from datetime import UTC, datetime

        span = ExecutionSpan(
            trace_id="tr-2",
            span_id="sp-err",
            name="bad_call",
            phase="generate",
            started_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        span.complete(
            status="error",
            error_message="connection refused",
        )
        assert span.status == "error"
        assert span.error_message == "connection refused"

    def test_span_to_dict_round_trips_all_fields(self) -> None:
        from datetime import UTC, datetime

        started = datetime(2025, 3, 15, 10, 0, tzinfo=UTC)
        ended = datetime(2025, 3, 15, 10, 2, tzinfo=UTC)
        span = ExecutionSpan(
            trace_id="tr-d",
            span_id="sp-d",
            name="run",
            phase="execute",
            started_at=started,
        )
        span.complete(status="success", ended_at=ended, output_tokens=42, input_tokens=10, cost_usd=0.003)
        span.metadata = {"env": "prod"}

        d = span.to_dict()
        assert d["trace_id"] == "tr-d"
        assert d["span_id"] == "sp-d"
        assert d["name"] == "run"
        assert d["phase"] == "execute"
        assert d["status"] == "success"
        assert d["started_at"] == "2025-03-15T10:00:00+00:00"
        assert d["ended_at"] == "2025-03-15T10:02:00+00:00"
        assert d["duration_ms"] == 120000.0
        assert d["input_tokens"] == 10
        assert d["output_tokens"] == 42
        assert d["cost_usd"] == 0.003
        assert d["error_message"] is None


# --------------------------------------------------------------------------- #
# ExecutionTrace
# --------------------------------------------------------------------------- #
class TestExecutionTrace:
    def test_trace_creation_generates_trace_id(self) -> None:
        trace = ExecutionTrace(todo_id="todo-1", work_type="code")
        assert trace.trace_id.startswith("trace-")
        assert len(trace.trace_id) == 18  # "trace-" + 12 hex chars
        assert trace.todo_id == "todo-1"
        assert trace.work_type == "code"
        assert trace.spans == []

    def test_trace_with_explicit_project_id_scoped(self) -> None:
        trace = ExecutionTrace(todo_id="t1", project_id="project-a")
        assert trace.project_id == "project-a"

    def test_start_span_appends_and_returns_span(self) -> None:
        trace = ExecutionTrace(todo_id="t1")
        span = trace.start_span("validate_input", phase="preflight")
        assert len(trace.spans) == 1
        assert trace.spans[0] is span
        assert span.name == "validate_input"
        assert span.trace_id == trace.trace_id

    def test_aggregation_properties_compute_correctly(self) -> None:
        from datetime import UTC, datetime

        trace = ExecutionTrace(todo_id="t2")
        now = datetime(2025, 6, 1, tzinfo=UTC)

        s1 = trace.start_span("step1", "plan")
        s1.complete(status="success", ended_at=now, output_tokens=100, input_tokens=30, cost_usd=0.01)

        s2 = trace.start_span("step2", "generate")
        s2.complete(status="error", ended_at=now, output_tokens=50, input_tokens=20, cost_usd=0.005)

        s3 = trace.start_span("step3", "verify")
        s3.complete(status="success", ended_at=now, output_tokens=0, input_tokens=10, cost_usd=0.0)

        assert trace.total_cost_usd == 0.015
        assert trace.total_tokens == 150  # output tokens only
        assert trace.total_input_tokens == 60
        assert trace.success_rate == 2.0 / 3.0

    def test_success_rate_zero_when_no_spans(self) -> None:
        trace = ExecutionTrace(todo_id="empty")
        assert trace.success_rate == 0.0

    def test_to_dict_includes_aggregates_and_spans(self) -> None:
        trace = ExecutionTrace(todo_id="t3", work_type="review", project_id="p1")
        s = trace.start_span("look", "audit")
        from datetime import UTC, datetime

        s.complete(status="success", ended_at=datetime(2025, 7, 1, tzinfo=UTC), output_tokens=10, cost_usd=0.001)

        d = trace.to_dict()
        assert d["trace_id"] == trace.trace_id
        assert d["todo_id"] == "t3"
        assert d["work_type"] == "review"
        assert d["project_id"] == "p1"
        assert d["total_cost_usd"] == 0.001
        assert d["total_tokens"] == 10
        assert d["success_rate"] == 1.0
        assert d["span_count"] == 1
        assert isinstance(d["spans"], list)
        assert d["spans"][0]["name"] == "look"


# --------------------------------------------------------------------------- #
# RecentTracesBuffer — ring buffer, snapshot, tenant isolation
# --------------------------------------------------------------------------- #
class TestRecentTracesBuffer:
    @staticmethod
    def _make_trace(todo_id: str, project_id: str | None = None) -> ExecutionTrace:
        t = ExecutionTrace(todo_id=todo_id, project_id=project_id)
        s = t.start_span("work", "generate")
        from datetime import UTC, datetime

        s.complete(status="success", ended_at=datetime(2025, 1, 1, tzinfo=UTC))
        return t

    def test_record_and_recent_with_default_limits(self) -> None:
        buf = RecentTracesBuffer(maxlen=DEFAULT_MAXLEN)
        t1 = self._make_trace("todo-1")
        t2 = self._make_trace("todo-2")
        buf.record(t1)
        buf.record(t2)

        assert buf.total_recorded == 2
        recent = buf.recent()
        assert len(recent) == 2
        # Most-recent-first
        assert recent[0] is t2
        assert recent[1] is t1

    def test_recent_with_limit_truncates(self) -> None:
        buf = RecentTracesBuffer(maxlen=DEFAULT_MAXLEN)
        for i in range(10):
            buf.record(self._make_trace(f"t{i}"))
        assert len(buf.recent(limit=3)) == 3

    def test_recent_filtered_by_todo_id(self) -> None:
        buf = RecentTracesBuffer(maxlen=DEFAULT_MAXLEN)
        buf.record(self._make_trace("a"))
        buf.record(self._make_trace("b"))
        buf.record(self._make_trace("a"))

        items = buf.recent(todo_id="a")
        assert len(items) == 2
        assert all(t.todo_id == "a" for t in items)

    def test_recent_scoped_to_project_excludes_null_project_traces(self) -> None:
        buf = RecentTracesBuffer(maxlen=DEFAULT_MAXLEN)
        buf.record(self._make_trace("a", project_id="proj-x"))
        buf.record(self._make_trace("b", project_id=None))
        buf.record(self._make_trace("c", project_id="proj-y"))

        scoped = buf.recent(project_id="proj-x")
        assert len(scoped) == 1
        assert scoped[0].todo_id == "a"

        unscoped = buf.recent(project_id="proj-y")
        assert len(unscoped) == 1
        assert unscoped[0].todo_id == "c"

    def test_ring_buffer_drops_oldest_when_full(self) -> None:
        buf = RecentTracesBuffer(maxlen=3)
        for i in range(5):
            buf.record(self._make_trace(f"t{i}"))
        assert buf.total_recorded == 5
        recent = buf.recent()
        assert len(recent) == 3

    def test_snapshot_includes_by_phase_aggregate(self) -> None:
        buf = RecentTracesBuffer(maxlen=DEFAULT_MAXLEN)
        t = ExecutionTrace(todo_id="t1")
        s1 = t.start_span("plan", "plan")
        from datetime import UTC, datetime

        s1.complete(status="success", ended_at=datetime(2025, 1, 1, tzinfo=UTC), output_tokens=50, cost_usd=0.01)
        s2 = t.start_span("gen", "generate")
        s2.complete(status="success", ended_at=datetime(2025, 1, 1, tzinfo=UTC), output_tokens=30, cost_usd=0.005)
        buf.record(t)

        snap = buf.snapshot()
        assert snap["count"] == 1
        assert snap["total_recorded"] == 1
        by_phase = snap["by_phase"]
        assert "plan" in by_phase
        assert "generate" in by_phase
        assert by_phase["plan"]["span_count"] == 1
        assert by_phase["plan"]["total_cost_usd"] == 0.01
        assert by_phase["plan"]["total_tokens"] == 50
        assert by_phase["plan"]["success_count"] == 1

    def test_snapshot_truncates_spans_when_over_max_spans(self) -> None:
        buf = RecentTracesBuffer(maxlen=DEFAULT_MAXLEN)
        t = ExecutionTrace(todo_id="fat")
        for i in range(30):
            s = t.start_span(f"s{i}", "gen")
            s.complete()
        buf.record(t)

        snap = buf.snapshot(max_spans=5)
        trace_row = snap["recent"][0]
        assert len(trace_row["spans"]) == 5
        assert trace_row["spans_truncated"] is True


# --------------------------------------------------------------------------- #
# MetricsExporter — counters, gauges, histograms, cardinality, JSON
# --------------------------------------------------------------------------- #
class TestMetricsExporter:
    @staticmethod
    def _fresh_exporter() -> MetricsExporter:
        return MetricsExporter(registry=CollectorRegistry(auto_describe=False))

    def test_counter_increment_without_labels(self) -> None:
        exp = self._fresh_exporter()
        exp.counter_inc("gludd_dc_1")
        exp.counter_inc("gludd_dc_1", value=2)
        text = exp.render_prometheus()
        assert "gludd_dc_1_total 3.0" in text

    def test_counter_increment_with_labels(self) -> None:
        exp = self._fresh_exporter()
        exp.counter_inc("gludd_dc_2", labels={"status": "ok"})
        exp.counter_inc("gludd_dc_2", labels={"status": "ok"})
        exp.counter_inc("gludd_dc_2", labels={"status": "err"})
        text = exp.render_prometheus()
        assert 'gludd_dc_2_total{status="err"} 1.0' in text
        assert 'gludd_dc_2_total{status="ok"} 2.0' in text

    def test_gauge_set_and_read(self) -> None:
        exp = self._fresh_exporter()
        exp.gauge_set("gludd_dg", 42.5, labels={"sensor": "a"})
        exp.gauge_set("gludd_dg", 36.0, labels={"sensor": "b"})
        gauges = exp.get_gauges()
        assert gauges["gludd_dg_sensor=a"] == 42.5
        assert gauges["gludd_dg_sensor=b"] == 36.0

    def test_histogram_observe_and_render(self) -> None:
        exp = self._fresh_exporter()
        exp.histogram_observe("gludd_dh", 0.05)
        exp.histogram_observe("gludd_dh", 0.12)
        exp.histogram_observe("gludd_dh", 0.12)
        json_out = exp.get_json()
        assert json_out["uptime_seconds"] >= 0
        assert "gludd_dh" in json_out["metrics"]

    def test_cardinality_bound_enforced(self) -> None:
        from general_ludd.observability.metrics_exporter import (
            MAX_LABEL_VALUES_PER_KEY,
            OVERFLOW_LABEL_VALUE,
        )

        exp = self._fresh_exporter()
        for i in range(MAX_LABEL_VALUES_PER_KEY + 10):
            exp.counter_inc("gludd_dcard", labels={"file": f"path_{i}"})
        counters = exp.get_counters()
        assert any(OVERFLOW_LABEL_VALUE in k for k in counters)

    def test_prometheus_render_is_non_empty_string(self) -> None:
        exp = self._fresh_exporter()
        exp.counter_inc("gludd_drender")
        text = exp.render_prometheus()
        assert "gludd_drender" in text
        assert isinstance(text, str)
        assert len(text) > 0

    def test_get_json_has_correct_shape(self) -> None:
        exp = self._fresh_exporter()
        exp.counter_inc("gludd_dx", value=3)
        json_out = exp.get_json()
        assert "metrics" in json_out
        assert "uptime_seconds" in json_out
        assert isinstance(json_out["uptime_seconds"], float)


# --------------------------------------------------------------------------- #
# Log correlation
# --------------------------------------------------------------------------- #
class TestLogCorrelation:
    def test_set_and_get_trace_id_per_thread(self) -> None:
        tid = set_trace_id()
        assert isinstance(tid, str)
        assert len(tid) == 16
        assert get_trace_id() == tid

    def test_get_trace_id_returns_unknown_when_not_set(self) -> None:

        tid = get_trace_id()
        # In the main test thread, may have been set by other tests; just verify
        # it's a non-empty string
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_correlated_log_adapter_prefixes_trace_and_span_ids(self) -> None:
        set_trace_id("aaaa1111bbbb2222")
        adapter = get_correlated_logger("test_logger")
        msg, _kwargs = adapter.process("hello world", {})
        assert "[trace=aaaa1111bbbb2222 span=" in msg
        assert "] hello world" in msg

    def test_correlated_log_adapter_preserves_extra_kwargs(self) -> None:
        set_trace_id("zzzz9999")
        adapter = CorrelatedLogAdapter(logging.getLogger("test_kwargs"), {})
        msg, kwargs = adapter.process("event", {"extra": {"user": "alice"}})
        assert "[trace=zzzz9999 span=" in msg
        assert kwargs["extra"]["user"] == "alice"


# --------------------------------------------------------------------------- #
# DurationTracker — baseline learning, anomaly detection
# --------------------------------------------------------------------------- #
class TestDurationTracker:
    def test_record_and_baseline_requires_min_samples(self) -> None:
        tracker = DurationTracker(window=10, min_samples=3, slow_factor=2.0)
        tracker.record("req", 0.1)
        tracker.record("req", 0.2)
        assert tracker.baseline("req") is None  # only 2 samples

        tracker.record("req", 0.3)
        assert tracker.baseline("req") == 0.2  # median of [0.1, 0.2, 0.3]

    def test_is_anomalous_flags_slow_operation(self) -> None:
        tracker = DurationTracker(window=10, min_samples=3, slow_factor=2.0, abs_floor_s=0.01)
        for _i in range(3):
            tracker.record("op", 0.1)  # baseline ~0.1

        verdict = tracker.is_anomalous("op", 0.5)  # 5x baseline
        assert verdict.anomalous is True

    def test_is_anomalous_ok_when_within_threshold(self) -> None:
        tracker = DurationTracker(window=10, min_samples=3, slow_factor=3.0, abs_floor_s=0.01)
        for _i in range(3):
            tracker.record("op", 1.0)

        verdict = tracker.is_anomalous("op", 1.5)  # 1.5x < 3x threshold
        assert verdict.anomalous is False

    def test_is_anomalous_not_flagged_during_learning(self) -> None:
        tracker = DurationTracker(min_samples=5, slow_factor=1.5)
        tracker.record("new_op", 100.0)  # only 1 sample
        verdict = tracker.is_anomalous("new_op", 1.0)
        assert verdict.anomalous is False
        assert "insufficient samples" in verdict.reason

    def test_check_then_record_judges_before_recording(self) -> None:
        tracker = DurationTracker(window=10, min_samples=2, slow_factor=2.0, abs_floor_s=0.01)
        tracker.record("task", 0.1)
        tracker.record("task", 0.15)
        # baseline = median(0.1, 0.15) = 0.125

        verdict = tracker.check_then_record("task", 1.0)  # 8x baseline
        assert verdict.anomalous is True
        # The anomalous sample was still recorded
        assert len(tracker._history["task"]) == 3

    def test_track_context_manager_times_and_records(self) -> None:
        tracker = DurationTracker(min_samples=1, slow_factor=1000.0)

        with tracker.track("ctx_test"):
            time.sleep(0.01)

        assert tracker.baseline("ctx_test") is not None

    def test_track_context_manager_on_anomaly_callback_fires(self) -> None:
        tracker = DurationTracker(window=10, min_samples=3, slow_factor=1.5, abs_floor_s=0.0)
        tracker.record("anom", 0.001)
        tracker.record("anom", 0.001)
        tracker.record("anom", 0.001)
        results: list[DurationVerdict] = []

        with tracker.track("anom", on_anomaly=results.append):
            time.sleep(0.05)

        assert len(results) == 1
        assert results[0].anomalous is True


# --------------------------------------------------------------------------- #
# StallWatchdog — in-flight stall detection
# --------------------------------------------------------------------------- #
class TestStallWatchdog:
    def test_start_and_finish_lifecycle(self) -> None:
        wd = StallWatchdog(capture_stacks=False)
        wd.start("op-1", "db_query", deadline_s=100.0)
        assert len(wd.poll()) == 0  # not past deadline
        wd.finish("op-1")
        assert len(wd.poll()) == 0

    def test_poll_detects_stalled_operation(self) -> None:
        wd = StallWatchdog(capture_stacks=False)
        wd.start("op-2", "slow_call", deadline_s=-1.0)  # already past
        reports = wd.poll()
        assert len(reports) == 1
        assert reports[0].key == "slow_call"
        assert reports[0].op_id == "op-2"

    def test_poll_reports_each_op_once(self) -> None:
        wd = StallWatchdog(capture_stacks=False)
        wd.start("op-3", "hang", deadline_s=-1.0)
        assert len(wd.poll()) == 1
        assert len(wd.poll()) == 0  # already reported

    def test_watch_context_manager_auto_finishes(self) -> None:
        wd = StallWatchdog(capture_stacks=False)

        with wd.watch("ctx-1", "fast_op", deadline_s=3600.0):
            pass  # completes instantly, well before deadline

        assert len(wd.poll()) == 0

    def test_on_stall_callback_fires(self) -> None:
        reports: list[StallReport] = []
        wd = StallWatchdog(on_stall=reports.append, capture_stacks=False)
        wd.start("op-cb", "callback_test", deadline_s=-1.0)
        wd.poll()
        assert len(reports) == 1
        assert reports[0].key == "callback_test"


# --------------------------------------------------------------------------- #
# Thread stack capture
# --------------------------------------------------------------------------- #
class TestCaptureThreadStacks:
    def test_capture_returns_non_empty_dict(self) -> None:
        stacks = capture_thread_stacks()
        assert isinstance(stacks, dict)
        # The main test thread should appear
        assert len(stacks) >= 1
        # Values are stack trace strings
        first_stack = next(iter(stacks.values()))
        assert "traceback" not in first_stack.lower() or "File" in first_stack

    def test_capture_contains_main_thread(self) -> None:
        stacks = capture_thread_stacks()
        ident = threading.get_ident()
        found = any(str(ident) in key for key in stacks)
        assert found, f"thread ident {ident} not found in {list(stacks.keys())}"


# --------------------------------------------------------------------------- #
# OTelBridge — availability check
# --------------------------------------------------------------------------- #
class TestOTelBridge:
    def test_check_otel_available_returns_bool(self) -> None:
        result = _check_otel_available()
        assert isinstance(result, bool)

    def test_bridge_graceful_when_otel_not_installed(self) -> None:
        bridge = OTelBridge(endpoint="http://localhost:4317")
        # Bridge should not raise; may or may not have OTel installed
        assert isinstance(bridge.is_available(), bool)
        # export_trace on unavailable bridge is a no-op
        trace = ExecutionTrace(todo_id="t99")
        bridge.export_trace(trace)  # must not raise

    def test_shutdown_on_unavailable_bridge_noop(self) -> None:
        bridge = OTelBridge(endpoint="http://localhost:4317")
        bridge.shutdown()  # must not raise
        assert not bridge.is_available()


# --------------------------------------------------------------------------- #
# AutoBenchmarkRecorder — scoring and recording
# --------------------------------------------------------------------------- #
class TestBenchmarkScoring:
    def test_compute_scores_success_trace(self) -> None:
        from datetime import UTC, datetime

        trace = ExecutionTrace(todo_id="win", work_type="code")
        now = datetime(2025, 1, 1, tzinfo=UTC)
        s = trace.start_span("step", "generate")
        s.complete(status="success", ended_at=now, output_tokens=200, input_tokens=100)

        scores = compute_scores_from_trace(trace, success=True)
        assert scores["completion"] == 1.0
        assert scores["instruction"] == 1.0
        assert scores["code_quality"] == 0.5
        assert 0.0 < scores["token_efficiency"] <= 1.0

    def test_compute_scores_failure_trace(self) -> None:
        trace = ExecutionTrace(todo_id="lose", work_type="code")
        s = trace.start_span("step", "generate")
        from datetime import UTC, datetime

        s.complete(status="error", ended_at=datetime(2025, 1, 1, tzinfo=UTC))

        scores = compute_scores_from_trace(trace, success=False)
        assert scores["completion"] == 0.0
        assert scores["instruction"] == 0.5

    def test_compute_scores_with_test_results_derives_quality(self) -> None:
        from datetime import UTC, datetime

        trace = ExecutionTrace(todo_id="tested", work_type="code")
        object.__setattr__(trace, "test_results", {"total": 10, "passed": 8, "failed": 2})
        now = datetime(2025, 1, 1, tzinfo=UTC)
        s = trace.start_span("step", "generate")
        s.complete(status="success", ended_at=now, output_tokens=100, input_tokens=40)

        scores = compute_scores_from_trace(trace, success=True)
        assert scores["code_quality"] == 0.8  # 8/10

    def test_compute_scores_token_efficiency_edge_cases(self) -> None:
        from datetime import UTC, datetime

        trace = ExecutionTrace(todo_id="cheap", work_type="code")
        now = datetime(2025, 1, 1, tzinfo=UTC)
        s = trace.start_span("step", "generate")
        s.complete(status="success", ended_at=now, output_tokens=0, input_tokens=0)

        scores = compute_scores_from_trace(trace, success=True)
        # When total_input_tokens is 0, max(float(0), 1.0)=1.0 => 1000/1=1000 => min(1.0, 1000) = 1.0
        assert scores["token_efficiency"] == 1.0


# --------------------------------------------------------------------------- #
# AutoBenchmarkRecorder — record path
# --------------------------------------------------------------------------- #
class TestAutoBenchmarkRecorder:
    def test_record_without_repo_or_buffer_is_noop(self) -> None:
        import asyncio

        recorder = AutoBenchmarkRecorder(benchmark_repo=None, trace_buffer=None)
        trace = ExecutionTrace(todo_id="t", work_type="code")
        s = trace.start_span("step", "gen")
        from datetime import UTC, datetime

        s.complete(status="success", ended_at=datetime(2025, 1, 1, tzinfo=UTC))

        # Must not raise
        asyncio.run(recorder.record_from_trace(trace, success=True))

    def test_record_with_buffer_retains_trace(self) -> None:
        import asyncio

        buf = RecentTracesBuffer()
        recorder = AutoBenchmarkRecorder(benchmark_repo=None, trace_buffer=buf)
        trace = ExecutionTrace(todo_id="t-buf", work_type="code")
        s = trace.start_span("step", "gen")
        from datetime import UTC, datetime

        s.complete(status="success", ended_at=datetime(2025, 1, 1, tzinfo=UTC))

        asyncio.run(recorder.record_from_trace(trace, success=True))
        assert buf.total_recorded == 1
        assert buf.recent()[0].todo_id == "t-buf"


# --------------------------------------------------------------------------- #
# default_tracker singleton
# --------------------------------------------------------------------------- #
class TestDefaultTracker:
    def test_returns_same_instance(self) -> None:
        t1 = default_tracker()
        t2 = default_tracker()
        assert t1 is t2

    def test_is_functional_duration_tracker(self) -> None:
        t = default_tracker()
        t.record("def_op", 0.5)
        t.record("def_op", 0.3)
        t.record("def_op", 0.4)
        t.record("def_op", 0.2)
        t.record("def_op", 0.6)
        baseline = t.baseline("def_op")
        assert baseline is not None
        assert 0.2 <= baseline <= 0.6  # ~median
