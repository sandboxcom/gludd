"""Unit tests for AsanaIssueSource (mocked httpx transport)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from general_ludd.issue_sources.asana import SYSTEM, AsanaIssueSource

_TASK = {
    "gid": "task1",
    "name": "Ship feature",
    "notes": "the body",
    "completed": False,
    "permalink_url": "https://app.asana.com/0/1/task1",
    "modified_at": "2026-06-10T00:00:00Z",
    "assignee": {"name": "Grace"},
    "memberships": [{"section": {"name": "In Review"}}],
    "tags": [{"name": "frontend"}],
}


def _config(**over: Any) -> dict[str, Any]:
    base = {
        "project": "proj1",
        "base_url": "https://app.asana.com",
        "status_map": {"done": {"completed": True}, "review": {"section": "sec9"}},
    }
    base.update(over)
    return base


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASANA_TOKEN", "asana_pat")


def test_fetch_issues_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": [_TASK]})

    src = AsanaIssueSource(_config(), transport=httpx.MockTransport(handler))
    issues = src.fetch_issues({})

    assert src.SYSTEM == "asana" == SYSTEM
    issue = issues[0]
    assert issue["external_id"] == "task1"
    assert issue["source"] == "asana"
    assert issue["title"] == "Ship feature"
    assert issue["description"] == "the body"
    assert issue["status"] == "In Review"  # section name preferred
    assert issue["assignee"] == "Grace"
    assert issue["labels"] == ["frontend"]
    assert issue["url"] == "https://app.asana.com/0/1/task1"
    # Bearer auth from env
    assert seen["auth"] == "Bearer asana_pat"


def test_update_status_completed_and_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append({"method": request.method, "path": request.url.path, "body": body})
        return httpx.Response(200, json={"data": {"gid": "task1"}})

    src = AsanaIssueSource(_config(), transport=httpx.MockTransport(handler))
    result = src.update_status("task1", "done", comment="done now")

    put = next(c for c in calls if c["method"] == "PUT")
    assert put["path"] == "/api/1.0/tasks/task1"
    assert put["body"] == {"data": {"completed": True}}  # write-back body asserted
    story = next(c for c in calls if c["path"].endswith("/stories"))
    assert story["body"] == {"data": {"text": "done now"}}
    assert "updated" in result and "comment" in result


def test_update_status_section_move(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append({"path": request.url.path, "body": body})
        return httpx.Response(200, json={"data": {}})

    src = AsanaIssueSource(_config(), transport=httpx.MockTransport(handler))
    src.update_status("task1", "review")
    move = next(c for c in calls if "addTask" in c["path"])
    assert move["path"] == "/api/1.0/sections/sec9/addTask"
    assert move["body"] == {"data": {"task": "task1"}}


def test_update_status_unmapped_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    src = AsanaIssueSource(_config(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {}})))
    with pytest.raises(ValueError):
        src.update_status("task1", "nope")


def test_internal_base_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    for bad in ("http://localhost", "http://127.0.0.1/api", "http://172.16.0.1"):
        with pytest.raises(ValueError):
            AsanaIssueSource(_config(base_url=bad))


def test_health_ok_and_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    ok_handler = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {"gid": "u"}}))
    ok = AsanaIssueSource(_config(), transport=ok_handler)
    assert ok.health() == {"ok": True, "detail": "asana reachable"}

    bad = AsanaIssueSource(_config(), transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    assert bad.health()["ok"] is False

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    err = AsanaIssueSource(_config(), transport=httpx.MockTransport(boom))
    assert err.health()["ok"] is False  # never raises
