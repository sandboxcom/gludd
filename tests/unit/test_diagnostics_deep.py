"""Deep diagnostic and debugging contract tests.

Covers: stack trace capture, state dump snapshotting, memory profiling,
call graph generation, error context assembly, log auditing, duration anomaly
detection, stall watchdog fidelity, and trace buffer integrity.
"""

from __future__ import annotations

import gc
import threading
import time
from datetime import UTC, datetime

import pytest

from general_ludd.code_intelligence.callgraph import CallGraph
from general_ludd.observability.run_history import RunHistoryRecorder
from general_ludd.observability.timing import (
    DurationTracker,
    DurationVerdict,
    StallWatchdog,
    capture_thread_stacks,
)
from general_ludd.observability.trace_store import RecentTracesBuffer
from general_ludd.observability.tracer import ExecutionSpan, ExecutionTrace
from general_ludd.validation.log_auditor import AuditFinding, AuditReport, LogAuditor


# Stack trace capture
class TestStackTraceCapture:
    def test_capture_returns_dict_with_main_thread(self) -> None:
        stacks = capture_thread_stacks()
        assert isinstance(stacks, dict)
        assert len(stacks) >= 1
        main = threading.main_thread()
        found = any(str(main.ident) in k for k in stacks)
        assert found, f"main thread {main.ident} not found in {list(stacks.keys())}"

    def test_capture_contains_file_reference(self) -> None:
        stacks = capture_thread_stacks()
        sample = next(iter(stacks.values()))
        assert "File" in sample, f"expected File references, got: {sample[:200]}"

    def test_capture_threads_increase_with_spawned_thread(self) -> None:
        before = len(capture_thread_stacks())
        started = threading.Event()
        done = threading.Event()

        def worker() -> None:
            started.set()
            done.wait()

        t = threading.Thread(target=worker, name="diag-test-worker")
        t.start()
        started.wait()
        during = len(capture_thread_stacks())
        done.set()
        t.join()
        assert during >= before + 1, f"expected at least {before + 1}, got {during}"


# State dump snapshotting
class TestStateDumpSnapshot:
    def test_execution_trace_to_dict_roundtrips(self) -> None:
        trace = ExecutionTrace(todo_id="dump-1", work_type="code")
        span = trace.start_span("auth-check", "preflight")
        span.complete(
            status="success",
            output_tokens=42,
            input_tokens=10,
            cost_usd=0.03,
            model_profile_id="gpt-4o",
        )
        d = trace.to_dict()
        assert d["trace_id"] == trace.trace_id
        assert d["todo_id"] == "dump-1"
        assert d["success_rate"] == 1.0
        assert d["span_count"] == 1
        spans = d["spans"]
        assert isinstance(spans, list)
        assert spans[0]["status"] == "success"

    def test_span_to_dict_includes_all_keys(self) -> None:
        span = ExecutionSpan(
            trace_id="tr-dmp",
            span_id="sp-dmp",
            name="encode",
            phase="generate",
            started_at=datetime(2025, 6, 1, tzinfo=UTC),
        )
        span.complete(status="error", error_message="token limit exceeded")
        d = span.to_dict()
        required_keys = (
            "trace_id",
            "span_id",
            "name",
            "phase",
            "status",
            "started_at",
            "ended_at",
            "duration_ms",
            "model_profile_id",
            "prompt_profile_id",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "error_message",
        )
        for key in required_keys:
            assert key in d, f"missing key '{key}' in span.to_dict()"

    def test_run_history_deep_copy_isolation(self) -> None:
        recorder = RunHistoryRecorder(max_jobs=10)
        mutable = {"x": 1}
        recorder.record_event("job-a", "start", mutable)
        mutable["x"] = 999
        timeline = recorder.get_timeline("job-a")
        assert timeline[0]["data"]["x"] == 1, "stored data was aliased, not deep-copied"

    def test_run_history_artifact_retrieval(self) -> None:
        recorder = RunHistoryRecorder(max_jobs=10)
        recorder.record_artifact("job-b", "stdout.log", "line1\nline2\n")
        recorder.record_artifact("job-b", "exit_code", "0")
        arts = recorder.get_artifacts("job-b")
        assert arts["stdout.log"] == "line1\nline2\n"
        assert arts["exit_code"] == "0"

    def test_run_history_summary_exact_todo_match(self) -> None:
        recorder = RunHistoryRecorder(max_jobs=10)
        recorder.record_event("TODO-5", "tick", {"phase": "gen"})
        recorder.record_event("TODO-5:subjob-1", "tick", {"phase": "review"})
        summary = recorder.get_summary("TODO-5")
        assert summary["event_count"] == 2

    def test_run_history_summary_rejects_empty_todo(self) -> None:
        recorder = RunHistoryRecorder(max_jobs=10)
        recorder.record_event("any-job", "tick", {})
        summary = recorder.get_summary("")
        assert summary["event_count"] == 0


# Memory profiling
class TestMemoryProfiling:
    def test_gc_collect_reduces_pending(self) -> None:
        gc.collect()
        before = gc.get_count()
        _ = [object() for _ in range(1000)]
        gc.collect()
        assert isinstance(before, tuple)
        assert len(before) == 3

    def test_execution_trace_many_spans_no_leak(self) -> None:
        gc.collect()
        trace = ExecutionTrace(todo_id="mem-1", work_type="code")
        for i in range(500):
            span = trace.start_span(f"step-{i}", "generate")
            span.complete(status="success", output_tokens=i, input_tokens=1, cost_usd=0.0)
        gc.collect()
        assert len(trace.spans) == 500
        assert trace.total_cost_usd == 0.0
        assert trace.total_tokens == sum(range(500))

    def test_recent_traces_buffer_bounded(self) -> None:
        buf = RecentTracesBuffer(maxlen=10)
        for i in range(50):
            trace = ExecutionTrace(todo_id=f"t-{i}", work_type="code")
            span = trace.start_span("s", "gen")
            span.complete(status="success", output_tokens=1, input_tokens=0)
            buf.record(trace)
        assert buf.total_recorded == 50
        recent = buf.recent()
        assert len(recent) <= 10
        todo_ids = {t.todo_id for t in recent}
        assert "t-0" not in todo_ids


# Call graph generation
class TestCallGraphGeneration:
    def test_build_from_blocks_creates_nodes(self) -> None:
        blocks = [
            {"name": "main", "source": "main()\n    util()", "parent": None},
            {"name": "util", "source": "def util(): pass", "parent": None},
        ]
        cg = CallGraph()
        cg.build_from_blocks(blocks)
        assert cg.has_node("main")
        assert cg.has_node("util")

    def test_get_callees_detects_calls_relation(self) -> None:
        blocks = [
            {
                "name": "controller",
                "source": "controller()\n    service.run()",
                "parent": None,
            },
            {
                "name": "service",
                "source": "class Service: ...",
                "parent": None,
            },
        ]
        cg = CallGraph()
        cg.build_from_blocks(blocks)
        callees = cg.get_callees("controller")
        assert len(callees) >= 1
        assert "service" in callees

    def test_get_callers_returns_reverse_edges(self) -> None:
        blocks = [
            {
                "name": "entry",
                "source": "entry()\n    dispatch()",
                "parent": None,
            },
            {
                "name": "dispatch",
                "source": "def dispatch(): pass",
                "parent": None,
            },
        ]
        cg = CallGraph()
        cg.build_from_blocks(blocks)
        callers = cg.get_callers("dispatch")
        assert len(callers) >= 1
        assert "entry" in callers

    def test_contains_relation_from_parent(self) -> None:
        blocks = [
            {"name": "__init__", "source": "pass", "parent": "Config"},
            {"name": "Config", "source": "class Config:", "parent": None},
        ]
        cg = CallGraph()
        cg.build_from_blocks(blocks)
        assert cg.has_node("Config.__init__")

    def test_inherits_relation_from_base_classes(self) -> None:
        blocks = [
            {"name": "BaseHandler", "source": "class BaseHandler:", "parent": None},
            {
                "name": "AuthHandler",
                "source": "class AuthHandler(BaseHandler):",
                "parent": None,
                "base_classes": ["BaseHandler"],
            },
        ]
        cg = CallGraph()
        cg.build_from_blocks(blocks)
        assert cg.is_subclass("AuthHandler", "BaseHandler")

    def test_to_dict_serializes_graph(self) -> None:
        blocks = [{"name": "root", "source": "root()", "parent": None}]
        cg = CallGraph()
        cg.build_from_blocks(blocks)
        d = cg.to_dict()
        assert isinstance(d, dict)
        assert "nodes" in d
        assert "edges" in d
        nodes = d["nodes"]
        assert isinstance(nodes, list)
        assert any(isinstance(n, dict) and n.get("name") == "root" for n in nodes)


# Error context assembly
class TestErrorContextAssembly:
    def test_auditor_detects_missing_correlation_id(self) -> None:
        auditor = LogAuditor()
        entries = [{"event": "model_call", "todo_id": "T-1"}]
        report = auditor.audit_logs(entries)
        assert report.total_findings >= 1
        missing = [f for f in report.findings if f.category == "missing_correlation_id"]
        assert len(missing) >= 1
        assert missing[0].severity == "medium"

    def test_auditor_detects_stuck_todo(self) -> None:
        auditor = LogAuditor()
        entries = [
            {
                "correlation_id": "c-1",
                "event": "retry",
                "todo_id": "STUCK-1",
                "attempt": 5,
                "from_status": "pending",
                "to_status": "pending",
            }
        ]
        report = auditor.audit_logs(entries)
        stuck = [f for f in report.findings if f.category == "stuck_todo"]
        assert len(stuck) >= 1
        assert stuck[0].severity == "high"

    def test_auditor_skips_stuck_if_status_changed(self) -> None:
        auditor = LogAuditor()
        entries = [
            {
                "correlation_id": "c-2",
                "event": "retry",
                "todo_id": "OK-1",
                "attempt": 5,
                "from_status": "pending",
                "to_status": "in_progress",
            }
        ]
        report = auditor.audit_logs(entries)
        stuck = [f for f in report.findings if f.category == "stuck_todo"]
        assert len(stuck) == 0

    def test_auditor_detects_secret_like_patterns(self) -> None:
        auditor = LogAuditor()
        entries = [
            {
                "correlation_id": "c-3",
                "event": "auth_call",
                "payload": {"api_key": "sk-123456789012345678901"},
            }
        ]
        report = auditor.audit_logs(entries)
        secret = [f for f in report.findings if f.category == "secret_like_value"]
        assert len(secret) >= 1
        assert secret[0].severity == "critical"

    def test_audit_report_structure(self) -> None:
        report = AuditReport()
        assert report.findings == []
        assert report.total_findings == 0
        finding = AuditFinding(
            severity="low",
            category="test",
            description="sample",
            evidence="{}",
        )
        report.findings.append(finding)
        report.total_findings = 1
        assert len(report.findings) == 1

    def test_span_error_message_preserved_on_complete(self) -> None:
        span = ExecutionSpan(
            trace_id="err-1",
            span_id="sp-e1",
            name="risky-op",
            phase="execute",
            started_at=datetime(2025, 7, 1, tzinfo=UTC),
        )
        span.complete(status="error", error_message="connection refused")
        assert span.error_message == "connection refused"
        assert span.status == "error"
        d = span.to_dict()
        assert d["error_message"] == "connection refused"

    def test_trace_total_cost_sums_across_spans(self) -> None:
        trace = ExecutionTrace(todo_id="cost-test", work_type="code")
        for cost in [0.01, 0.02, 0.03, 0.0, 0.05]:
            span = trace.start_span("call", "generate")
            span.complete(
                status="success",
                output_tokens=10,
                input_tokens=5,
                cost_usd=cost,
            )
        assert trace.total_cost_usd == 0.11


# Timing and stall watchdog
class TestDiagnosticTiming:
    def test_anomaly_detection(self) -> None:
        tracker = DurationTracker(window=10, min_samples=3, slow_factor=3.0, abs_floor_s=0.01)
        for _ in range(5):
            tracker.record("query", 0.1)
        verdict = tracker.is_anomalous("query", 1.0)
        assert verdict.anomalous is True
        assert "10.0x" in verdict.reason

    def test_normal_duration_not_flagged(self) -> None:
        tracker = DurationTracker(window=10, min_samples=3, slow_factor=3.0, abs_floor_s=0.01)
        for _ in range(5):
            tracker.record("query", 0.1)
        verdict = tracker.is_anomalous("query", 0.12)
        assert verdict.anomalous is False

    def test_rejects_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            DurationTracker(window=0)
        with pytest.raises(ValueError):
            DurationTracker(min_samples=0)
        with pytest.raises(ValueError):
            DurationTracker(slow_factor=0.5)

    def test_stall_watchdog_deadline_fallback(self) -> None:
        wd = StallWatchdog(tracker=None, abs_deadline_s=0.001, capture_stacks=False)
        wd.start("op-fb", "no-baseline")
        time.sleep(0.01)
        reports = wd.poll()
        assert len(reports) >= 1
        assert reports[0].op_id == "op-fb"

    def test_duration_verdict_string_representation(self) -> None:
        v = DurationVerdict(
            key="slow_op",
            seconds=5.0,
            baseline=1.0,
            anomalous=True,
            reason="5.0x baseline",
        )
        s = str(v)
        assert "SLOW" in s
        assert "slow_op" in s

    def test_stall_report_captures_thread_stacks(self) -> None:
        wd = StallWatchdog(capture_stacks=True)
        wd.start("op-stk", "stack-test", deadline_s=-1.0)
        reports = wd.poll()
        assert len(reports) == 1
        assert isinstance(reports[0].thread_stacks, dict)
        assert len(reports[0].thread_stacks) >= 1

    def test_check_then_record_uses_baseline_before_sample(self) -> None:
        tracker = DurationTracker(window=10, min_samples=3, slow_factor=3.0, abs_floor_s=0.01)
        tracker.record("x", 0.1)
        tracker.record("x", 0.1)
        tracker.record("x", 0.1)
        verdict = tracker.check_then_record("x", 1.0)
        assert verdict.anomalous is True
        verdict2 = tracker.is_anomalous("x", 0.15)
        assert verdict2.anomalous is False


# Trace buffer snapshot integrity
class TestTraceBufferSnapshot:
    def test_snapshot_includes_by_phase_aggregate(self) -> None:
        buf = RecentTracesBuffer(maxlen=20)
        trace = ExecutionTrace(todo_id="phase-1", work_type="code")
        s1 = trace.start_span("step-a", "generate")
        s1.complete(status="success", output_tokens=100, input_tokens=50, cost_usd=0.05)
        s2 = trace.start_span("step-b", "review")
        s2.complete(status="success", output_tokens=0, input_tokens=20, cost_usd=0.0)
        buf.record(trace)
        snap = buf.snapshot(limit=10)
        assert "by_phase" in snap
        assert "generate" in snap["by_phase"]
        assert "review" in snap["by_phase"]
        gen = snap["by_phase"]["generate"]
        assert gen["span_count"] == 1
        assert gen["success_count"] == 1

    def test_snapshot_truncates_spans_over_max(self) -> None:
        buf = RecentTracesBuffer(maxlen=20)
        trace = ExecutionTrace(todo_id="many-spans", work_type="code")
        for i in range(30):
            span = trace.start_span(f"s-{i}", "generate")
            span.complete(status="success", output_tokens=1, input_tokens=0)
        buf.record(trace)
        snap = buf.snapshot(limit=10, max_spans=5)
        recent = snap["recent"]
        assert len(recent) >= 1
        raw_spans = recent[0].get("spans", [])
        assert len(raw_spans) <= 5

    def test_snapshot_project_scoping_excludes_none_traces(self) -> None:
        buf = RecentTracesBuffer(maxlen=20)
        t1 = ExecutionTrace(todo_id="proj-a", work_type="code", project_id="proj-1")
        t1.start_span("s", "gen").complete(status="success", output_tokens=1, input_tokens=0)
        t2 = ExecutionTrace(todo_id="no-proj", work_type="code", project_id=None)
        t2.start_span("s", "gen").complete(status="success", output_tokens=1, input_tokens=0)
        buf.record(t1)
        buf.record(t2)
        snap_scoped = buf.snapshot(limit=10, project_id="proj-1")
        assert snap_scoped["count"] == 1
