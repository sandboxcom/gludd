"""Deep tests for run_history.py — event/artifact recording, summary, eviction."""

from __future__ import annotations

import time

from general_ludd.observability.run_history import RunHistoryRecorder


class TestRecordEvent:
    def test_record_event_stores_with_timestamp(self):
        r = RunHistoryRecorder()
        r.record_event("job-1", "model_call", {"model": "gpt-4"})
        timeline = r.get_timeline("job-1")
        assert len(timeline) == 1
        assert timeline[0]["event_type"] == "model_call"
        assert timeline[0]["data"] == {"model": "gpt-4"}
        assert "ts" in timeline[0]

    def test_record_event_with_todo_id_stores_override(self):
        r = RunHistoryRecorder()
        r.record_event("job-1", "dispatch", {"target": "lint"}, todo_id="TODO-42")
        r.record_event("job-2", "commit", {"sha": "abc"}, todo_id="TODO-99")
        summary = r.get_summary("TODO-42")
        assert summary["event_count"] == 1

    def test_record_event_deep_copies_data(self):
        r = RunHistoryRecorder()
        mutable = {"count": 0}
        r.record_event("job-1", "tick", mutable)
        mutable["count"] = 999
        stored = r.get_timeline("job-1")[0]
        assert stored["data"]["count"] == 0

    def test_record_event_multiple_jobs_independent(self):
        r = RunHistoryRecorder()
        r.record_event("j-1", "a", {"x": 1})
        r.record_event("j-2", "b", {"x": 2})
        assert len(r.get_timeline("j-1")) == 1
        assert len(r.get_timeline("j-2")) == 1

    def test_record_event_respects_max_events_per_job(self):
        r = RunHistoryRecorder(max_events_per_job=3)
        for i in range(5):
            r.record_event("job", f"evt-{i}", {})
        timeline = r.get_timeline("job")
        assert len(timeline) == 3
        assert timeline[0]["event_type"] == "evt-2"
        assert timeline[-1]["event_type"] == "evt-4"


class TestRecordArtifact:
    def test_record_artifact_stores_content(self):
        r = RunHistoryRecorder()
        r.record_artifact("job-1", "report.txt", "PASS")
        arts = r.get_artifacts("job-1")
        assert arts["report.txt"] == "PASS"

    def test_record_artifact_multiple_per_job(self):
        r = RunHistoryRecorder()
        r.record_artifact("job-1", "a.txt", "a")
        r.record_artifact("job-1", "b.txt", "b")
        arts = r.get_artifacts("job-1")
        assert len(arts) == 2

    def test_record_artifact_overwrite(self):
        r = RunHistoryRecorder()
        r.record_artifact("job-1", "out.txt", "v1")
        r.record_artifact("job-1", "out.txt", "v2")
        assert r.get_artifacts("job-1")["out.txt"] == "v2"

    def test_get_artifacts_unknown_job_returns_empty(self):
        r = RunHistoryRecorder()
        assert r.get_artifacts("nonexistent") == {}

    def test_get_artifacts_returns_copy_not_alias(self):
        r = RunHistoryRecorder()
        r.record_artifact("job-1", "f", "val")
        arts = r.get_artifacts("job-1")
        arts["f"] = "mutated"
        assert r.get_artifacts("job-1")["f"] == "val"


class TestGetTimeline:
    def test_get_timeline_unknown_job_returns_empty(self):
        r = RunHistoryRecorder()
        assert r.get_timeline("nonexistent") == []

    def test_get_timeline_returns_deep_copy(self):
        r = RunHistoryRecorder()
        r.record_event("job-1", "e", {"k": "v"})
        timeline = r.get_timeline("job-1")
        timeline[0]["data"]["k"] = "mutated"
        stored = r.get_timeline("job-1")[0]
        assert stored["data"]["k"] == "v"

    def test_get_timeline_chronological_order(self):
        r = RunHistoryRecorder()
        r.record_event("job", "first", {"i": 1})
        time.sleep(0.01)
        r.record_event("job", "second", {"i": 2})
        timeline = r.get_timeline("job")
        assert timeline[0]["event_type"] == "first"
        assert timeline[1]["event_type"] == "second"


class TestGetSummary:
    def test_get_summary_empty_todo_id_rejected(self):
        r = RunHistoryRecorder()
        r.record_event("job-1", "evt", {}, todo_id="REAL-1")
        result = r.get_summary("")
        assert result["event_count"] == 0

    def test_get_summary_exact_todo_id_match(self):
        r = RunHistoryRecorder()
        r.record_event("TODO-5", "e1", {}, todo_id="TODO-5")
        result = r.get_summary("TODO-5")
        assert result["event_count"] == 1

    def test_get_summary_prefix_match_via_colon_separator(self):
        r = RunHistoryRecorder()
        r.record_event("TODO-5:subjob-1", "e1", {})
        r.record_event("TODO-5:subjob-2", "e2", {})
        result = r.get_summary("TODO-5")
        assert result["event_count"] == 2

    def test_get_summary_does_not_false_match_substring(self):
        r = RunHistoryRecorder()
        r.record_event("TODO-5", "e1", {})
        r.record_event("TODO-55", "e2", {})
        result = r.get_summary("TODO-5")
        assert result["event_count"] == 1

    def test_get_summary_does_not_false_match_longer_id(self):
        r = RunHistoryRecorder()
        r.record_event("TODO-42", "e1", {})
        result = r.get_summary("TODO-4")
        assert result["event_count"] == 0

    def test_get_summary_todo_override_takes_precedence(self):
        r = RunHistoryRecorder()
        r.record_event("unrelated-job", "e1", {}, todo_id="MATCH-ME")
        result = r.get_summary("MATCH-ME")
        assert result["event_count"] == 1

    def test_get_summary_missing_todo_returns_zero(self):
        r = RunHistoryRecorder()
        result = r.get_summary("NONEXISTENT")
        assert result["event_count"] == 0

    def test_get_summary_returns_event_list(self):
        r = RunHistoryRecorder()
        r.record_event("TODO-1", "dispatch", {"target": "gate"})
        result = r.get_summary("TODO-1")
        assert len(result["events"]) == 1
        assert result["events"][0]["event_type"] == "dispatch"

    def test_get_summary_events_are_deep_copies(self):
        r = RunHistoryRecorder()
        r.record_event("TODO-1", "e", {"key": "orig"})
        result = r.get_summary("TODO-1")
        result["events"][0]["data"]["key"] = "mutated"
        result2 = r.get_summary("TODO-1")
        assert result2["events"][0]["data"]["key"] == "orig"


class TestEviction:
    def test_job_eviction_when_above_max_jobs(self):
        r = RunHistoryRecorder(max_jobs=3)
        for i in range(5):
            r.record_event(f"job-{i}", "evt", {"idx": i})
        assert r.get_timeline("job-0") == []
        assert r.get_timeline("job-1") == []
        assert len(r.get_timeline("job-2")) == 1
        assert len(r.get_timeline("job-3")) == 1
        assert len(r.get_timeline("job-4")) == 1

    def test_eviction_clears_artifacts_too(self):
        r = RunHistoryRecorder(max_jobs=2)
        r.record_artifact("job-0", "f", "val")
        r.record_event("job-1", "e", {})
        r.record_event("job-2", "e", {})
        assert r.get_artifacts("job-0") == {}

    def test_eviction_fifo_order(self):
        r = RunHistoryRecorder(max_jobs=2)
        r.record_event("a", "e", {})
        r.record_event("b", "e", {})
        r.record_event("c", "e", {})
        assert r.get_timeline("a") == []
        assert len(r.get_timeline("b")) == 1
        assert len(r.get_timeline("c")) == 1

    def test_re_record_keeps_original_position(self):
        r = RunHistoryRecorder(max_jobs=2)
        r.record_event("a", "e1", {})
        r.record_event("b", "e1", {})
        r.record_event("a", "e2", {})  # re-record a — keeps original FIFO position
        r.record_event("c", "e1", {})  # a still oldest — evicted despite re-record
        c_timeline = r.get_timeline("c")
        assert len(c_timeline) == 1
        assert c_timeline[0]["event_type"] == "e1"

    def test_eviction_clears_todo_overrides(self):
        r = RunHistoryRecorder(max_jobs=2)
        r.record_event("job-0", "e", {}, todo_id="T-0")
        r.record_event("job-1", "e", {})
        r.record_event("job-2", "e", {})
        assert r.get_summary("T-0")["event_count"] == 0


class TestDefaults:
    def test_default_max_events_per_job_is_10000(self):
        r = RunHistoryRecorder()
        assert r.max_events_per_job == 10000

    def test_default_max_jobs_is_1000(self):
        r = RunHistoryRecorder()
        assert r.max_jobs == 1000

    def test_custom_max_values_accepted(self):
        r = RunHistoryRecorder(max_events_per_job=50, max_jobs=10)
        assert r.max_events_per_job == 50
        assert r.max_jobs == 10
