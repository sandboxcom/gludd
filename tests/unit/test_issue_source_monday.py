"""Unit tests for MondayIssueSource (mocked transport, no network)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from general_ludd.issue_sources.monday import MondayIssueSource


class RecordingTransport:
    """Mock transport that records calls and replays scripted responses."""

    def __init__(self, responses: list[tuple[int, dict[str, Any]]]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: dict[str, Any] | None,
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": json_body,
                "timeout": timeout,
            }
        )
        if self._responses:
            return self._responses.pop(0)
        return 200, {"data": {}}


def _items_payload() -> dict[str, Any]:
    return {
        "data": {
            "boards": [
                {
                    "items_page": {
                        "items": [
                            {
                                "id": "101",
                                "name": "Fix login bug",
                                "updated_at": "2026-06-01T10:00:00Z",
                                "creators": [{"id": "u1", "name": "Ada"}],
                                "column_values": [
                                    {"id": "status", "title": "Status", "text": "Working on it"},
                                    {"id": "priority", "title": "Priority", "text": "High"},
                                    {"id": "tags", "title": "Tags", "text": "bug, backend"},
                                    {"id": "person", "title": "Owner", "text": "Grace"},
                                    {"id": "description", "title": "Description", "text": "users locked out"},
                                ],
                            }
                        ]
                    }
                }
            ]
        }
    }


def test_fetch_normalizes_items() -> None:
    tx = RecordingTransport([(200, _items_payload())])
    src = MondayIssueSource(
        {"board_id": 555, "token_env": "MTOK"},
        transport=tx,
        env={"MTOK": "secret-token"},
    )
    issues = src.fetch_issues({"limit": 10})
    assert len(issues) == 1
    issue = issues[0]
    assert issue["external_id"] == "101"
    assert issue["source"] == "monday"
    assert issue["title"] == "Fix login bug"
    assert issue["description"] == "users locked out"
    assert issue["status"] == "Working on it"
    assert issue["assignee"] == "Grace"
    assert issue["labels"] == ["bug", "backend"]
    assert issue["priority"] == "High"
    assert issue["updated_ts"] == "2026-06-01T10:00:00Z"
    assert "101" in issue["url"]
    assert issue["raw"]["id"] == "101"
    # Normalized dict carries the full contract key-set.
    for key in (
        "external_id",
        "source",
        "title",
        "description",
        "status",
        "assignee",
        "labels",
        "priority",
        "url",
        "updated_ts",
        "raw",
    ):
        assert key in issue


def test_auth_token_from_env() -> None:
    tx = RecordingTransport([(200, _items_payload())])
    src = MondayIssueSource(
        {"board_id": 1, "token_env": "MTOK"},
        transport=tx,
        env={"MTOK": "abc123"},
    )
    src.fetch_issues()
    assert tx.calls[0]["headers"]["Authorization"] == "abc123"
    # Endpoint is the GraphQL v2 path on the default host.
    assert tx.calls[0]["url"] == "https://api.monday.com/v2"
    assert tx.calls[0]["method"] == "POST"


def test_update_status_writeback_body() -> None:
    tx = RecordingTransport(
        [
            (200, {"data": {"change_column_value": {"id": "101"}}}),
            (200, {"data": {"create_update": {"id": "u9"}}}),
        ]
    )
    src = MondayIssueSource(
        {"board_id": 555, "status_column": "status", "token_env": "MTOK"},
        transport=tx,
        env={"MTOK": "tok"},
    )
    result = src.update_status("101", "Done", comment="shipped")
    assert result["ok"] is True
    # First call: change_column_value mutation with status column + JSON label.
    status_call = tx.calls[0]
    assert "change_column_value" in status_call["body"]["query"]
    vs = status_call["body"]["variables"]
    assert vs["item"] == "101"
    assert vs["column"] == "status"
    assert '"label": "Done"' in vs["value"]
    # Second call: create_update comment.
    comment_call = tx.calls[1]
    assert "create_update" in comment_call["body"]["query"]
    assert comment_call["body"]["variables"]["body"] == "shipped"


def test_add_comment_body() -> None:
    tx = RecordingTransport([(200, {"data": {"create_update": {"id": "u1"}}})])
    src = MondayIssueSource({"board_id": 1}, transport=tx, env={})
    res = src.add_comment("77", "a note")
    assert res["ok"] is True
    assert tx.calls[0]["body"]["variables"]["item"] == "77"
    assert tx.calls[0]["body"]["variables"]["body"] == "a note"


def test_internal_base_url_rejected() -> None:
    for bad in (
        "http://169.254.169.254",
        "http://127.0.0.1",
        "http://localhost",
        "http://10.0.0.5",
        "http://metadata",
        "http://svc.internal",
    ):
        with pytest.raises(ValueError):
            MondayIssueSource({"base_url": bad}, transport=RecordingTransport([]), env={})


def test_health_ok() -> None:
    tx = RecordingTransport([(200, {"data": {"me": {"id": "u1"}}})])
    src = MondayIssueSource({}, transport=tx, env={"MONDAY_API_TOKEN": "t"})
    assert src.health() == {"ok": True, "detail": "ok"}


def test_health_not_ok_on_http_error() -> None:
    tx = RecordingTransport([(401, {"errors": [{"message": "unauthorized"}]})])
    src = MondayIssueSource({}, transport=tx, env={})
    h = src.health()
    assert h["ok"] is False
    assert "401" in h["detail"]


def test_health_never_raises() -> None:
    def boom(*_a: Any, **_k: Any) -> tuple[int, dict[str, Any]]:
        raise RuntimeError("network down")

    src = MondayIssueSource({}, transport=boom, env={})
    h = src.health()
    assert h["ok"] is False
    assert "network down" in h["detail"]


def test_system_and_name_attrs() -> None:
    assert MondayIssueSource.SYSTEM == "monday"
    src = MondayIssueSource({"name": "team-board"}, transport=RecordingTransport([]), env={})
    assert src.name == "team-board"
