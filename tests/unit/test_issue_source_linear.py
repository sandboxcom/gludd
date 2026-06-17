"""Unit tests for LinearIssueSource (mocked httpx transport)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from general_ludd.issue_sources.linear import SYSTEM, LinearIssueSource

_ISSUE_NODE = {
    "id": "iss_1",
    "identifier": "ENG-1",
    "title": "Crash on save",
    "description": "Null deref",
    "url": "https://linear.app/x/issue/ENG-1",
    "updatedAt": "2026-06-10T00:00:00Z",
    "priority": 2,
    "state": {"name": "In Progress"},
    "assignee": {"name": "Ada"},
    "labels": {"nodes": [{"name": "bug"}, {"name": "p1"}]},
}


def _config(**over: Any) -> dict[str, Any]:
    base = {
        "base_url": "https://api.linear.app",
        "status_state_map": {"done": "state_done"},
    }
    base.update(over)
    return base


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_key")


def test_fetch_issues_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"issues": {"nodes": [_ISSUE_NODE]}}})

    src = LinearIssueSource(_config(), transport=httpx.MockTransport(handler))
    issues = src.fetch_issues({"first": 10})

    assert src.SYSTEM == "linear" == SYSTEM
    issue = issues[0]
    assert issue["external_id"] == "iss_1"
    assert issue["source"] == "linear"
    assert issue["title"] == "Crash on save"
    assert issue["status"] == "In Progress"
    assert issue["assignee"] == "Ada"
    assert issue["labels"] == ["bug", "p1"]
    assert issue["priority"] == 2
    assert issue["url"] == "https://linear.app/x/issue/ENG-1"
    # auth header from env (apiKey style, raw value)
    assert seen["auth"] == "lin_api_key"
    assert seen["body"]["variables"] == {"first": 10}


def test_update_status_mutation_and_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "issueUpdate" in body["query"]:
            return httpx.Response(200, json={"data": {"issueUpdate": {"success": True}}})
        return httpx.Response(200, json={"data": {"commentCreate": {"success": True}}})

    src = LinearIssueSource(_config(), transport=httpx.MockTransport(handler))
    result = src.update_status("iss_1", "done", comment="fixed it")

    update_body = next(b for b in bodies if "issueUpdate" in b["query"])
    assert update_body["variables"] == {"id": "iss_1", "stateId": "state_done"}
    comment_body = next(b for b in bodies if "commentCreate" in b["query"])
    assert comment_body["variables"] == {"issueId": "iss_1", "body": "fixed it"}
    assert "updated" in result and "comment" in result


def test_update_status_unmapped_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    src = LinearIssueSource(_config(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {}})))
    with pytest.raises(ValueError):
        src.update_status("iss_1", "nope")


def test_internal_base_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    for bad in ("http://localhost/graphql", "http://169.254.169.254", "http://192.168.1.1"):
        with pytest.raises(ValueError):
            LinearIssueSource(_config(base_url=bad))


def test_health_ok_and_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    ok = LinearIssueSource(
        _config(),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}})),
    )
    assert ok.health() == {"ok": True, "detail": "linear reachable"}

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    err = LinearIssueSource(_config(), transport=httpx.MockTransport(boom))
    assert err.health()["ok"] is False  # never raises
