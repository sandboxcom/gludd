"""Coverage tests for observability/run_history.py (RunHistoryRecorder).

CI flagged this module at 50%. Uncovered paths were the second-event append
branch (job_id already present in the timeline dict), the second-artifact
branch (job_id already present in the artifacts dict), the get_timeline /
get_artifacts copy-out accessors, and get_summary's substring-matching
aggregation across multiple job ids.

Pure in-memory recorder — no DB / network.
"""

from __future__ import annotations

from general_ludd.observability.run_history import RunHistoryRecorder


class TestRecordEvent:
    def test_first_event_creates_timeline_list(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("job-1", "model_call", {"model": "x"})
        timeline = rec.get_timeline("job-1")
        assert len(timeline) == 1
        assert timeline[0]["event_type"] == "model_call"
        assert timeline[0]["data"] == {"model": "x"}

    def test_second_event_appends_to_existing_list(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("job-1", "start", {})
        rec.record_event("job-1", "commit", {"sha": "abc123"})
        timeline = rec.get_timeline("job-1")
        assert len(timeline) == 2
        assert [e["event_type"] for e in timeline] == ["start", "commit"]

    def test_events_isolated_per_job(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("job-1", "a", {})
        rec.record_event("job-2", "b", {})
        assert len(rec.get_timeline("job-1")) == 1
        assert len(rec.get_timeline("job-2")) == 1

    def test_get_timeline_unknown_job_returns_empty(self) -> None:
        rec = RunHistoryRecorder()
        assert rec.get_timeline("nope") == []

    def test_get_timeline_returns_copy_not_internal_list(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("job-1", "a", {})
        out = rec.get_timeline("job-1")
        out.append({"event_type": "injected", "data": {}})
        # mutating the returned list must not affect internal state
        assert len(rec.get_timeline("job-1")) == 1


class TestRecordArtifact:
    def test_first_artifact_creates_dict(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_artifact("job-1", "test_output.txt", "PASSED")
        arts = rec.get_artifacts("job-1")
        assert arts == {"test_output.txt": "PASSED"}

    def test_second_artifact_adds_to_existing_dict(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_artifact("job-1", "a.txt", "one")
        rec.record_artifact("job-1", "b.txt", "two")
        arts = rec.get_artifacts("job-1")
        assert arts == {"a.txt": "one", "b.txt": "two"}

    def test_artifact_overwrites_same_name(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_artifact("job-1", "a.txt", "old")
        rec.record_artifact("job-1", "a.txt", "new")
        assert rec.get_artifacts("job-1")["a.txt"] == "new"

    def test_get_artifacts_unknown_job_returns_empty(self) -> None:
        rec = RunHistoryRecorder()
        assert rec.get_artifacts("nope") == {}

    def test_get_artifacts_returns_copy(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_artifact("job-1", "a.txt", "v")
        out = rec.get_artifacts("job-1")
        out["injected"] = "x"
        assert "injected" not in rec.get_artifacts("job-1")


class TestGetSummary:
    def test_summary_empty_for_unknown_todo(self) -> None:
        rec = RunHistoryRecorder()
        summary = rec.get_summary("TODO-XYZ")
        assert summary == {"todo_id": "TODO-XYZ", "event_count": 0, "events": []}

    def test_summary_aggregates_events_by_substring_match(self) -> None:
        rec = RunHistoryRecorder()
        # job ids embedding the todo id should be collected
        rec.record_event("TODO-42:job-a", "e1", {})
        rec.record_event("TODO-42:job-a", "e2", {})
        rec.record_event("TODO-42:job-b", "e3", {})
        # unrelated job id must NOT be collected
        rec.record_event("TODO-99:job-c", "e4", {})
        summary = rec.get_summary("TODO-42")
        assert summary["todo_id"] == "TODO-42"
        assert summary["event_count"] == 3
        event_types = sorted(e["event_type"] for e in summary["events"])
        assert event_types == ["e1", "e2", "e3"]

    def test_summary_excludes_non_matching_jobs(self) -> None:
        rec = RunHistoryRecorder()
        rec.record_event("other-job", "x", {})
        summary = rec.get_summary("TODO-1")
        assert summary["event_count"] == 0
