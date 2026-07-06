"""Unit tests for ingest: record->todo mapping, dedup, lifecycle write-back."""

from __future__ import annotations

from typing import Any, cast

from general_ludd.issue_sources.base import IssueSource, Transition, new_issue_record
from general_ludd.issue_sources.ingest import (
    dedup_key,
    ingest_records,
    lifecycle_write_back,
    record_to_todo,
    transition_for_status,
)
from general_ludd.schemas.todo import TodoStatus


def _rec(external_id: str, status: str = "open", **kw: object) -> dict:
    return cast(Any, new_issue_record)(external_id=external_id, title=f"item {external_id}", status=status, **kw)


# --- dedup key -------------------------------------------------------------


def test_dedup_key_format() -> None:
    assert dedup_key("jira", "PROJ-1") == "jira:PROJ-1"


# --- record -> todo --------------------------------------------------------


def test_record_to_todo_maps_fields() -> None:
    rec = _rec("PROJ-1", status="In Progress", priority="High", labels=["bug"], url="https://x/1")
    todo = record_to_todo(rec, "jira")
    assert todo["title"] == "item PROJ-1"
    assert todo["status"] == TodoStatus.ACTIVE.value
    assert todo["priority"] == 2  # High -> 2
    assert todo["external_id"] == "jira:PROJ-1"
    assert "source:jira" in todo["tags"]
    assert "bug" in todo["tags"]
    assert "https://x/1" in todo["description"]
    assert todo["created_by"] == "issue_source:jira"
    assert todo["queue"] == "intake"


def test_record_to_todo_unknown_status_defaults_backlog() -> None:
    todo = record_to_todo(_rec("R-1", status="some-novel"), "redmine")
    assert todo["status"] == TodoStatus.BACKLOG.value


def test_record_to_todo_empty_title_falls_back() -> None:
    rec = new_issue_record(external_id="9", title="")
    todo = record_to_todo(rec, "github_issues")
    assert todo["title"] == "github_issues 9"


def test_record_to_todo_numeric_priority_clamped() -> None:
    assert record_to_todo(_rec("a", priority="5"), "s")["priority"] == 3
    assert record_to_todo(_rec("a", priority="0"), "s")["priority"] == 0
    assert record_to_todo(_rec("a"), "s")["priority"] == 1  # None -> medium


# --- dedup ingest ----------------------------------------------------------


def test_ingest_dedups_within_batch() -> None:
    records = [_rec("A"), _rec("A"), _rec("B")]
    todos, seen = ingest_records(records, "jira")
    ids = sorted(t["external_id"] for t in todos)
    assert ids == ["jira:A", "jira:B"]
    assert seen == {"jira:A", "jira:B"}


def test_ingest_skips_already_seen() -> None:
    todos, seen = ingest_records([_rec("A"), _rec("B")], "jira", seen_keys={"jira:A"})
    assert [t["external_id"] for t in todos] == ["jira:B"]
    assert seen == {"jira:A", "jira:B"}


def test_ingest_is_idempotent_with_returned_keys() -> None:
    records = [_rec("A"), _rec("B")]
    todos1, seen1 = ingest_records(records, "jira")
    todos2, seen2 = ingest_records(records, "jira", seen_keys=seen1)
    assert len(todos1) == 2
    assert todos2 == []  # nothing new the second pass
    assert seen2 == seen1


def test_ingest_skips_unkeyable_records() -> None:
    todos, _ = ingest_records([new_issue_record(external_id="", title="no id")], "jira")
    assert todos == []


def test_ingest_distinct_sources_do_not_collide() -> None:
    todos, seen = ingest_records([_rec("1")], "jira", seen_keys={"redmine:1"})
    assert [t["external_id"] for t in todos] == ["jira:1"]
    assert seen == {"redmine:1", "jira:1"}


# --- lifecycle write-back --------------------------------------------------


class _RecordingSource(IssueSource):
    SOURCE = "fake"

    def __init__(self) -> None:
        super().__init__({"base_url": "https://fake.example.com"})
        self.writes: list[tuple[str, Transition]] = []

    def fetch(self, spec: object | None = None) -> list:
        return []

    def write_back(self, external_id: str, transition: Transition) -> bool:
        self.writes.append((external_id, transition))
        return True


def test_transition_for_status() -> None:
    assert transition_for_status(TodoStatus.ACTIVE) is Transition.CLAIM
    assert transition_for_status(TodoStatus.COMPLETE) is Transition.DONE
    assert transition_for_status(TodoStatus.BACKLOG) is None


def test_lifecycle_write_back_claim_on_active() -> None:
    src = _RecordingSource()
    assert lifecycle_write_back(src, "X-1", TodoStatus.ACTIVE) is True
    assert src.writes == [("X-1", Transition.CLAIM)]


def test_lifecycle_write_back_done_on_complete() -> None:
    src = _RecordingSource()
    assert lifecycle_write_back(src, "X-1", TodoStatus.COMPLETE) is True
    assert src.writes == [("X-1", Transition.DONE)]


def test_lifecycle_write_back_noop_for_unmapped_status() -> None:
    src = _RecordingSource()
    # BACKLOG has no outward transition -> no write, but a success return.
    assert lifecycle_write_back(src, "X-1", TodoStatus.BACKLOG) is True
    assert src.writes == []
