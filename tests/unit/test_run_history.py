"""Bug-fix tests for observability/run_history.py (RunHistoryRecorder).

These tests pin four real defects (and their fixes) in the in-memory flight
recorder, keeping the public API back-compatible:

1. `_timeline` was an UNBOUNDED list -> an event flood could OOM. It is now a
   bounded ring buffer (collections.deque with a configurable maxlen).
2. Recorded events lacked any timestamp -> ordering was implicit/unreliable.
   Each event now carries a wall-clock epoch `ts`.
3. `get_summary` matched `todo_id in job_id` (naive substring) -> `'todo-4'`
   leaked into `'todo-42-job'` and `''` aggregated everything. Matching is now
   on a STRUCTURED todo segment (exact), so neither false positive occurs.
4. `get_timeline` / `get_summary` shallow-copied -> inner dicts were shared and
   callers could mutate internal state. They now return DEEP copies.

Pure in-memory recorder — no DB / network.
"""

from __future__ import annotations

import time

from general_ludd.observability.run_history import RunHistoryRecorder


class TestRingBuffer:
    def test_timeline_is_bounded_by_maxlen(self) -> None:
        rec = RunHistoryRecorder(max_events_per_job=5)
        for i in range(50):
            rec.record_event("job-1", f"e{i}", {"i": i})
        timeline = rec.get_timeline("job-1")
        # only the last `maxlen` events survive — no unbounded growth
        assert len(timeline) == 5
        assert [e["event_type"] for e in timeline] == ["e45", "e46", "e47", "e48", "e49"]

    def test_default_maxlen_is_finite(self) -> None:
        rec = RunHistoryRecorder()
        # default bound must be a finite positive int (not None / unbounded)
        assert isinstance(rec.max_events_per_job, int)
        assert rec.max_events_per_job > 0

    def test_maxlen_does_not_drop_when_under_limit(self) -> None:
        rec = RunHistoryRecorder(max_events_per_job=10)
        for i in range(3):
            rec.record_event("job-1", f"e{i}", {})
        assert len(rec.get_timeline("job-1")) == 3


class TestTimestamp:
    def test_event_has_wall_clock_ts(self) -> None:
        before = time.time()
        rec = RunHistoryRecorder()
        rec.record_event("job-1", "start", {})
        after = time.time()
        ev = rec.get_timeline("job-1")[0]
        assert "ts" in ev
        assert isinstance(ev["ts"], float)
        assert before <= ev["ts"] <= after

    def test_ts_is_non_decreasing_in_record_order(self) -> None:
        rec = RunHistoryRecorder()
        for i in range(5):
            rec.record_event("job-1", f"e{i}", {})
        ts_values = [e["ts"] for e in rec.get_timeline("job-1")]
        assert ts_values == sorted(ts_values)


class TestStructuredSummaryMatch:
    def test_no_substring_false_positive(self) -> None:
        rec = RunHistoryRecorder()
        # job id whose todo segment is 'todo-42' must NOT match query 'todo-4'
        rec.record_event("todo-42:job", "e1", {})
        summary = rec.get_summary("todo-4")
        assert summary["event_count"] == 0
        assert summary["events"] == []

    def test_exact_todo_segment_matches(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("todo-4:job-a", "e1", {})
        rec.record_event("todo-4:job-b", "e2", {})
        rec.record_event("todo-42:job-c", "e3", {})
        summary = rec.get_summary("todo-4")
        assert summary["event_count"] == 2
        assert sorted(e["event_type"] for e in summary["events"]) == ["e1", "e2"]

    def test_empty_query_does_not_aggregate_everything(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("todo-1:job", "e1", {})
        rec.record_event("todo-2:job", "e2", {})
        summary = rec.get_summary("")
        assert summary["event_count"] == 0
        assert summary["events"] == []

    def test_explicit_todo_id_param_is_honored(self) -> None:
        rec = RunHistoryRecorder()
        # caller can pass a structured todo_id explicitly, independent of job_id
        rec.record_event("opaque-job-1", "e1", {}, todo_id="todo-7")
        rec.record_event("opaque-job-2", "e2", {}, todo_id="todo-7")
        rec.record_event("opaque-job-3", "e3", {}, todo_id="todo-8")
        summary = rec.get_summary("todo-7")
        assert summary["event_count"] == 2


class TestDeepCopyIsolation:
    def test_get_timeline_inner_dict_is_isolated(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("job-1", "a", {"k": "v"})
        out = rec.get_timeline("job-1")
        out[0]["data"]["k"] = "MUTATED"
        out[0]["event_type"] = "HACKED"
        # internal state must be untouched by mutating the returned structures
        internal = rec.get_timeline("job-1")[0]
        assert internal["data"] == {"k": "v"}
        assert internal["event_type"] == "a"

    def test_get_summary_inner_dict_is_isolated(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("todo-9:job", "a", {"k": "v"})
        summary = rec.get_summary("todo-9")
        summary["events"][0]["data"]["k"] = "MUTATED"
        fresh = rec.get_summary("todo-9")
        assert fresh["events"][0]["data"] == {"k": "v"}
