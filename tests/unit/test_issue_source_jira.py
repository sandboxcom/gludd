"""Structural tests for issue_sources/jira.py."""

from __future__ import annotations

import httpx
import pytest

from general_ludd.issue_sources.jira import JiraIssueSource, _adf, _parse_updated


class FakeHttpResponse:
    def __init__(self, status_code: int, data: dict | list):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeHttpTransport:
    def __init__(self, responses: list[FakeHttpResponse] | None = None):
        self.responses = responses or []
        self.requests: list[dict] = []

    def request(self, method, url, *, headers=None, json=None, timeout=None):
        self.requests.append({
            "method": method,
            "url": url,
            "headers": headers,
            "body": json,
            "timeout": timeout,
        })
        if self.responses:
            return self.responses.pop(0)
        return FakeHttpResponse(200, {"issues": []})


def _make_config(**overrides):
    config = {"base_url": "https://example.atlassian.net", "project": "TEST"}
    config.update(overrides)
    return config


def test_jira_requires_base_url():
    with pytest.raises(ValueError, match="requires 'base_url'"):
        JiraIssueSource({})


def test_jira_rejects_ftp_url():
    with pytest.raises(ValueError, match="must be http"):
        JiraIssueSource({"base_url": "ftp://example.com"}, transport=FakeHttpTransport())


def test_jira_rejects_loopback_url():
    with pytest.raises(ValueError, match="blocked"):
        JiraIssueSource({"base_url": "http://127.0.0.1:8080"}, transport=FakeHttpTransport())


def test_jira_construction_defaults(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "fake-token")
    source = JiraIssueSource(
        _make_config(),
        transport=FakeHttpTransport(),
    )
    assert source.base_url == "https://example.atlassian.net"
    assert source.name == "jira"


def test_jira_closes_only_its_default_transport(monkeypatch):
    class Client:
        closed = 0

        def close(self):
            self.closed += 1

    client = Client()
    monkeypatch.setattr(httpx, "Client", lambda: client)
    owned = JiraIssueSource(_make_config())
    owned.close()
    owned.close()
    assert client.closed == 1

    injected = FakeHttpTransport()
    injected.close = lambda: pytest.fail("injected transport is externally owned")
    JiraIssueSource(_make_config(), transport=injected).close()


def test_jira_auth_header(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok123")
    source = JiraIssueSource(
        _make_config(),
        transport=FakeHttpTransport(),
    )
    header = source._auth_header()
    assert header.startswith("Basic ")


def test_jira_auth_header_requires_both_credentials(monkeypatch):
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    source = JiraIssueSource(_make_config(), transport=FakeHttpTransport())
    with pytest.raises(ValueError, match="Missing Jira credentials"):
        source._auth_header()


def test_jira_health_ok(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "u@e.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    transport = FakeHttpTransport([FakeHttpResponse(200, {"displayName": "test"})])
    source = JiraIssueSource(_make_config(), transport=transport)
    result = source.health()
    assert result["ok"] is True


def test_jira_health_fail(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "u@e.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    transport = FakeHttpTransport([FakeHttpResponse(500, {})])
    source = JiraIssueSource(_make_config(), transport=transport)
    result = source.health()
    assert result["ok"] is False


def test_jira_fetch_issues(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "u@e.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    issue_data = {
        "issues": [{
            "key": "TEST-1",
            "fields": {
                "summary": "Fix bug",
                "description": "A bug",
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "Alice"},
                "labels": ["bug"],
                "priority": {"name": "High"},
                "updated": "2026-07-01T10:00:00.000+0000",
            },
        }],
    }
    transport = FakeHttpTransport([FakeHttpResponse(200, issue_data)])
    source = JiraIssueSource(_make_config(), transport=transport)
    issues = source.fetch_issues({"max_results": 10})
    assert len(issues) == 1
    assert issues[0]["external_id"] == "TEST-1"
    assert issues[0]["title"] == "Fix bug"
    assert issues[0]["status"] == "In Progress"
    assert issues[0]["assignee"] == "Alice"


def test_jira_normalize_missing_fields(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "u@e.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    source = JiraIssueSource(_make_config(), transport=FakeHttpTransport())
    normalized = source._normalize({"key": "T-1", "fields": {}})
    assert normalized["external_id"] == "T-1"
    assert normalized["source"] == "jira"
    assert normalized["title"] is None
    assert normalized["description"] is None
    assert normalized["status"] is None
    assert normalized["assignee"] is None


def test_jira_update_status_posts_matching_transition_with_comment(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "u@e.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    transport = FakeHttpTransport([
        FakeHttpResponse(
            200,
            {
                "transitions": [
                    {"id": "31", "name": "Finish", "to": {"name": "Done"}},
                ],
            },
        ),
        FakeHttpResponse(204, {}),
    ])
    source = JiraIssueSource(_make_config(), transport=transport)

    result = source.update_status("TEST-1", "done", comment="shipped")

    assert result == {
        "external_id": "TEST-1",
        "status": "done",
        "transition_id": "31",
        "ok": True,
    }
    assert transport.requests[1]["body"]["transition"] == {"id": "31"}
    assert (
        transport.requests[1]["body"]["update"]["comment"][0]["add"]["body"]
        == _adf("shipped")
    )


def test_jira_update_status_rejects_unavailable_transition(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "u@e.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    transport = FakeHttpTransport([
        FakeHttpResponse(
            200,
            {"transitions": [{"id": "1", "to": {"name": "In Progress"}}]},
        ),
    ])
    source = JiraIssueSource(_make_config(), transport=transport)

    with pytest.raises(ValueError, match="No Jira transition"):
        source.update_status("TEST-1", "Done")


@pytest.mark.parametrize(("status_code", "expected"), [(201, True), (500, False)])
def test_jira_add_comment_reports_http_status(
    monkeypatch,
    status_code,
    expected,
):
    monkeypatch.setenv("JIRA_EMAIL", "u@e.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    transport = FakeHttpTransport([FakeHttpResponse(status_code, {})])
    source = JiraIssueSource(_make_config(), transport=transport)

    result = source.add_comment("TEST-1", "hello")

    assert result["ok"] is expected
    assert result["detail"] == f"HTTP {status_code}"
    assert transport.requests[0]["body"] == {"body": _adf("hello")}


def test_parse_updated_normal():
    ts = _parse_updated("2026-06-12T10:30:00.000+0000")
    assert ts > 0.0


def test_parse_updated_none():
    assert _parse_updated(None) == 0.0


def test_parse_updated_empty():
    assert _parse_updated("") == 0.0


def test_parse_updated_offset_with_colon():
    ts = _parse_updated("2026-06-12T10:30:00.000+00:00")
    assert ts > 0.0


def test_parse_updated_invalid_returns_zero():
    assert _parse_updated("not-a-date") == 0.0


def test_adf():
    result = _adf("hello world")
    assert result["type"] == "doc"
    assert result["version"] == 1
    content = result["content"][0]["content"][0]
    assert content["text"] == "hello world"
    assert content["type"] == "text"
