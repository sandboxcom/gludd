"""Structural tests for issue_sources/gitlab_issues.py."""

from __future__ import annotations

import os

import pytest

from general_ludd.issue_sources.gitlab_issues import GitLabIssueSource
from general_ludd.issue_sources.gitlab_issues import _PRIORITY_LABELS


class FakeHTTPResponse:
    def __init__(self, status_code: int, data: dict | list):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeHTTPTransport:
    def __init__(self, responses: list[FakeHTTPResponse] | None = None):
        self.responses = responses or []
        self.calls: list[dict] = []

    def __call__(self, method, url, *, headers, params=None, json=None, timeout):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "params": params,
            "json": json,
            "timeout": timeout,
        })
        if self.responses:
            return self.responses.pop(0)
        return FakeHTTPResponse(200, [])


def _make_config(**overrides):
    config = {"base_url": "https://gitlab.example.com", "project_id": "42"}
    config.update(overrides)
    return config


def test_gitlab_construction_defaults():
    env = {"GITLAB_TOKEN": "glpat-fake"}
    source = GitLabIssueSource(_make_config(), transport=FakeHTTPTransport(), env=env)
    assert source._base_url == "https://gitlab.example.com"
    assert source._project_id == "42"
    assert source.name == "gitlab"


def test_gitlab_construction_no_token():
    source = GitLabIssueSource(
        _make_config(),
        transport=FakeHTTPTransport(),
        env={},
    )
    assert source._token() == ""


def test_gitlab_health_no_project_id():
    transport = FakeHTTPTransport()
    source = GitLabIssueSource(
        {"base_url": "https://gitlab.com"},
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.health()
    assert result["ok"] is False
    assert "missing" in result["detail"]


def test_gitlab_health_no_token():
    transport = FakeHTTPTransport()
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={},
    )
    result = source.health()
    assert result["ok"] is False
    assert "missing token" in result["detail"]


def test_gitlab_health_ok():
    transport = FakeHTTPTransport([FakeHTTPResponse(200, {"id": 42})])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.health()
    assert result["ok"] is True


def test_gitlab_health_loopback_blocked():
    source = GitLabIssueSource(
        {"base_url": "http://127.0.0.1", "project_id": "1"},
        transport=FakeHTTPTransport(),
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.health()
    assert result["ok"] is False


def test_gitlab_normalize_minimal():
    issue = {"iid": 1, "title": "Test", "description": "desc"}
    result = GitLabIssueSource._normalize(issue)
    assert result["external_id"] == "1"
    assert result["source"] == "gitlab"
    assert result["title"] == "Test"
    assert result["status"] == "open"
    assert result["assignee"] is None
    assert result["labels"] == []
    assert result["priority"] is None


def test_gitlab_normalize_closed():
    issue = {"iid": 5, "title": "Done", "description": "", "state": "closed"}
    result = GitLabIssueSource._normalize(issue)
    assert result["status"] == "closed"


def test_gitlab_normalize_with_labels():
    issue = {
        "iid": 10,
        "title": "High pri bug",
        "description": "",
        "labels": [{"name": "P0"}, {"name": "bug"}],
    }
    result = GitLabIssueSource._normalize(issue)
    assert result["labels"] == ["P0", "bug"]
    assert result["priority"] == "critical"


def test_gitlab_normalize_assignee():
    issue = {
        "iid": 3,
        "title": "Assigned",
        "description": "",
        "assignee": {"username": "alice"},
    }
    result = GitLabIssueSource._normalize(issue)
    assert result["assignee"] == "alice"


def test_gitlab_normalize_assignees_fallback():
    issue = {
        "iid": 4,
        "title": "Multi assignee",
        "description": "",
        "assignees": [{"username": "bob"}],
    }
    result = GitLabIssueSource._normalize(issue)
    assert result["assignee"] == "bob"


def test_gitlab_fetch_issues_open(monkeypatch):
    transport = FakeHTTPTransport([FakeHTTPResponse(200, [
        {"iid": 1, "title": "Bug", "description": ""},
    ])])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    issues = source.fetch_issues({"state": "open"})
    assert len(issues) == 1
    assert issues[0]["external_id"] == "1"
    call = transport.calls[0]
    assert call["params"]["state"] == "opened"


def test_gitlab_fetch_issues_closed(monkeypatch):
    transport = FakeHTTPTransport([FakeHTTPResponse(200, [
        {"iid": 2, "title": "Done", "description": "", "state": "closed"},
    ])])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    issues = source.fetch_issues({"state": "closed"})
    assert len(issues) == 1
    assert issues[0]["status"] == "closed"


def test_gitlab_update_status(monkeypatch):
    transport = FakeHTTPTransport([FakeHTTPResponse(200, {"iid": 1})])
    source = GitLabIssueSource(
        _make_config(),
        transport=transport,
        env={"GITLAB_TOKEN": "t"},
    )
    result = source.update_status("1", "closed")
    assert result["ok"] is True
    assert result["state_event"] == "close"


def test_priority_labels_complete():
    assert _PRIORITY_LABELS["p0"] == "critical"
    assert _PRIORITY_LABELS["p1"] == "high"
    assert _PRIORITY_LABELS["p2"] == "medium"
    assert _PRIORITY_LABELS["p3"] == "low"
    assert _PRIORITY_LABELS["critical"] == "critical"
    assert _PRIORITY_LABELS["urgent"] == "critical"
