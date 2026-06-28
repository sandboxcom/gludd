"""Adversarial unit tests for RunHistoryRecorder.

Target: src/general_ludd/observability/run_history.py

These tests assert the CURRENT, real behavior of the recorder. Where the
module docstring / task framing implies a feature that does NOT exist
(ring-buffer bounds, timestamps, concurrency locks), the test documents the
actual behavior and an ``xfail`` marks the genuine gap so it is reported
rather than silently hidden.
"""

from __future__ import annotations

import threading

from general_ludd.observability.run_history import RunHistoryRecorder

# --------------------------------------------------------------------------
# record_event / get_timeline
# --------------------------------------------------------------------------

def test_record_event_creates_timeline_entry() -> None:
    rec = RunHistoryRecorder()
    rec.record_event("job-1", "model_call", {"tokens": 42})
    tl = rec.get_timeline("job-1")
    assert len(tl) == 1
    ev = tl[0]
    assert ev["event_type"] == "model_call"
    assert ev["data"] == {"tokens": 42}
    # Events now carry a wall-clock timestamp.
    assert "ts" in ev


def test_get_timeline_unknown_job_returns_empty_list() -> None:
    rec = RunHistoryRecorder()
    assert rec.get_timeline("does-not-exist") == []


def test_get_timeline_returns_a_copy_not_internal_list() -> None:
    # get_timeline does list(...) so mutating the result must not corrupt state.
    rec = RunHistoryRecorder()
    rec.record_event("job-1", "a", {})
    tl = rec.get_timeline("job-1")
    tl.append({"event_type": "INJECTED", "data": {}})
    assert len(rec.get_timeline("job-1")) == 1


def test_get_timeline_returns_deep_copy_inner_dict_is_isolated() -> None:
    # get_timeline now returns a deep copy — the per-event dicts (and their
    # nested ``data``) are independent objects. Mutating the returned record
    # must NOT corrupt stored history.
    rec = RunHistoryRecorder()
    payload = {"k": "v"}
    rec.record_event("job-1", "a", payload)
    leaked = rec.get_timeline("job-1")[0]["data"]
    leaked["k"] = "TAMPERED"
    # Internal state is isolated — deep copy means the mutation cannot reach in.
    assert rec.get_timeline("job-1")[0]["data"]["k"] == "v"


def test_events_preserve_insertion_order() -> None:
    rec = RunHistoryRecorder()
    for i in range(10):
        rec.record_event("job-1", f"evt{i}", {"i": i})
    types = [e["event_type"] for e in rec.get_timeline("job-1")]
    assert types == [f"evt{i}" for i in range(10)]


def test_duplicate_events_are_all_kept() -> None:
    rec = RunHistoryRecorder()
    rec.record_event("job-1", "same", {"x": 1})
    rec.record_event("job-1", "same", {"x": 1})
    assert len(rec.get_timeline("job-1")) == 2


def test_empty_job_id_is_a_valid_key() -> None:
    rec = RunHistoryRecorder()
    rec.record_event("", "evt", {})
    assert len(rec.get_timeline("")) == 1


def test_events_for_distinct_jobs_are_isolated() -> None:
    rec = RunHistoryRecorder()
    rec.record_event("job-a", "x", {})
    rec.record_event("job-b", "y", {})
    assert len(rec.get_timeline("job-a")) == 1
    assert len(rec.get_timeline("job-b")) == 1


# --------------------------------------------------------------------------
# Ring-buffer bounds — DOES NOT EXIST (gap report)
# --------------------------------------------------------------------------

def test_timeline_is_currently_unbounded() -> None:
    # CURRENT behavior: there is no ring buffer; the list grows without limit.
    rec = RunHistoryRecorder()
    for i in range(5000):
        rec.record_event("flood", "evt", {"i": i})
    assert len(rec.get_timeline("flood")) == 5000


def test_timeline_is_bounded_by_a_ring_buffer() -> None:
    # FIXED: RunHistoryRecorder now uses a bounded deque (max_events_per_job).
    # Flooding the recorder respects the cap — no unbounded OOM growth.
    rec = RunHistoryRecorder(max_events_per_job=50)
    for i in range(100_000):
        rec.record_event("flood", "evt", {"i": i})
    assert len(rec.get_timeline("flood")) == 50


def test_job_count_is_bounded_evicting_oldest() -> None:
    # The per-job deque caps events WITHIN a job; this caps the NUMBER of jobs.
    # Flooding distinct job_ids must not grow the maps without bound — the oldest
    # job (by first sighting) is evicted from timeline + artifacts + todo together.
    rec = RunHistoryRecorder(max_jobs=10)
    for i in range(1000):
        jid = f"JOB-{i}"
        rec.record_event(jid, "evt", {"i": i}, todo_id=f"TODO-{i}")
        rec.record_artifact(jid, "log", f"content-{i}")

    # Only the last 10 jobs are retained across all three maps.
    assert len(rec._timeline) == 10
    assert len(rec._artifacts) == 10
    assert len(rec._job_todo) == 10
    assert len(rec._job_order) == 10
    assert len(rec._known_jobs) == 10
    # The oldest job is gone; the newest survives.
    assert rec.get_timeline("JOB-0") == []
    assert rec.get_artifacts("JOB-0") == {}
    assert rec.get_timeline("JOB-999") != []
    assert rec.get_artifacts("JOB-999") == {"log": "content-999"}


def test_reseen_job_keeps_fifo_position_no_duplicate_growth() -> None:
    # Re-recording an existing job must not append a duplicate order entry or
    # inflate the tracked-job count.
    rec = RunHistoryRecorder(max_jobs=5)
    for _ in range(100):
        rec.record_event("STABLE", "evt", {})
    assert len(rec._job_order) == 1
    assert len(rec._known_jobs) == 1
    assert list(rec._job_order) == ["STABLE"]


# --------------------------------------------------------------------------
# Timestamps / ordering — NOT RECORDED (gap report)
# --------------------------------------------------------------------------

def test_events_carry_a_wall_clock_timestamp() -> None:
    # FIXED: events now include a float wall-clock epoch `ts`.
    rec = RunHistoryRecorder()
    rec.record_event("job-1", "evt", {})
    stored = rec.get_timeline("job-1")[0]
    assert "ts" in stored
    assert isinstance(stored["ts"], float)
    assert set(stored.keys()) == {"event_type", "data", "ts"}


# --------------------------------------------------------------------------
# Concurrency — no lock; assert it survives concurrent appends to distinct
# and shared jobs (CPython list.append is atomic under the GIL).
# --------------------------------------------------------------------------

def test_concurrent_record_distinct_jobs_no_loss() -> None:
    rec = RunHistoryRecorder()
    n_threads = 8
    per_thread = 500

    def worker(tid: int) -> None:
        for i in range(per_thread):
            rec.record_event(f"job-{tid}", "evt", {"i": i})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for t in range(n_threads):
        assert len(rec.get_timeline(f"job-{t}")) == per_thread


def test_concurrent_record_shared_job_no_loss() -> None:
    # All threads write to the SAME job_id. list.append under the GIL is
    # atomic, so no events should be lost even without an explicit lock.
    rec = RunHistoryRecorder()
    n_threads = 8
    per_thread = 500

    def worker() -> None:
        for i in range(per_thread):
            rec.record_event("shared", "evt", {"i": i})

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(rec.get_timeline("shared")) == n_threads * per_thread


# --------------------------------------------------------------------------
# record_artifact / get_artifacts
# --------------------------------------------------------------------------

def test_record_artifact_and_retrieve() -> None:
    rec = RunHistoryRecorder()
    rec.record_artifact("job-1", "diff.txt", "patch-content")
    assert rec.get_artifacts("job-1") == {"diff.txt": "patch-content"}


def test_artifact_same_name_overwrites() -> None:
    rec = RunHistoryRecorder()
    rec.record_artifact("job-1", "a", "first")
    rec.record_artifact("job-1", "a", "second")
    assert rec.get_artifacts("job-1") == {"a": "second"}


def test_get_artifacts_unknown_job_empty_dict() -> None:
    rec = RunHistoryRecorder()
    assert rec.get_artifacts("nope") == {}


def test_get_artifacts_returns_a_copy() -> None:
    rec = RunHistoryRecorder()
    rec.record_artifact("job-1", "a", "x")
    out = rec.get_artifacts("job-1")
    out["injected"] = "y"
    assert "injected" not in rec.get_artifacts("job-1")


def test_artifacts_and_timeline_are_independent_stores() -> None:
    rec = RunHistoryRecorder()
    rec.record_event("job-1", "evt", {})
    assert rec.get_artifacts("job-1") == {}
    rec.record_artifact("job-2", "a", "x")
    assert rec.get_timeline("job-2") == []


# --------------------------------------------------------------------------
# get_summary — substring matching is the adversarial surface
# --------------------------------------------------------------------------

def test_get_summary_aggregates_matching_jobs() -> None:
    # Job IDs use the "todo_id:subjob" colon-separator convention so that
    # get_summary can do exact-prefix matching rather than substring matching.
    rec = RunHistoryRecorder()
    rec.record_event("todo-42:job-1", "a", {})
    rec.record_event("todo-42:job-2", "b", {})
    summary = rec.get_summary("todo-42")
    assert summary["todo_id"] == "todo-42"
    assert summary["event_count"] == 2
    assert {e["event_type"] for e in summary["events"]} == {"a", "b"}


def test_get_summary_unknown_todo_is_empty() -> None:
    rec = RunHistoryRecorder()
    rec.record_event("todo-1-job", "a", {})
    summary = rec.get_summary("todo-999")
    assert summary["event_count"] == 0
    assert summary["events"] == []


def test_get_summary_no_substring_false_positive() -> None:
    # FIXED: matching is now exact/prefix-boundary, not substring.
    # "todo-4" must NOT match job "todo-42:job" (different todo segment).
    rec = RunHistoryRecorder()
    rec.record_event("todo-42:job", "wrong", {})
    summary = rec.get_summary("todo-4")
    # FIXED behavior: "todo-4" does NOT match "todo-42:job" — no false hit.
    assert summary["event_count"] == 0


def test_get_summary_empty_todo_id_returns_nothing() -> None:
    # FIXED: "" is rejected by get_summary (not substring-matched against every key).
    # An empty query no longer aggregates all jobs' events.
    rec = RunHistoryRecorder()
    rec.record_event("job-a", "x", {})
    rec.record_event("job-b", "y", {})
    summary = rec.get_summary("")
    assert summary["event_count"] == 0
    assert summary["events"] == []


def test_get_summary_events_are_deep_copied_not_shared() -> None:
    # FIXED: get_summary now returns deep copies — mutating the returned event
    # dict does NOT corrupt the internal stored history.
    rec = RunHistoryRecorder()
    rec.record_event("todo-1:job", "a", {"flag": False})
    summary = rec.get_summary("todo-1")
    summary["events"][0]["data"]["flag"] = True
    # Internal state is unaffected by the mutation.
    assert rec.get_timeline("todo-1:job")[0]["data"]["flag"] is False
