"""Integration tests verifying the issue_sources package wiring:
IssueRegistry, IssueSyncEngine, CsvExcelSource, and the ingest pipeline.

Tests exercise the real package without real GitHub API calls.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Any

import pytest

from general_ludd.issue_sources.base import (
    IssueRegistry,
    IssueSyncEngine,
    SyncReport,
    new_issue_record,
)
from general_ludd.issue_sources.csv_excel import CsvExcelSource
from general_ludd.issue_sources.github_issues import GitHubIssuesSource
from general_ludd.issue_sources.ingest import (
    dedup_key,
    ingest_records,
    record_to_todo,
    transition_for_status,
)
from general_ludd.schemas.todo import TodoStatus


class InMemoryTodoStore:
    """Fake TodoStore for testing IssueSyncEngine without a database."""

    def __init__(self) -> None:
        self._todos: dict[str, dict[str, Any]] = {}

    def list_linked(self, source: str) -> dict[str, dict[str, Any]]:
        return {
            v.get("external_id", ""): v
            for v in self._todos.values()
            if v.get("source") == source
        }

    def create_from_issue(self, issue: dict[str, Any]) -> dict[str, Any]:
        todo_id = f"todo-{len(self._todos) + 1}"
        todo = {
            "id": todo_id,
            "title": issue.get("title", ""),
            "status": issue.get("status", "queued"),
            "source": issue.get("source", ""),
            "external_id": issue.get("external_id", ""),
        }
        self._todos[todo_id] = todo
        return todo

    def update_todo(self, todo_id: str, **fields: Any) -> dict[str, Any]:
        todo = self._todos.get(todo_id, {})
        todo.update(fields)
        self._todos[todo_id] = todo
        return todo

    def internal_status(self, todo: dict[str, Any]) -> str:
        return str(todo.get("status", "queued"))


def _write_csv(path: str, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


# --------------------------------------------------------------------------- #
# IssueRegistry
# --------------------------------------------------------------------------- #
class TestIssueRegistry:
    def test_register_and_get(self) -> None:
        registry = IssueRegistry()
        assert len(registry.all()) == 0

        src = CsvExcelSource(
            config={"name": "csv-test", "path": "/tmp/test.csv", "root": "/tmp"},
        )
        registry.register(src)
        assert len(registry.all()) == 1
        assert registry.get("csv-test") is src

    def test_register_duplicate_raises(self) -> None:
        registry = IssueRegistry()
        src = CsvExcelSource(
            config={"name": "csv-test", "path": "/tmp/test.csv", "root": "/tmp"},
        )
        registry.register(src)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(src)

    def test_get_missing_raises(self) -> None:
        registry = IssueRegistry()
        with pytest.raises(KeyError, match="no issue source registered"):
            registry.get("nonexistent")

    def test_all_returns_snapshot(self) -> None:
        registry = IssueRegistry()
        src_a = CsvExcelSource(
            config={"name": "csv-a", "path": "/tmp/a.csv", "root": "/tmp"},
        )
        src_b = CsvExcelSource(
            config={"name": "csv-b", "path": "/tmp/b.csv", "root": "/tmp"},
        )
        registry.register(src_a)
        registry.register(src_b)
        all_sources = registry.all()
        assert len(all_sources) == 2
        names = {s.name for s in all_sources}
        assert names == {"csv-a", "csv-b"}


# --------------------------------------------------------------------------- #
# CsvExcelSource
# --------------------------------------------------------------------------- #
class TestCsvExcelSource:
    def test_fetch_parses_csv_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = str(Path(tmp) / "issues.csv")
            _write_csv(
                csv_path,
                header=["id", "title", "status", "priority"],
                rows=[
                    ["ISS-1", "Fix login bug", "open", "high"],
                    ["ISS-2", "Add dark mode", "in progress", "medium"],
                ],
            )
            source = CsvExcelSource(config={"path": csv_path, "root": tmp})
            records = source.fetch()
            assert len(records) == 2

            r1, r2 = records
            assert r1["external_id"] == "ISS-1"
            assert r1["title"] == "Fix login bug"
            assert r1["status"] == "open"
            assert r1["priority"] == "high"
            assert isinstance(r1["source"], str)

            assert r2["external_id"] == "ISS-2"
            assert r2["title"] == "Add dark mode"
            assert r2["status"] == "in progress"
            assert r2["priority"] == "medium"
            assert isinstance(r2["source"], str)

    def test_fetch_empty_csv_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = str(Path(tmp) / "empty.csv")
            _write_csv(csv_path, header=[], rows=[])
            source = CsvExcelSource(config={"path": csv_path, "root": tmp})
            records = source.fetch()
            assert records == []

    def test_fetch_header_only_csv_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = str(Path(tmp) / "header_only.csv")
            _write_csv(csv_path, header=["id", "title"], rows=[])
            source = CsvExcelSource(config={"path": csv_path, "root": tmp})
            records = source.fetch()
            assert records == []

    def test_fetch_with_custom_column_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = str(Path(tmp) / "custom.csv")
            _write_csv(
                csv_path,
                header=["ticket_id", "summary", "state"],
                rows=[["T-100", "Database migration", "done"]],
            )
            source = CsvExcelSource(
                config={
                    "path": csv_path,
                    "root": tmp,
                    "columns": {
                        "ticket_id": "external_id",
                        "summary": "title",
                        "state": "status",
                    },
                },
            )
            records = source.fetch()
            assert len(records) == 1
            assert records[0]["external_id"] == "T-100"
            assert records[0]["title"] == "Database migration"
            assert records[0]["status"] == "done"

    def test_write_back_csv_marks_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = str(Path(tmp) / "writeback.csv")
            _write_csv(
                csv_path,
                header=["id", "title", "status"],
                rows=[["ISS-1", "Fix bug", "open"]],
            )
            source = CsvExcelSource(config={"path": csv_path, "root": tmp})

            from general_ludd.issue_sources.base import Transition

            result = source.write_back("ISS-1", Transition.CLAIM)
            assert result is True

            records = source.fetch()
            # After CLAIM the status should be "in progress"
            assert records[0]["status"] in ("in progress", "open")

    def test_missing_path_raises(self) -> None:
        with pytest.raises(ValueError, match="config\\['path'\\]"):
            CsvExcelSource(config={})


# --------------------------------------------------------------------------- #
# ingest pipeline: fetch() -> ingest_records() -> todo dicts
# --------------------------------------------------------------------------- #
class TestIngestPipeline:

    def test_ingest_records_creates_todos_and_dedup(self) -> None:
        records = [
            new_issue_record(external_id="1", title="Task 1", status="open"),
            new_issue_record(external_id="2", title="Task 2", status="in progress"),
        ]
        new_todos, seen = ingest_records(records, source="test-source")
        assert len(new_todos) == 2
        assert len(seen) == 2
        assert "test-source:1" in seen
        assert "test-source:2" in seen

        assert new_todos[0]["title"] == "Task 1"
        assert new_todos[0]["external_id"] == "test-source:1"
        assert new_todos[1]["title"] == "Task 2"
        assert new_todos[1]["external_id"] == "test-source:2"

    def test_ingest_records_skips_already_seen(self) -> None:
        records = [
            new_issue_record(external_id="1", title="Task 1", status="open"),
            new_issue_record(external_id="2", title="Task 2", status="open"),
        ]
        _, seen = ingest_records(records, source="test-source")

        # Re-ingest the same records with the seen set.
        records_again = [
            new_issue_record(external_id="1", title="Task 1", status="open"),
            new_issue_record(external_id="3", title="Task 3", status="open"),
        ]
        new_todos, updated_seen = ingest_records(
            records_again, source="test-source", seen_keys=seen,
        )
        assert len(new_todos) == 1
        assert new_todos[0]["external_id"] == "test-source:3"
        assert len(updated_seen) == 3

    def test_ingest_records_skips_blank_external_id(self) -> None:
        records = [
            new_issue_record(external_id="", title="No ID", status="open"),
            new_issue_record(external_id="1", title="Has ID", status="open"),
        ]
        new_todos, _seen = ingest_records(records, source="test-source")
        assert len(new_todos) == 1
        assert new_todos[0]["external_id"] == "test-source:1"

    def test_record_to_todo_maps_status_via_external_map(self) -> None:
        from general_ludd.issue_sources.base import IssueRecord

        record: IssueRecord = new_issue_record(
            external_id="42", title="Sample", status="done",
        )
        todo = record_to_todo(record, source="csv-x")
        assert todo["title"] == "Sample"
        assert todo["status"] == "complete"  # "done" maps to "complete"
        assert todo["external_id"] == "csv-x:42"
        assert "source:csv-x" in todo["tags"]

    def test_dedup_key_format(self) -> None:
        key = dedup_key("github-main", "101")
        assert key == "github-main:101"

        key2 = dedup_key("csv_excel", "row-5")
        assert key2 == "csv_excel:row-5"

    def test_record_to_todo_unknown_status_falls_back_to_backlog(self) -> None:
        record = new_issue_record(
            external_id="99", title="Unknown status", status="fantastical",
        )
        todo = record_to_todo(record, source="src")
        assert todo["status"] == "backlog"

    def test_transition_for_status_known(self) -> None:
        assert transition_for_status(TodoStatus.ACTIVE) is not None
        assert transition_for_status(TodoStatus.COMPLETE) is not None

    def test_transition_for_status_unknown_is_none(self) -> None:
        assert transition_for_status(TodoStatus.BACKLOG) is None
        assert transition_for_status(TodoStatus.QUEUED) is None

    def test_ingest_pipeline_end_to_end_with_csv_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = str(Path(tmp) / "pipeline.csv")
            _write_csv(
                csv_path,
                header=["id", "title", "status", "priority"],
                rows=[
                    ["P-1", "Login error", "open", "high"],
                    ["P-2", "Search slow", "in progress", "medium"],
                    ["P-3", "Cache invalidation", "done", "low"],
                ],
            )
            source = CsvExcelSource(config={"path": csv_path, "root": tmp})
            records = source.fetch()
            assert len(records) == 3

            new_todos, _ = ingest_records(records, source="pipeline-csv")
            assert len(new_todos) == 3

            statuses = sorted(t["status"] for t in new_todos)
            # "open" -> "backlog", "done" -> "complete", "in progress" -> "active"
            assert "active" in statuses
            assert "backlog" in statuses
            assert "complete" in statuses


# --------------------------------------------------------------------------- #
# IssueSyncEngine: bidirectional sync
# --------------------------------------------------------------------------- #
class TestIssueSyncEngine:
    def test_sync_in_creates_new_todos(self) -> None:
        registry = IssueRegistry()
        store = InMemoryTodoStore()
        engine = IssueSyncEngine(registry, store)

        issues = [
            {
                "external_id": "1",
                "source": "test",
                "title": "Issue one",
                "status": "Open",
                "description": "",
                "assignee": None,
                "labels": [],
                "priority": None,
                "url": "https://example.com/1",
                "updated_ts": None,
                "raw": {},
            },
            {
                "external_id": "2",
                "source": "test",
                "title": "Issue two",
                "status": "In Progress",
                "description": "",
                "assignee": None,
                "labels": [],
                "priority": None,
                "url": "https://example.com/2",
                "updated_ts": None,
                "raw": {},
            },
        ]
        report = engine.sync_in("test", issues)
        assert isinstance(report, SyncReport)
        assert report.created == 2
        assert report.updated == 0
        assert report.skipped == 0
        assert report.errors == []

    def test_sync_in_skips_unchanged_existing(self) -> None:
        registry = IssueRegistry()
        store = InMemoryTodoStore()
        store._todos = {
            "todo-a": {
                "id": "todo-a",
                "title": "Issue one",
                "status": "QUEUED",
                "source": "test",
                "external_id": "1",
            },
        }
        engine = IssueSyncEngine(registry, store)

        issues = [
            {
                "external_id": "1",
                "source": "test",
                "title": "Issue one",
                "status": "Open",
                "description": "",
                "assignee": None,
                "labels": [],
                "priority": None,
                "url": "https://example.com/1",
                "updated_ts": None,
                "raw": {},
            },
        ]
        report = engine.sync_in("test", issues)
        assert report.created == 0
        assert report.updated == 0
        assert report.skipped == 1

    def test_sync_in_custom_inbound_status_map(self) -> None:
        registry = IssueRegistry()
        store = InMemoryTodoStore()
        engine = IssueSyncEngine(
            registry,
            store,
            inbound_status_map={"Open": "CUSTOM_QUEUED"},
        )
        issues = [
            {
                "external_id": "1",
                "source": "test",
                "title": "Issue one",
                "status": "Open",
                "description": "",
                "assignee": None,
                "labels": [],
                "priority": None,
                "url": "https://example.com/1",
                "updated_ts": None,
                "raw": {},
            },
        ]
        report = engine.sync_in("test", issues)
        assert report.created == 1
        assert store._todos["todo-1"]["status"] == "CUSTOM_QUEUED"


# --------------------------------------------------------------------------- #
# GitHubIssuesSource: instantiation (no real API calls)
# --------------------------------------------------------------------------- #
class TestGitHubIssuesSource:
    def test_can_instantiate_with_config(self) -> None:
        source = GitHubIssuesSource(
            config={
                "name": "gh-test",
                "repo": "owner/repo",
                "base_url": "https://api.github.com",
            },
        )
        assert source.name == "gh-test"
        assert source.SOURCE == "github_issues"

    def test_can_instantiate_with_env_token_name(self) -> None:
        source = GitHubIssuesSource(
            config={
                "name": "gh-env",
                "repo": "owner/repo",
                "base_url": "https://api.github.com",
                "token_env": "GITHUB_TOKEN",
            },
        )
        assert source.name == "gh-env"

    def test_internal_base_url_is_refused(self) -> None:
        with pytest.raises(ValueError, match="refusing internal base_url"):
            GitHubIssuesSource(
                config={
                    "name": "bad",
                    "repo": "owner/repo",
                    "base_url": "http://localhost:8080",
                },
            )
