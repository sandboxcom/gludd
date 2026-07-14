"""Tests for the issue-source contract, registry, and bidirectional sync engine.

TDD: these tests pin the behaviour of ``general_ludd.issue_sources.base`` —
the ``IssueSource`` / ``TodoStore`` Protocols, the ``IssueRegistry``, and the
``IssueSyncEngine`` that translates between external issue trackers and gludd's
internal todo lifecycle.

The engine is pure/deterministic: it depends only on the injected Protocols
(a fake ``IssueSource`` and a fake ``TodoStore``), never the real DB or any
concrete adapter.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from general_ludd.issue_sources.base import (
    IssueRegistry,
    IssueSource,
    IssueSyncEngine,
    NormalizedIssue,
    SyncReport,
)


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #
class FakeIssueSource:
    """In-memory IssueSource satisfying the Protocol for tests."""

    name = "fake"
    SYSTEM = "fake-tracker"

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
        # records of update_status / add_comment calls for assertions
        self.status_calls: list[dict[str, Any]] = []
        self.comment_calls: list[dict[str, Any]] = []

    def health(self) -> dict[str, Any]:
        return {"ok": True, "source": self.name}

    def fetch_issues(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def update_status(
        self, external_id: str, status: str, comment: str | None = None
    ) -> dict[str, Any]:
        if external_id in self.fail_on:
            raise RuntimeError(f"boom on {external_id}")
        rec = {"external_id": external_id, "status": status, "comment": comment}
        self.status_calls.append(rec)
        return {"ok": True, **rec}

    def add_comment(self, external_id: str, comment: str) -> dict[str, Any]:
        rec = {"external_id": external_id, "comment": comment}
        self.comment_calls.append(rec)
        return {"ok": True, **rec}


class FakeTodoStore:
    """In-memory TodoStore satisfying the Protocol for tests.

    Stores todos keyed by an internal ``id``; a todo linked to an external
    issue carries ``source`` + ``external_id`` so ``list_linked`` can index it.
    """

    def __init__(self) -> None:
        self.todos: dict[str, dict[str, Any]] = {}
        self._next = 1

    def list_linked(self, source: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for todo in self.todos.values():
            if todo.get("source") == source and todo.get("external_id"):
                out[str(todo["external_id"])] = todo
        return out

    def create_from_issue(self, issue: dict[str, Any]) -> dict[str, Any]:
        tid = f"T{self._next}"
        self._next += 1
        todo = {
            "id": tid,
            "source": issue["source"],
            "external_id": issue["external_id"],
            "title": issue.get("title", ""),
            "status": issue.get("status", ""),
        }
        self.todos[tid] = todo
        return todo

    def update_todo(self, todo_id: str, **fields: Any) -> dict[str, Any]:
        todo = self.todos[todo_id]
        todo.update(fields)
        return todo

    def internal_status(self, todo: dict[str, Any]) -> str:
        return str(todo.get("internal_status", todo.get("status", "")))


# --------------------------------------------------------------------------- #
# Registry                                                                     #
# --------------------------------------------------------------------------- #
class TestIssueRegistry:
    def test_register_get_all(self) -> None:
        reg = IssueRegistry()
        src = FakeIssueSource()
        reg.register(src)
        assert reg.get("fake") is src
        assert reg.all() == [src]

    def test_get_unknown_raises_keyerror(self) -> None:
        reg = IssueRegistry()
        with pytest.raises(KeyError):
            reg.get("nope")

    def test_register_duplicate_name_raises(self) -> None:
        reg = IssueRegistry()
        reg.register(FakeIssueSource())
        with pytest.raises(ValueError):
            reg.register(FakeIssueSource())

    def test_all_is_a_copy(self) -> None:
        reg = IssueRegistry()
        reg.register(FakeIssueSource())
        snap = reg.all()
        snap.clear()
        assert len(reg.all()) == 1


# --------------------------------------------------------------------------- #
# sync_in                                                                      #
# --------------------------------------------------------------------------- #
def _issue(external_id: str, **over: Any) -> NormalizedIssue:
    base: NormalizedIssue = {
        "external_id": external_id,
        "source": "fake",
        "title": f"Issue {external_id}",
        "description": "",
        "status": "Open",
        "assignee": None,
        "labels": [],
        "priority": None,
        "url": f"https://tracker/{external_id}",
        "updated_ts": None,
        "raw": {},
    }
    cast(Any, base).update(over)
    return base


class TestSyncIn:
    def test_creates_new_todo(self) -> None:
        store = FakeTodoStore()
        reg = IssueRegistry()
        reg.register(FakeIssueSource())
        engine = IssueSyncEngine(reg, store)

        report = engine.sync_in("fake", [_issue("E1")])

        assert report.created == 1
        assert report.updated == 0
        assert report.skipped == 0
        assert not report.errors
        linked = store.list_linked("fake")
        assert "E1" in linked
        assert linked["E1"]["title"] == "Issue E1"

    def test_dedups_already_linked_issue(self) -> None:
        store = FakeTodoStore()
        reg = IssueRegistry()
        reg.register(FakeIssueSource())
        engine = IssueSyncEngine(reg, store)

        engine.sync_in("fake", [_issue("E1")])
        # second sync of the *same* unchanged issue -> skipped, no duplicate
        report = engine.sync_in("fake", [_issue("E1")])

        assert report.created == 0
        assert report.skipped == 1
        assert len(store.list_linked("fake")) == 1

    def test_updates_changed_issue(self) -> None:
        store = FakeTodoStore()
        reg = IssueRegistry()
        reg.register(FakeIssueSource())
        engine = IssueSyncEngine(reg, store)

        engine.sync_in("fake", [_issue("E1", title="old", status="Open")])
        report = engine.sync_in(
            "fake", [_issue("E1", title="new title", status="In Progress")]
        )

        assert report.created == 0
        assert report.updated == 1
        todo = store.list_linked("fake")["E1"]
        assert todo["title"] == "new title"
        # external status mapped to an internal queue/status
        assert todo["status"] != "Open"

    def test_external_to_internal_status_map_on_create(self) -> None:
        store = FakeTodoStore()
        reg = IssueRegistry()
        reg.register(FakeIssueSource())
        # custom inbound map: external "Open" -> internal "QUEUED"
        engine = IssueSyncEngine(
            reg, store, inbound_status_map={"Open": "QUEUED"}
        )

        engine.sync_in("fake", [_issue("E1", status="Open")])
        assert store.list_linked("fake")["E1"]["status"] == "QUEUED"

    def test_mixed_batch_counts(self) -> None:
        store = FakeTodoStore()
        reg = IssueRegistry()
        reg.register(FakeIssueSource())
        engine = IssueSyncEngine(reg, store)

        engine.sync_in("fake", [_issue("E1", title="t1")])
        report = engine.sync_in(
            "fake",
            [
                _issue("E1", title="t1"),  # unchanged -> skip
                _issue("E2", title="t2"),  # new -> create
            ],
        )
        assert report.created == 1
        assert report.skipped == 1


# --------------------------------------------------------------------------- #
# sync_out                                                                     #
# --------------------------------------------------------------------------- #
def _linked_todo(
    store: FakeTodoStore, external_id: str, internal_status: str
) -> dict[str, Any]:
    todo = {
        "id": external_id,
        "source": "fake",
        "external_id": external_id,
        "title": "x",
        "status": "",
        "internal_status": internal_status,
    }
    store.todos[external_id] = todo
    return todo


class TestSyncOut:
    def test_active_maps_to_in_progress_with_comment(self) -> None:
        store = FakeTodoStore()
        src = FakeIssueSource()
        reg = IssueRegistry()
        reg.register(src)
        engine = IssueSyncEngine(reg, store)

        todo = _linked_todo(store, "E1", "ACTIVE")
        report = engine.sync_out("fake", [todo])

        assert report.updated == 1
        assert not report.errors
        assert src.status_calls == [
            {
                "external_id": "E1",
                "status": "In Progress",
                "comment": "gludd is now working this issue",
            }
        ]

    def test_in_progress_alias_maps_to_in_progress(self) -> None:
        store = FakeTodoStore()
        src = FakeIssueSource()
        reg = IssueRegistry()
        reg.register(src)
        engine = IssueSyncEngine(reg, store)

        todo = _linked_todo(store, "E1", "IN_PROGRESS")
        engine.sync_out("fake", [todo])
        assert src.status_calls[0]["status"] == "In Progress"

    def test_done_maps_to_done_with_comment(self) -> None:
        store = FakeTodoStore()
        src = FakeIssueSource()
        reg = IssueRegistry()
        reg.register(src)
        engine = IssueSyncEngine(reg, store)

        todo = _linked_todo(store, "E1", "DONE")
        report = engine.sync_out("fake", [todo])

        assert report.updated == 1
        assert src.status_calls == [
            {
                "external_id": "E1",
                "status": "Done",
                "comment": "gludd has completed this issue",
            }
        ]

    def test_completed_alias_maps_to_done(self) -> None:
        store = FakeTodoStore()
        src = FakeIssueSource()
        reg = IssueRegistry()
        reg.register(src)
        engine = IssueSyncEngine(reg, store)

        engine.sync_out("fake", [_linked_todo(store, "E1", "COMPLETED")])
        assert src.status_calls[0]["status"] == "Done"

    def test_cancelled_maps_to_cancelled(self) -> None:
        store = FakeTodoStore()
        src = FakeIssueSource()
        reg = IssueRegistry()
        reg.register(src)
        engine = IssueSyncEngine(reg, store)

        engine.sync_out("fake", [_linked_todo(store, "E1", "CANCELLED")])
        assert src.status_calls[0]["status"] == "Cancelled"

    def test_unmapped_internal_status_is_skipped(self) -> None:
        store = FakeTodoStore()
        src = FakeIssueSource()
        reg = IssueRegistry()
        reg.register(src)
        engine = IssueSyncEngine(reg, store)

        report = engine.sync_out("fake", [_linked_todo(store, "E1", "QUEUED")])
        assert report.updated == 0
        assert report.skipped == 1
        assert src.status_calls == []

    def test_failing_update_recorded_as_error_without_aborting(self) -> None:
        store = FakeTodoStore()
        src = FakeIssueSource(fail_on={"E1"})
        reg = IssueRegistry()
        reg.register(src)
        engine = IssueSyncEngine(reg, store)

        todos = [
            _linked_todo(store, "E1", "ACTIVE"),  # raises
            _linked_todo(store, "E2", "DONE"),  # still processed
        ]
        report = engine.sync_out("fake", todos)

        assert report.updated == 1  # E2 succeeded
        assert len(report.errors) == 1
        err_id, err_msg = report.errors[0]
        assert err_id == "E1"
        assert "boom" in err_msg
        # the whole sync was NOT aborted: E2 update happened
        assert any(c["external_id"] == "E2" for c in src.status_calls)

    def test_outbound_status_map_override_respected(self) -> None:
        store = FakeTodoStore()
        src = FakeIssueSource()
        reg = IssueRegistry()
        reg.register(src)
        # override: internal DONE -> external "Closed" + custom comment
        engine = IssueSyncEngine(
            reg,
            store,
            status_map={"DONE": ("Closed", "wrapped up")},
        )

        engine.sync_out("fake", [_linked_todo(store, "E1", "DONE")])
        assert src.status_calls == [
            {"external_id": "E1", "status": "Closed", "comment": "wrapped up"}
        ]

    def test_skips_todos_not_linked_to_this_source(self) -> None:
        store = FakeTodoStore()
        src = FakeIssueSource()
        reg = IssueRegistry()
        reg.register(src)
        engine = IssueSyncEngine(reg, store)

        other = {
            "id": "X1",
            "source": "other-tracker",
            "external_id": "X1",
            "internal_status": "DONE",
        }
        report = engine.sync_out("fake", [other])
        assert report.updated == 0
        assert src.status_calls == []


# --------------------------------------------------------------------------- #
# SyncReport                                                                   #
# --------------------------------------------------------------------------- #
class TestSyncReport:
    def test_defaults_are_zero_and_empty(self) -> None:
        r = SyncReport()
        assert (r.created, r.updated, r.skipped) == (0, 0, 0)
        assert r.errors == []


# --------------------------------------------------------------------------- #
# IssueSource — abstract base                                                  #
# --------------------------------------------------------------------------- #
class TestIssueSourceAbstract:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            IssueSource({"name": "test"}, require_base_url=False)  # type: ignore[abstract]
        assert True  # reached — ABC prevents direct instantiation
