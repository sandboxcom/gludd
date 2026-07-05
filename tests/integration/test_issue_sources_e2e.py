"""End-to-end integration tests for the issue-sources feature.

Covers the full adapter family (CSV/Excel, MarkdownTodo, GitHubIssuesSource),
the sync engine, the registry, the ingest pipeline, status mappings, record
factories, SSRF guards, and transition enums. Every external dependency is
faked/mocked inline; no network, no real database.
"""

from __future__ import annotations

import csv
import os
import tempfile
from typing import Any

import pytest

from general_ludd.issue_sources.base import (
    DEFAULT_INBOUND_STATUS_MAP,
    DEFAULT_OUTBOUND_STATUS_MAP,
    IssueRecord,
    IssueRegistry,
    IssueSource,
    IssueSyncEngine,
    NormalizedIssue,
    SyncReport,
    Transition,
    map_external_status,
    new_issue_record,
    parse_iso_ts,
)
from general_ludd.issue_sources.csv_excel import CsvExcelSource
from general_ludd.issue_sources.github_issues import GitHubIssuesSource
from general_ludd.issue_sources.ingest import (
    dedup_key,
    ingest_records,
    lifecycle_write_back,
    record_to_todo,
    transition_for_status,
)
from general_ludd.issue_sources.markdown_todo import MarkdownTodoSource
from general_ludd.schemas.todo import TodoStatus

# ---------------------------------------------------------------------------
# Inline fakes for TodoStore and SyncSource
# ---------------------------------------------------------------------------

class FakeTodoStore:
    """In-memory fake that implements the TodoStore Protocol."""

    def __init__(self, existing: dict[str, dict[str, Any]] | None = None):
        self._todos: dict[str, dict[str, Any]] = {}
        self._source_index: dict[str, dict[str, dict[str, Any]]] = {}
        if existing:
            for key, todo in existing.items():
                self._todos[key] = dict(todo)
                src = todo.get("source", "unknown")
                ext = todo.get("external_id", key)
                self._source_index.setdefault(src, {})[ext] = self._todos[key]

    def list_linked(self, source: str) -> dict[str, dict[str, Any]]:
        return dict(self._source_index.get(source, {}))

    def create_from_issue(self, issue: dict[str, Any]) -> dict[str, Any]:
        todo_id = issue.get("external_id", f"gen-{len(self._todos)}")
        todo = {
            "id": todo_id,
            "title": issue.get("title", ""),
            "status": issue.get("status", "backlog"),
            "source": issue.get("source", "unknown"),
            "external_id": issue.get("external_id", todo_id),
        }
        self._todos[todo_id] = todo
        src = todo["source"]
        ext = todo["external_id"]
        self._source_index.setdefault(src, {})[ext] = todo
        return todo

    def update_todo(self, todo_id: str, **fields: Any) -> dict[str, Any]:
        todo = self._todos.get(todo_id)
        if todo is None:
            raise KeyError(f"no todo with id {todo_id!r}")
        todo.update(fields)
        return todo

    def internal_status(self, todo: dict[str, Any]) -> str:
        return str(todo.get("status", "backlog"))


class FakeSyncSource:
    """In-memory fake that implements the SyncSource Protocol."""

    def __init__(self, name: str, SYSTEM: str = "fake"):
        self.name = name
        self.SYSTEM = SYSTEM
        self._issues: dict[str, dict[str, Any]] = {}
        self._status_updates: list[dict[str, Any]] = []
        self._comments: list[dict[str, Any]] = []

    def health(self) -> dict[str, Any]:
        return {"ok": True, "name": self.name}

    def fetch_issues(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return [self._issues[key] for key in sorted(self._issues)]

    def update_status(self, external_id: str, status: str, comment: str | None = None) -> dict[str, Any]:
        self._status_updates.append(
            {"external_id": external_id, "status": status, "comment": comment}
        )
        return {"ok": True, "external_id": external_id}

    def add_comment(self, external_id: str, comment: str) -> dict[str, Any]:
        self._comments.append({"external_id": external_id, "comment": comment})
        return {"ok": True, "external_id": external_id}

    def seed_issue(self, **overrides: Any) -> NormalizedIssue:
        defaults: dict[str, Any] = {
            "external_id": "1",
            "source": self.name,
            "title": "Test issue",
            "description": "",
            "status": "Open",
            "assignee": None,
            "labels": [],
            "priority": None,
            "url": "https://example.com/1",
            "updated_ts": None,
            "raw": {},
        }
        defaults.update(overrides)
        issue: NormalizedIssue = NormalizedIssue(**defaults)
        self._issues[issue["external_id"]] = dict(issue)
        return issue


# ---------------------------------------------------------------------------
# 1. IssueRegistry
# ---------------------------------------------------------------------------

class TestIssueRegistry:
    def test_register_and_get(self):
        reg = IssueRegistry()
        src = FakeSyncSource("gh-main", "github")
        reg.register(src)
        assert reg.get("gh-main") is src

    def test_get_missing_raises_keyerror(self):
        reg = IssueRegistry()
        with pytest.raises(KeyError, match="no issue source registered: 'nope'"):
            reg.get("nope")

    def test_register_duplicate_raises_valueerror(self):
        reg = IssueRegistry()
        reg.register(FakeSyncSource("dup", "github"))
        with pytest.raises(ValueError, match="already registered: 'dup'"):
            reg.register(FakeSyncSource("dup", "jira"))

    def test_all_snapshot(self):
        reg = IssueRegistry()
        a = FakeSyncSource("a", "fake")
        b = FakeSyncSource("b", "fake")
        reg.register(a)
        reg.register(b)
        all_srcs = reg.all()
        assert len(all_srcs) == 2
        assert a in all_srcs
        assert b in all_srcs


# ---------------------------------------------------------------------------
# 2. SyncReport
# ---------------------------------------------------------------------------

class TestSyncReport:
    def test_defaults(self):
        r = SyncReport()
        assert r.created == 0
        assert r.updated == 0
        assert r.skipped == 0
        assert r.errors == []

    def test_field_assignment(self):
        r = SyncReport(created=3, updated=1, skipped=5, errors=[("1", "boom")])
        assert r.created == 3
        assert r.updated == 1
        assert r.skipped == 5
        assert r.errors == [("1", "boom")]


# ---------------------------------------------------------------------------
# 3. IssueSyncEngine — sync_in
# ---------------------------------------------------------------------------

class TestSyncEngineSyncIn:
    def test_creates_new_todos_from_issues(self):
        reg = IssueRegistry()
        reg.register(FakeSyncSource("gh", "github"))
        store = FakeTodoStore()
        engine = IssueSyncEngine(reg, store)

        issue1: NormalizedIssue = NormalizedIssue(
            external_id="10", source="gh", title="First",
            description="", status="Open", assignee=None, labels=[],
            priority=None, url="", updated_ts=None, raw={},
        )
        issue2: NormalizedIssue = NormalizedIssue(
            external_id="11", source="gh", title="Second",
            description="", status="In Progress", assignee="alice",
            labels=["bug"], priority="High", url="", updated_ts=None, raw={},
        )
        report = engine.sync_in("gh", [dict(issue1), dict(issue2)])
        assert report.created == 2
        assert report.updated == 0
        assert report.skipped == 0
        assert report.errors == []

    def test_dedup_within_batch_skips_duplicates(self):
        reg = IssueRegistry()
        reg.register(FakeSyncSource("gh", "github"))
        store = FakeTodoStore()
        engine = IssueSyncEngine(reg, store)

        issue: NormalizedIssue = NormalizedIssue(
            external_id="1", source="gh", title="One",
            description="", status="Open", assignee=None, labels=[],
            priority=None, url="", updated_ts=None, raw={},
        )
        report = engine.sync_in("gh", [dict(issue), dict(issue)])
        assert report.created == 1
        assert report.skipped == 1

    def test_update_existing_when_title_changes(self):
        reg = IssueRegistry()
        reg.register(FakeSyncSource("gh", "github"))
        store = FakeTodoStore()
        existing = {"id": "1", "title": "Old Title", "status": "QUEUED",
                    "source": "gh", "external_id": "1"}
        store._todos["1"] = existing
        store._source_index["gh"] = {"1": existing}
        engine = IssueSyncEngine(reg, store)

        issue: NormalizedIssue = NormalizedIssue(
            external_id="1", source="gh", title="New Title",
            description="", status="Open", assignee=None, labels=[],
            priority=None, url="", updated_ts=None, raw={},
        )
        report = engine.sync_in("gh", [dict(issue)])
        assert report.created == 0
        assert report.updated == 1
        assert report.skipped == 0

    def test_skip_when_no_change(self):
        reg = IssueRegistry()
        reg.register(FakeSyncSource("gh", "github"))
        store = FakeTodoStore()
        store._source_index["gh"] = {
            "1": {"id": "1", "title": "Same Title", "status": "QUEUED",
                  "source": "gh", "external_id": "1"}
        }
        engine = IssueSyncEngine(reg, store)

        issue: NormalizedIssue = NormalizedIssue(
            external_id="1", source="gh", title="Same Title",
            description="", status="Open", assignee=None, labels=[],
            priority=None, url="", updated_ts=None, raw={},
        )
        report = engine.sync_in("gh", [dict(issue)])
        assert report.created == 0
        assert report.updated == 0
        assert report.skipped == 1

    def test_per_issue_failure_captured_in_errors(self):
        reg = IssueRegistry()
        reg.register(FakeSyncSource("gh", "github"))
        store = FakeTodoStore()
        store._source_index["gh"] = {
            "1": {"id": "1", "title": "X", "status": "QUEUED",
                  "source": "gh", "external_id": "1"}
        }
        # Cause update_todo to fail by not including "id" on the existing todo
        store._source_index["gh"]["1"].pop("id")

        engine = IssueSyncEngine(reg, store)
        issue: NormalizedIssue = NormalizedIssue(
            external_id="1", source="gh", title="Changed",
            description="", status="Open", assignee=None, labels=[],
            priority=None, url="", updated_ts=None, raw={},
        )
        report = engine.sync_in("gh", [dict(issue)])
        assert len(report.errors) == 1
        assert report.errors[0][0] == "1"
        # The second issue should still process normally
        issue2: NormalizedIssue = NormalizedIssue(
            external_id="2", source="gh", title="Other",
            description="", status="Open", assignee=None, labels=[],
            priority=None, url="", updated_ts=None, raw={},
        )
        report = engine.sync_in("gh", [dict(issue), dict(issue2)])
        assert report.created >= 1  # issue2 was created
        assert len(report.errors) == 1


# ---------------------------------------------------------------------------
# 4. IssueSyncEngine — sync_out
# ---------------------------------------------------------------------------

class TestSyncEngineSyncOut:
    def test_pushes_status_to_source(self):
        reg = IssueRegistry()
        src = FakeSyncSource("gh-main", "github")
        reg.register(src)
        store = FakeTodoStore()
        engine = IssueSyncEngine(reg, store)

        todos = [
            {"id": "t1", "source": "gh-main", "external_id": "42", "status": "DONE"},
            {"id": "t2", "source": "gh-main", "external_id": "43", "status": "ACTIVE"},
        ]
        report = engine.sync_out("gh-main", todos)
        assert report.updated == 2
        assert report.skipped == 0
        assert len(src._status_updates) == 2
        assert src._status_updates[0]["external_id"] == "42"
        assert src._status_updates[0]["status"] == "Done"
        assert src._status_updates[1]["external_id"] == "43"
        assert src._status_updates[1]["status"] == "In Progress"

    def test_skip_unmapped_status(self):
        reg = IssueRegistry()
        src = FakeSyncSource("gh-main", "github")
        reg.register(src)
        store = FakeTodoStore()
        engine = IssueSyncEngine(reg, store)

        todos = [{"id": "t1", "source": "gh-main", "external_id": "42",
                  "status": "UNKNOWN_WEIRD_STATUS"}]
        report = engine.sync_out("gh-main", todos)
        assert report.updated == 0
        assert report.skipped == 1

    def test_skip_different_source(self):
        reg = IssueRegistry()
        src = FakeSyncSource("gh-main", "github")
        reg.register(src)
        store = FakeTodoStore()
        engine = IssueSyncEngine(reg, store)

        todos = [{"id": "t1", "source": "other-source", "external_id": "42",
                  "status": "DONE"}]
        report = engine.sync_out("gh-main", todos)
        assert report.updated == 0
        assert report.skipped == 1

    def test_per_todo_failure_captured(self):
        reg = IssueRegistry()

        class ExplodingSource:
            name = "gh-main"
            SYSTEM = "github"

            def health(self):
                return {"ok": True}

            def fetch_issues(self, spec):
                return []

            def update_status(self, external_id, status, comment=None):
                if external_id == "bad":
                    raise RuntimeError("simulated failure")
                return {"ok": True, "external_id": external_id}

            def add_comment(self, external_id, comment):
                return {"ok": True}

        reg.register(ExplodingSource())
        store = FakeTodoStore()
        engine = IssueSyncEngine(reg, store)

        todos = [
            {"id": "t1", "source": "gh-main", "external_id": "good", "status": "DONE"},
            {"id": "t2", "source": "gh-main", "external_id": "bad", "status": "DONE"},
        ]
        report = engine.sync_out("gh-main", todos)
        assert len(report.errors) == 1
        assert report.errors[0][0] == "bad"
        assert report.updated >= 1


# ---------------------------------------------------------------------------
# 5. Custom status maps
# ---------------------------------------------------------------------------

class TestCustomStatusMaps:
    def test_custom_outbound_map(self):
        reg = IssueRegistry()
        src = FakeSyncSource("gh", "github")
        reg.register(src)
        store = FakeTodoStore()
        custom = {"DONE": ("Resolved", "custom comment")}
        engine = IssueSyncEngine(reg, store, status_map=custom)

        todos = [{"id": "t1", "source": "gh", "external_id": "1", "status": "DONE"}]
        report = engine.sync_out("gh", todos)
        assert report.updated == 1
        assert src._status_updates[0]["status"] == "Resolved"
        assert src._status_updates[0]["comment"] == "custom comment"

    def test_custom_inbound_map(self):
        reg = IssueRegistry()
        reg.register(FakeSyncSource("gh", "github"))
        store = FakeTodoStore()
        custom_inbound = {"Open": "SCHEDULED"}
        engine = IssueSyncEngine(reg, store, inbound_status_map=custom_inbound)

        issue: NormalizedIssue = NormalizedIssue(
            external_id="1", source="gh", title="T",
            description="", status="Open", assignee=None, labels=[],
            priority=None, url="", updated_ts=None, raw={},
        )
        report = engine.sync_in("gh", [dict(issue)])
        assert report.created == 1
        linked = store.list_linked("gh")
        assert linked["1"]["status"] == "SCHEDULED"


# ---------------------------------------------------------------------------
# 6. DEFAULT_OUTBOUND_STATUS_MAP and DEFAULT_INBOUND_STATUS_MAP
# ---------------------------------------------------------------------------

class TestDefaultStatusMaps:
    def test_outbound_aliases(self):
        assert DEFAULT_OUTBOUND_STATUS_MAP["ACTIVE"] == (
            "In Progress", "gludd is now working this issue")
        assert DEFAULT_OUTBOUND_STATUS_MAP["IN_PROGRESS"] == (
            "In Progress", "gludd is now working this issue")
        assert DEFAULT_OUTBOUND_STATUS_MAP["DONE"] == (
            "Done", "gludd has completed this issue")
        assert DEFAULT_OUTBOUND_STATUS_MAP["COMPLETED"] == (
            "Done", "gludd has completed this issue")
        assert DEFAULT_OUTBOUND_STATUS_MAP["CANCELLED"] == ("Cancelled", None)

    def test_inbound_canonical(self):
        assert DEFAULT_INBOUND_STATUS_MAP["Open"] == "QUEUED"
        assert DEFAULT_INBOUND_STATUS_MAP["To Do"] == "QUEUED"
        assert DEFAULT_INBOUND_STATUS_MAP["Backlog"] == "QUEUED"
        assert DEFAULT_INBOUND_STATUS_MAP["In Progress"] == "ACTIVE"
        assert DEFAULT_INBOUND_STATUS_MAP["Done"] == "DONE"
        assert DEFAULT_INBOUND_STATUS_MAP["Closed"] == "DONE"
        assert DEFAULT_INBOUND_STATUS_MAP["Cancelled"] == "CANCELLED"


# ---------------------------------------------------------------------------
# 7. map_external_status
# ---------------------------------------------------------------------------

class TestMapExternalStatus:
    @pytest.mark.parametrize("external, expected", [
        ("open", TodoStatus.BACKLOG),
        ("new", TodoStatus.BACKLOG),
        ("to do", TodoStatus.BACKLOG),
        ("todo", TodoStatus.BACKLOG),
        ("backlog", TodoStatus.BACKLOG),
        ("queued", TodoStatus.QUEUED),
        ("in progress", TodoStatus.ACTIVE),
        ("in-progress", TodoStatus.ACTIVE),
        ("active", TodoStatus.ACTIVE),
        ("doing", TodoStatus.ACTIVE),
        ("started", TodoStatus.ACTIVE),
        ("done", TodoStatus.COMPLETE),
        ("closed", TodoStatus.COMPLETE),
        ("complete", TodoStatus.COMPLETE),
        ("completed", TodoStatus.COMPLETE),
        ("resolved", TodoStatus.COMPLETE),
        ("fixed", TodoStatus.COMPLETE),
        ("cancelled", TodoStatus.CANCELLED),
        ("canceled", TodoStatus.CANCELLED),
        ("wontfix", TodoStatus.CANCELLED),
        ("blocked", TodoStatus.BLOCKED),
    ])
    def test_known_status_words(self, external, expected):
        assert map_external_status(external) is expected

    def test_case_insensitive(self):
        assert map_external_status("OPEN") is TodoStatus.BACKLOG
        assert map_external_status("In Progress") is TodoStatus.ACTIVE
        assert map_external_status("DONE") is TodoStatus.COMPLETE

    def test_unknown_falls_back_to_backlog(self):
        assert map_external_status("whimsy") is TodoStatus.BACKLOG
        assert map_external_status("") is TodoStatus.BACKLOG

    def test_whitespace_stripped(self):
        assert map_external_status("  open  ") is TodoStatus.BACKLOG


# ---------------------------------------------------------------------------
# 8. new_issue_record
# ---------------------------------------------------------------------------

class TestNewIssueRecord:
    def test_minimal_record(self):
        rec = new_issue_record(external_id="42", title="Fix bug")
        assert rec["external_id"] == "42"
        assert rec["title"] == "Fix bug"
        assert rec["body"] == ""
        assert rec["status"] == "open"
        assert rec["priority"] is None
        assert rec["assignee"] is None
        assert rec["labels"] == []
        assert rec["url"] == ""
        assert rec["updated_at"] is None
        assert rec["raw"] == {}

    def test_fully_populated(self):
        rec = new_issue_record(
            external_id="99", title="Full", body="desc", status="closed",
            priority="High", assignee="alice", labels=["bug", "urgent"],
            url="https://x.com/99", updated_at=1.0,
            source="gh", raw={"ghid": 99},
        )
        assert rec["external_id"] == "99"
        assert rec["body"] == "desc"
        assert rec["status"] == "closed"
        assert rec["priority"] == "High"
        assert rec["assignee"] == "alice"
        assert rec["labels"] == ["bug", "urgent"]
        assert rec["url"] == "https://x.com/99"
        assert rec["updated_at"] == 1.0
        assert rec["source"] == "gh"
        assert rec["raw"] == {"ghid": 99}

    def test_none_labels_becomes_empty_list(self):
        rec = new_issue_record(external_id="1", title="T", labels=None)
        assert rec["labels"] == []

    def test_none_raw_becomes_empty_dict(self):
        rec = new_issue_record(external_id="1", title="T", raw=None)
        assert rec["raw"] == {}


# ---------------------------------------------------------------------------
# 9. parse_iso_ts
# ---------------------------------------------------------------------------

class TestParseIsoTs:
    def test_none_string_returns_none(self):
        assert parse_iso_ts(None) is None

    def test_empty_string_returns_none(self):
        assert parse_iso_ts("") is None
        assert parse_iso_ts("   ") is None

    def test_valid_iso(self):
        ts = parse_iso_ts("2025-01-15T08:30:00+00:00")
        assert ts is not None
        assert ts > 1_730_000_000

    def test_trailing_z(self):
        ts = parse_iso_ts("2025-01-15T08:30:00Z")
        assert ts is not None

    def test_lowercase_z(self):
        ts = parse_iso_ts("2025-01-15T08:30:00z")
        assert ts is not None

    def test_invalid_returns_none(self):
        assert parse_iso_ts("not-a-date") is None
        assert parse_iso_ts("2025-13-01") is None

    def test_date_only(self):
        ts = parse_iso_ts("2025-01-15")
        assert ts is not None


# ---------------------------------------------------------------------------
# 10. Transition enum
# ---------------------------------------------------------------------------

class TestTransitionEnum:
    def test_values(self):
        assert Transition.CLAIM.value == "claim"
        assert Transition.DONE.value == "done"

    def test_is_enum(self):
        assert isinstance(Transition.CLAIM, Transition)
        assert isinstance(Transition.DONE, Transition)


# ---------------------------------------------------------------------------
# 11. CsvExcelSource
# ---------------------------------------------------------------------------

class TestCsvExcelSource:
    def test_fetch_basic_csv(self):
        content = (
            "id,title,status\n"
            "1,Fix login,open\n"
            "2,Add tests,in progress\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "issues.csv")
            with open(path, "w") as f:
                f.write(content)
            src = CsvExcelSource({"path": path, "root": tmp})
            records = src.fetch()
            assert len(records) == 2
            assert records[0]["external_id"] == "1"
            assert records[0]["title"] == "Fix login"
            assert records[0]["status"] == "open"
            assert records[1]["external_id"] == "2"
            assert records[1]["title"] == "Add tests"
            assert records[1]["status"] == "in progress"

    def test_fetch_empty_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.csv")
            with open(path, "w") as f:
                f.write("")
            src = CsvExcelSource({"path": path, "root": tmp})
            assert src.fetch() == []

    def test_missing_id_column_falls_back_to_row_ordinal(self):
        content = (
            "title,status\n"
            "No ID,open\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "noid.csv")
            with open(path, "w") as f:
                f.write(content)
            src = CsvExcelSource({"path": path, "root": tmp})
            records = src.fetch()
            assert records[0]["external_id"] == "row-1"

    def test_write_back_csv_claim_and_done(self):
        content = (
            "id,title,status\n"
            "42,The Task,open\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "issues.csv")
            with open(path, "w") as f:
                f.write(content)
            src = CsvExcelSource({"path": path, "root": tmp})

            assert src.write_back("42", Transition.CLAIM) is True
            # Re-read to verify
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            assert rows[1][2] == "in progress"

            assert src.write_back("42", Transition.DONE) is True
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            assert rows[1][2] == "done"

    def test_write_back_idempotent(self):
        content = (
            "id,title,status\n"
            "42,The Task,done\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "issues.csv")
            with open(path, "w") as f:
                f.write(content)
            src = CsvExcelSource({"path": path, "root": tmp})
            # Already "done" — idempotent
            assert src.write_back("42", Transition.DONE) is True

    def test_write_back_nonexistent_id_returns_false(self):
        content = (
            "id,title,status\n"
            "1,Only,open\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "issues.csv")
            with open(path, "w") as f:
                f.write(content)
            src = CsvExcelSource({"path": path, "root": tmp})
            assert src.write_back("999", Transition.DONE) is False

    def test_missing_path_raises_valueerror(self):
        with pytest.raises(ValueError, match="config\\['path'\\] is required"):
            CsvExcelSource({"path": ""})

    def test_path_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, pytest.raises(
            ValueError, match="refusing csv/excel path outside"
        ):
            CsvExcelSource({"path": "/etc/passwd", "root": tmp})

    def test_custom_columns_mapping(self):
        content = (
            "ticket_id,summary,state\n"
            "ABC-1,Fix stuff,todo\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "custom.csv")
            with open(path, "w") as f:
                f.write(content)
            src = CsvExcelSource({
                "path": path,
                "root": tmp,
                "columns": {"ticket_id": "external_id", "summary": "title", "state": "status"},
            })
            records = src.fetch()
            assert records[0]["external_id"] == "ABC-1"
            assert records[0]["title"] == "Fix stuff"
            assert records[0]["status"] == "todo"

    def test_labels_column_split_on_comma(self):
        content = (
            "id,title,status,labels\n"
            '1,T,bug,"bug,urgent"\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "labels.csv")
            with open(path, "w") as f:
                f.write(content)
            src = CsvExcelSource({"path": path, "root": tmp})
            records = src.fetch()
            assert records[0]["labels"] == ["bug", "urgent"]

    def test_custom_status_words(self):
        content = (
            "id,title,status\n"
            "1,T,open\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sw.csv")
            with open(path, "w") as f:
                f.write(content)
            src = CsvExcelSource({
                "path": path,
                "root": tmp,
                "status_words": {"claim": "working", "done": "finished"},
            })
            assert src.write_back("1", Transition.CLAIM) is True
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            assert rows[1][2] == "working"


# ---------------------------------------------------------------------------
# 12. MarkdownTodoSource
# ---------------------------------------------------------------------------

class TestMarkdownTodoSource:
    def test_fetch_checkboxes(self):
        content = (
            "- [ ] Buy milk\n"
            "- [x] Pay bills <!--id:bills-->\n"
            "not a checkbox\n"
            "- [ ] (#42)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "todo.md")
            with open(path, "w") as f:
                f.write(content)
            src = MarkdownTodoSource({"root": tmp, "path": path})
            issues = src.fetch_issues()
            assert len(issues) == 3

            # First: derived id (no explicit id)
            assert issues[0]["external_id"].startswith("md-")
            assert issues[0]["title"] == "Buy milk"
            assert issues[0]["status"] == "open"

            # Second: HTML id comment extracts "bills"
            assert issues[1]["external_id"] == "bills"
            assert issues[1]["title"] == "Pay bills"
            assert issues[1]["status"] == "done"

            # Third: paren-hash ref extracts "42"
            assert issues[2]["external_id"] == "42"
            assert issues[2]["title"] == ""
            assert issues[2]["status"] == "open"

    def test_health_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "todo.md")
            with open(path, "w") as f:
                f.write("- [ ] a\n")
            src = MarkdownTodoSource({"root": tmp, "path": path})
            health = src.health()
            assert health["ok"] is True

    def test_health_not_ok_when_unreadable(self):
        src = MarkdownTodoSource({"root": "/tmp", "path": "/tmp/no-such-file-md-todo-xyz.md"})
        health = src.health()
        assert health["ok"] is False

    def test_update_status_mark_done(self):
        content = "- [ ] Deploy <!--id:deploy-->\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "todo.md")
            with open(path, "w") as f:
                f.write(content)
            src = MarkdownTodoSource({"root": tmp, "path": path})
            result = src.update_status("deploy", "done")
            assert result["status"] == "done"
            with open(path) as f:
                updated = f.read()
            assert "- [x] Deploy <!--id:deploy-->" in updated

    def test_update_status_with_comment(self):
        content = "- [ ] Task <!--id:t1-->\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "todo.md")
            with open(path, "w") as f:
                f.write(content)
            src = MarkdownTodoSource({"root": tmp, "path": path})
            result = src.update_status("t1", "open", comment="gludd says hi")
            assert result["comment"] == "gludd says hi"
            with open(path) as f:
                updated = f.read()
            assert "<!--gludd:gludd says hi-->" in updated

    def test_update_status_missing_id_raises_keyerror(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "todo.md")
            with open(path, "w") as f:
                f.write("- [ ] Only\n")
            src = MarkdownTodoSource({"root": tmp, "path": path})
            with pytest.raises(KeyError, match="not found"):
                src.update_status("nonexistent", "done")

    def test_add_comment(self):
        content = "- [ ] Item <!--id:item-->\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "todo.md")
            with open(path, "w") as f:
                f.write(content)
            src = MarkdownTodoSource({"root": tmp, "path": path})
            result = src.add_comment("item", "working on it")
            assert "working on it" in result["comment"]
            with open(path) as f:
                updated = f.read()
            assert "  - working on it" in updated

    def test_add_comment_missing_id_raises_keyerror(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "todo.md")
            with open(path, "w") as f:
                f.write("- [ ] lone\n")
            src = MarkdownTodoSource({"root": tmp, "path": path})
            with pytest.raises(KeyError, match="not found"):
                src.add_comment("nope", "comment")

    def test_path_confined_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp, pytest.raises(
            ValueError, match=r"escapes the configured root"
        ):
            MarkdownTodoSource({"root": tmp, "path": os.path.join(tmp, "..", "escape.md")})

    def test_missing_root_raises(self):
        with pytest.raises(ValueError, match=r"config\['root'\].*is required"):
            MarkdownTodoSource({"root": "", "path": "/x"})

    def test_missing_path_raises(self):
        with pytest.raises(ValueError, match="config\\['path'\\] is required"):
            MarkdownTodoSource({"root": "/tmp", "path": ""})


# ---------------------------------------------------------------------------
# 13. ingest module — dedup_key / record_to_todo / ingest_records /
#     lifecycle_write_back / transition_for_status
# ---------------------------------------------------------------------------

class TestIngestDedupKey:
    def test_format(self):
        assert dedup_key("github", "42") == "github:42"
        assert dedup_key("csv_excel", "row-3") == "csv_excel:row-3"


class TestRecordToTodo:
    def test_basic_mapping(self):
        record: IssueRecord = IssueRecord(
            external_id="42", source="gh", title="Fix login", body="desc",
            status="in progress", priority="High", assignee="alice",
            labels=["bug"], url="https://x.com/42", updated_at=1.0, raw={},
        )
        todo = record_to_todo(record, "gh")
        assert todo["title"] == "Fix login"
        assert todo["status"] == TodoStatus.ACTIVE.value
        assert todo["priority"] == 2  # "high" -> 2
        assert "source:gh" in todo["tags"]
        assert "bug" in todo["tags"]
        assert todo["external_id"] == "gh:42"
        assert "https://x.com/42" in todo["description"]

    def test_missing_title_falls_back(self):
        record: IssueRecord = IssueRecord(
            external_id="1", source="test", title="", body="",
            status="open", priority=None, assignee=None, labels=[],
            url="", updated_at=None, raw={},
        )
        todo = record_to_todo(record, "test")
        assert todo["title"] == "test 1"

    def test_numeric_priority_string_clamped(self):
        record: IssueRecord = IssueRecord(
            external_id="1", source="test", title="T", body="",
            status="open", priority="5", assignee=None, labels=[],
            url="", updated_at=None, raw={},
        )
        todo = record_to_todo(record, "test")
        assert todo["priority"] == 3  # clamped to 0..3

    def test_no_url_means_no_external_line(self):
        record: IssueRecord = IssueRecord(
            external_id="1", source="test", title="T", body="desc",
            status="open", priority=None, assignee=None, labels=[],
            url="", updated_at=None, raw={},
        )
        todo = record_to_todo(record, "test")
        assert "External:" not in todo["description"]


class TestIngestRecords:
    def test_creates_new_and_dedups(self):
        record: IssueRecord = IssueRecord(
            external_id="1", source="csv", title="A", body="",
            status="open", priority=None, assignee=None, labels=[],
            url="", updated_at=None, raw={},
        )
        todos, keys = ingest_records([record], "csv")
        assert len(todos) == 1
        assert "csv:1" in keys

        # Idempotent call
        todos2, keys2 = ingest_records([record], "csv", seen_keys=keys)
        assert len(todos2) == 0
        assert keys2 == keys

    def test_skips_empty_external_id(self):
        record: IssueRecord = IssueRecord(
            external_id="", source="csv", title="A", body="",
            status="open", priority=None, assignee=None, labels=[],
            url="", updated_at=None, raw={},
        )
        todos, _keys = ingest_records([record], "csv")
        assert todos == []

    def test_seen_keys_filter(self):
        r1: IssueRecord = IssueRecord(
            external_id="1", source="csv", title="A", body="",
            status="open", priority=None, assignee=None, labels=[],
            url="", updated_at=None, raw={},
        )
        r2: IssueRecord = IssueRecord(
            external_id="2", source="csv", title="B", body="",
            status="open", priority=None, assignee=None, labels=[],
            url="", updated_at=None, raw={},
        )
        todos, _keys = ingest_records([r1, r2], "csv", seen_keys={"csv:1"})
        assert len(todos) == 1
        assert todos[0]["external_id"] == "csv:2"


class TestTransitionForStatus:
    def test_active_maps_to_claim(self):
        assert transition_for_status(TodoStatus.ACTIVE) is Transition.CLAIM

    def test_complete_maps_to_done(self):
        assert transition_for_status(TodoStatus.COMPLETE) is Transition.DONE

    def test_other_statuses_return_none(self):
        assert transition_for_status(TodoStatus.BACKLOG) is None
        assert transition_for_status(TodoStatus.QUEUED) is None
        assert transition_for_status(TodoStatus.CANCELLED) is None
        assert transition_for_status(TodoStatus.BLOCKED) is None


class TestLifecycleWriteBack:
    def test_claim_calls_write_back(self):
        calls: list[tuple[str, Transition]] = []

        class SpySource(IssueSource):
            SOURCE = "spy"

            def __init__(self) -> None:
                super().__init__({}, require_base_url=False)

            def fetch(self, spec=None):
                return []

            def write_back(self, external_id: str, transition: Transition) -> bool:
                calls.append((external_id, transition))
                return True

        src = SpySource()
        result = lifecycle_write_back(src, "ext-1", TodoStatus.ACTIVE)
        assert result is True
        assert calls == [("ext-1", Transition.CLAIM)]

    def test_complete_calls_write_back(self):
        calls: list[tuple[str, Transition]] = []

        class SpySource(IssueSource):
            SOURCE = "spy"

            def __init__(self) -> None:
                super().__init__({}, require_base_url=False)

            def fetch(self, spec=None):
                return []

            def write_back(self, external_id: str, transition: Transition) -> bool:
                calls.append((external_id, transition))
                return True

        src = SpySource()
        result = lifecycle_write_back(src, "ext-2", TodoStatus.COMPLETE)
        assert result is True
        assert calls == [("ext-2", Transition.DONE)]

    def test_no_write_back_for_unmapped_status(self):
        class NoopSource(IssueSource):
            SOURCE = "noop"

            def __init__(self) -> None:
                super().__init__({}, require_base_url=False)

            def fetch(self, spec=None):
                return []

            def write_back(self, external_id: str, transition: Transition) -> bool:
                raise AssertionError("should not be called")

        src = NoopSource()
        result = lifecycle_write_back(src, "ext-1", TodoStatus.BACKLOG)
        assert result is True


# ---------------------------------------------------------------------------
# 14. GitHubIssuesSource — construction, SSRF guard, transport injection
# ---------------------------------------------------------------------------

class TestGitHubIssuesSource:
    def test_construction_with_valid_config(self):
        src = GitHubIssuesSource({"repo": "owner/name"})
        assert src.SOURCE == "github_issues"
        assert src.base_url == "https://api.github.com"

    def test_custom_base_url(self):
        src = GitHubIssuesSource({
            "repo": "owner/name",
            "base_url": "https://ghe.example.com/api/v3",
        })
        assert src.base_url == "https://ghe.example.com/api/v3"

    def test_missing_repo_raises(self):
        with pytest.raises(ValueError, match="config\\['repo'\\] must be 'owner/name'"):
            GitHubIssuesSource({"repo": ""})

    def test_repo_without_slash_raises(self):
        with pytest.raises(ValueError, match="config\\['repo'\\] must be 'owner/name'"):
            GitHubIssuesSource({"repo": "justowner"})

    def test_base_url_ssrf_localhost_blocked(self):
        with pytest.raises(ValueError, match="refusing internal base_url host"):
            GitHubIssuesSource({"repo": "owner/name", "base_url": "http://localhost:8080"})

    def test_base_url_ssrf_private_ip_blocked(self):
        with pytest.raises(ValueError, match="refusing internal base_url host"):
            GitHubIssuesSource({"repo": "owner/name", "base_url": "http://10.0.0.1/api"})

    def test_base_url_ssrf_loopback_blocked(self):
        with pytest.raises(ValueError, match="refusing internal base_url host"):
            GitHubIssuesSource({"repo": "owner/name", "base_url": "http://127.0.0.1/api"})

    def test_transport_injection_fetch(self):
        transport_calls: list[dict[str, Any]] = []

        def fake_transport(
            method: str, url: str, headers: dict[str, str], body: Any | None,
        ) -> tuple[int, Any]:
            transport_calls.append(
                {"method": method, "url": url, "headers": headers, "body": body}
            )
            return (200, [{
                "number": 1, "title": "Hello", "state": "open",
                "body": "", "labels": [], "assignee": None,
                "html_url": "https://github.com/owner/name/issues/1",
            }])

        src = GitHubIssuesSource({"repo": "owner/name"}, transport=fake_transport)
        records = src.fetch()
        assert len(records) == 1
        assert records[0]["external_id"] == "1"
        assert records[0]["title"] == "Hello"
        assert records[0]["status"] == "open"
        assert len(transport_calls) == 1
        assert transport_calls[0]["method"] == "GET"
        assert "/repos/owner/name/issues" in transport_calls[0]["url"]

    def test_transport_injection_write_back_done(self):
        transport_calls: list[dict[str, Any]] = []

        def fake_transport(
            method: str, url: str, headers: dict[str, str], body: Any | None,
        ) -> tuple[int, Any]:
            transport_calls.append(
                {"method": method, "url": url, "headers": headers, "body": body}
            )
            return (200, {})

        src = GitHubIssuesSource({"repo": "owner/name"}, transport=fake_transport)
        result = src.write_back("42", Transition.DONE)
        assert result is True
        assert len(transport_calls) == 1
        assert transport_calls[0]["method"] == "PATCH"
        assert transport_calls[0]["body"] == {"state": "closed"}

    def test_transport_injection_write_back_claim_no_label(self):
        src = GitHubIssuesSource({"repo": "owner/name"}, transport=lambda *a: (200, {}))
        result = src.write_back("42", Transition.CLAIM)
        assert result is True  # no-op success when no claim_label

    def test_transport_injection_write_back_claim_with_label(self):
        transport_calls: list[dict[str, Any]] = []

        def fake_transport(
            method: str, url: str, headers: dict[str, str], body: Any | None,
        ) -> tuple[int, Any]:
            transport_calls.append(
                {"method": method, "url": url, "headers": headers, "body": body}
            )
            return (200, {})

        src = GitHubIssuesSource(
            {"repo": "owner/name", "claim_label": "claimed"},
            transport=fake_transport,
        )
        result = src.write_back("42", Transition.CLAIM)
        assert result is True
        assert len(transport_calls) == 1
        assert transport_calls[0]["method"] == "POST"
        assert transport_calls[0]["body"] == {"labels": ["claimed"]}

    def test_token_env(self):
        src = GitHubIssuesSource(
            {"repo": "owner/name", "token_env": "MY_TOKEN"},
            env={"MY_TOKEN": "ghp_secret"},
        )
        assert src._token() == "ghp_secret"

    def test_fetch_skips_pull_requests(self):
        def fake_transport(
            method: str, url: str, headers: dict[str, str], body: Any | None,
        ) -> tuple[int, Any]:
            return (200, [
                {"number": 1, "title": "Issue", "state": "open",
                 "body": "", "labels": [], "assignee": None,
                 "html_url": "https://github.com/o/r/issues/1"},
                {"number": 2, "title": "PR", "state": "open",
                 "body": "", "labels": [], "assignee": None,
                 "html_url": "https://github.com/o/r/pull/2",
                 "pull_request": {"url": "..."}},
            ])

        src = GitHubIssuesSource({"repo": "owner/name"}, transport=fake_transport)
        records = src.fetch()
        assert len(records) == 1
        assert records[0]["external_id"] == "1"

    def test_fetch_non_200_returns_empty(self):
        def fake_transport(*a: Any) -> tuple[int, Any]:
            return (404, {})

        src = GitHubIssuesSource({"repo": "owner/name"}, transport=fake_transport)
        assert src.fetch() == []


# ---------------------------------------------------------------------------
# 15. IssueSyncEngine with an IssueSource adapter (real composition)
# ---------------------------------------------------------------------------

class TestSyncEngineWithRealAdapters:
    def test_sync_in_with_csv_source(self):
        content = (
            "id,title,status\n"
            "1,Fix login,Open\n"
            "2,Add tests,In Progress\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "issues.csv")
            with open(path, "w") as f:
                f.write(content)
            csv_src = CsvExcelSource({"path": path, "root": tmp})
            records = csv_src.fetch()

            issues: list[NormalizedIssue] = []
            for rec in records:
                issues.append(NormalizedIssue(
                    external_id=rec["external_id"],
                    source="csv_excel",
                    title=rec["title"],
                    description=rec["body"],
                    status=rec["status"],
                    assignee=rec["assignee"],
                    labels=rec["labels"],
                    priority=rec["priority"],
                    url=rec["url"],
                    updated_ts=rec["updated_at"],
                    raw=rec["raw"],
                ))

            reg = IssueRegistry()
            store = FakeTodoStore()
            engine = IssueSyncEngine(reg, store)
            report = engine.sync_in("csv_excel", [dict(iss) for iss in issues])
            assert report.created == 2
            linked = store.list_linked("csv_excel")
            assert len(linked) == 2
            assert linked["1"]["status"] == "QUEUED"
            assert linked["2"]["status"] == "ACTIVE"
