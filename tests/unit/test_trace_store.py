"""Unit tests for the bounded recent-traces store (observability/trace_store.py).

The store is the HONEST persistence seam for execution traces: ``ExecutionTrace``
objects are captured in-process but were not previously retained anywhere
queryable. ``RecentTracesBuffer`` is a bounded ring that the recorder path
appends to and the /api/facts + /api/traces endpoints read from, so the facts
snapshot reflects genuinely-captured traces (never fabricated spans).
"""

from __future__ import annotations

from general_ludd.observability.trace_store import RecentTracesBuffer
from general_ludd.observability.tracer import ExecutionTrace


def _trace(todo_id: str, phases: list[str]) -> ExecutionTrace:
    t = ExecutionTrace(todo_id=todo_id, work_type="code")
    for i, phase in enumerate(phases):
        span = t.start_span(name=f"{phase}-{i}", phase=phase)
        span.complete(
            status="success",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            model_profile_id="profile-a",
        )
    return t


class TestRecentTracesBuffer:
    def test_record_and_recent(self):
        buf = RecentTracesBuffer(maxlen=10)
        buf.record(_trace("T1", ["generate"]))
        buf.record(_trace("T2", ["plan", "generate"]))
        recent = buf.recent()
        assert len(recent) == 2
        # Most-recent first.
        assert recent[0].todo_id == "T2"
        assert recent[1].todo_id == "T1"

    def test_bounded_ring_drops_oldest(self):
        buf = RecentTracesBuffer(maxlen=3)
        for i in range(5):
            buf.record(_trace(f"T{i}", ["generate"]))
        recent = buf.recent()
        assert len(recent) == 3
        assert [t.todo_id for t in recent] == ["T4", "T3", "T2"]

    def test_recent_limit(self):
        buf = RecentTracesBuffer(maxlen=10)
        for i in range(5):
            buf.record(_trace(f"T{i}", ["generate"]))
        assert len(buf.recent(limit=2)) == 2

    def test_filter_by_todo_id(self):
        buf = RecentTracesBuffer(maxlen=10)
        buf.record(_trace("T1", ["generate"]))
        buf.record(_trace("T2", ["generate"]))
        buf.record(_trace("T1", ["plan"]))
        only_t1 = buf.recent(todo_id="T1")
        assert len(only_t1) == 2
        assert all(t.todo_id == "T1" for t in only_t1)

    def test_snapshot_shape_and_span_cap(self):
        buf = RecentTracesBuffer(maxlen=10)
        buf.record(_trace("T1", ["plan", "generate", "validate"]))
        snap = buf.snapshot(limit=5, max_spans=2)
        assert snap["count"] == 1
        assert snap["total_recorded"] == 1
        rec = snap["recent"][0]
        assert rec["todo_id"] == "T1"
        assert rec["work_type"] == "code"
        assert rec["span_count"] == 3
        assert "trace_id" in rec
        assert "total_cost_usd" in rec
        assert "total_tokens" in rec
        assert "success_rate" in rec
        # Spans bounded to max_spans.
        assert len(rec["spans"]) == 2

    def test_phase_summary_aggregates(self):
        buf = RecentTracesBuffer(maxlen=10)
        buf.record(_trace("T1", ["generate", "generate"]))
        buf.record(_trace("T2", ["generate", "validate"]))
        snap = buf.snapshot()
        phases = snap["by_phase"]
        assert phases["generate"]["span_count"] == 3
        assert phases["validate"]["span_count"] == 1
        # Each generate span cost 0.001 -> 3 * 0.001.
        assert abs(phases["generate"]["total_cost_usd"] - 0.003) < 1e-9
        assert phases["generate"]["total_tokens"] == 150  # 3 * 50 output

    def test_empty_snapshot(self):
        buf = RecentTracesBuffer(maxlen=10)
        snap = buf.snapshot()
        assert snap["count"] == 0
        assert snap["recent"] == []
        assert snap["by_phase"] == {}
        assert snap["total_recorded"] == 0
