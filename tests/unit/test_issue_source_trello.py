"""Unit tests for TrelloIssueSource (mocked httpx transport)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from general_ludd.issue_sources.trello import SYSTEM, TrelloIssueSource

_LISTS = [
    {"id": "list_todo", "name": "To Do"},
    {"id": "list_done", "name": "Done"},
]

_CARDS = [
    {
        "id": "card1",
        "name": "Fix login",
        "desc": "Login is broken",
        "idList": "list_todo",
        "idMembers": ["mem1"],
        "labels": [{"name": "bug"}, {"name": "urgent"}],
        "shortUrl": "https://trello.com/c/card1",
        "dateLastActivity": "2026-06-10T00:00:00Z",
    }
]


def _config(**over: Any) -> dict[str, Any]:
    base = {
        "board_id": "board123",
        "base_url": "https://api.trello.com",
        "status_list_map": {"done": "list_done"},
    }
    base.update(over)
    return base


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "KEY123")
    monkeypatch.setenv("TRELLO_TOKEN", "TOK456")


def test_fetch_issues_normalizes_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lists"):
            return httpx.Response(200, json=_LISTS)
        if request.url.path.endswith("/cards"):
            seen["card_params"] = dict(request.url.params)
            return httpx.Response(200, json=_CARDS)
        return httpx.Response(404)

    src = TrelloIssueSource(_config(), transport=httpx.MockTransport(handler))
    issues = src.fetch_issues({})

    assert src.SYSTEM == "trello" == SYSTEM
    assert len(issues) == 1
    issue = issues[0]
    assert issue["external_id"] == "card1"
    assert issue["source"] == "trello"
    assert issue["title"] == "Fix login"
    assert issue["status"] == "To Do"
    assert issue["labels"] == ["bug", "urgent"]
    assert issue["assignee"] == "mem1"
    assert issue["url"] == "https://trello.com/c/card1"
    assert issue["raw"] == _CARDS[0]
    # auth comes from env, never hardcoded
    assert seen["card_params"]["key"] == "KEY123"
    assert seen["card_params"]["token"] == "TOK456"


def test_update_status_moves_card_and_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {"method": request.method, "path": request.url.path, "params": dict(request.url.params)}
        )
        return httpx.Response(200, json={"id": "card1"})

    src = TrelloIssueSource(_config(), transport=httpx.MockTransport(handler))
    result = src.update_status("card1", "done", comment="moving on")

    put = next(c for c in calls if c["method"] == "PUT")
    assert put["path"] == "/1/cards/card1"
    assert put["params"]["idList"] == "list_done"  # mapped destination list
    comment = next(c for c in calls if c["method"] == "POST")
    assert comment["path"] == "/1/cards/card1/actions/comments"
    assert comment["params"]["text"] == "moving on"
    assert "moved" in result and "comment" in result


def test_update_status_unmapped_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    src = TrelloIssueSource(_config(), transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(ValueError):
        src.update_status("card1", "nope")


def test_internal_base_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    for bad in ("http://localhost/x", "http://127.0.0.1", "http://10.0.0.5", "ftp://api.trello.com"):
        with pytest.raises(ValueError):
            TrelloIssueSource(_config(base_url=bad))


def test_health_ok_and_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    ok = TrelloIssueSource(_config(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert ok.health() == {"ok": True, "detail": "trello reachable"}

    bad = TrelloIssueSource(_config(), transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    h = bad.health()
    assert h["ok"] is False

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    err = TrelloIssueSource(_config(), transport=httpx.MockTransport(boom))
    assert err.health()["ok"] is False  # never raises
