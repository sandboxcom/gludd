"""Unit tests for AzureBoardsIssueSource (mocked httpx transport)."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from general_ludd.issue_sources.azure_boards import SYSTEM, AzureBoardsIssueSource

_WORK_ITEM = {
    "id": 42,
    "url": "https://dev.azure.com/org/_apis/wit/workItems/42",
    "fields": {
        "System.Title": "Investigate timeout",
        "System.Description": "<div>desc</div>",
        "System.State": "Active",
        "System.AssignedTo": {"displayName": "Linus"},
        "System.Tags": "infra; urgent",
        "System.ChangedDate": "2026-06-10T00:00:00Z",
        "Microsoft.VSTS.Common.Priority": 1,
    },
}


def _config(**over: Any) -> dict[str, Any]:
    base = {
        "org": "org",
        "project": "proj",
        "base_url": "https://dev.azure.com",
    }
    base.update(over)
    return base


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "pat_secret")


def test_fetch_issues_wiql_then_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        if request.url.path.endswith("/wiql"):
            seen["wiql_body"] = json.loads(request.content)
            return httpx.Response(200, json={"workItems": [{"id": 42}]})
        if request.url.path.endswith("/workitems"):
            seen["ids"] = request.url.params.get("ids")
            return httpx.Response(200, json={"value": [_WORK_ITEM]})
        return httpx.Response(404)

    src = AzureBoardsIssueSource(_config(), transport=httpx.MockTransport(handler))
    issues = src.fetch_issues({})

    assert src.SYSTEM == "azure_boards" == SYSTEM
    issue = issues[0]
    assert issue["external_id"] == "42"
    assert issue["source"] == "azure_boards"
    assert issue["title"] == "Investigate timeout"
    assert issue["status"] == "Active"
    assert issue["assignee"] == "Linus"
    assert issue["labels"] == ["infra", "urgent"]
    assert issue["priority"] == 1
    assert seen["ids"] == "42"
    # Basic ':PAT' auth derived from env
    expected = "Basic " + base64.b64encode(b":pat_secret").decode()
    assert seen["auth"] == expected


def test_update_status_patches_state_and_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "ctype": request.headers.get("Content-Type"),
                "body": body,
            }
        )
        return httpx.Response(200, json={"id": 42})

    src = AzureBoardsIssueSource(_config(), transport=httpx.MockTransport(handler))
    result = src.update_status("42", "Closed", comment="resolved")

    patch = next(c for c in calls if c["method"] == "PATCH")
    assert patch["path"] == "/org/proj/_apis/wit/workitems/42"
    assert patch["ctype"] == "application/json-patch+json"
    assert patch["body"] == [{"op": "add", "path": "/fields/System.State", "value": "Closed"}]
    comment = next(c for c in calls if c["path"].endswith("/comments"))
    assert comment["body"] == {"text": "resolved"}
    assert "updated" in result and "comment" in result


def test_internal_base_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    for bad in ("http://localhost", "http://127.0.0.1", "http://10.1.2.3", "gopher://dev.azure.com"):
        with pytest.raises(ValueError):
            AzureBoardsIssueSource(_config(base_url=bad))


def test_health_ok_and_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    ok = AzureBoardsIssueSource(_config(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert ok.health() == {"ok": True, "detail": "azure boards reachable"}

    bad = AzureBoardsIssueSource(_config(), transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert bad.health()["ok"] is False

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    err = AzureBoardsIssueSource(_config(), transport=httpx.MockTransport(boom))
    assert err.health()["ok"] is False  # never raises
