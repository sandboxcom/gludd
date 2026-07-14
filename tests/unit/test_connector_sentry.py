"""Structural tests for Sentry connector."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.sentry import (
    SentrySource,
    Transport,
    _SentryResponse,
)


class FakeTransport:
    def __init__(self, responses: list[_SentryResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _SentryResponse:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(status: int, body: Any = None) -> _SentryResponse:
    import json
    raw = json.dumps(body).encode() if body is not None else b"{}"
    return _SentryResponse(status=status, body=raw)


def _cfg(**kw: Any) -> dict[str, Any]:
    base = {"org": "myorg", "project": "myproject", "token_env": "SENTRY_TOKEN"}
    base.update(kw)
    return base


CANNED_ISSUES = [
    {
        "id": "1", "title": "TypeError in handler", "culprit": "app.py:42",
        "status": "unresolved", "level": "error", "count": "5",
        "lastSeen": "2026-01-15T10:30:00Z", "shortId": "PROJ-1",
        "project": {"slug": "myproject"},
    }
]


class TestContract:
    def test_kind(self) -> None:
        assert SentrySource.KIND == "logs"

    def test_transport_protocol_exists(self) -> None:
        assert Transport is not None

    def test_response_slots(self) -> None:
        r = _SentryResponse(200, b'{"key": "val"}')
        assert r.status == 200
        assert r.json() == {"key": "val"}


class TestInit:
    def test_minimal(self) -> None:
        src = SentrySource(_cfg())
        assert src.name == "sentry"
        assert src.org == "myorg"
        assert src.project == "myproject"

    def test_missing_token_env(self) -> None:
        with pytest.raises(ValueError, match="token_env"):
            SentrySource({"org": "x", "project": "y"})

    def test_missing_org(self) -> None:
        with pytest.raises(ValueError, match="org"):
            SentrySource({"token_env": "T", "project": "y"})

    def test_missing_project(self) -> None:
        with pytest.raises(ValueError, match="project"):
            SentrySource({"token_env": "T", "org": "x"})

    def test_not_dict_raises(self) -> None:
        with pytest.raises(TypeError):
            SentrySource("not a dict")  # type: ignore[arg-type]

    def test_custom_name(self) -> None:
        src = SentrySource(_cfg(name="ops-sentry"))
        assert src.name == "ops-sentry"


class TestSSRF:
    def test_localhost_rejected(self) -> None:
        with pytest.raises(ValueError):
            SentrySource(_cfg(base_url="http://localhost/"))

    def test_metadata_rejected(self) -> None:
        with pytest.raises(ValueError):
            SentrySource(_cfg(base_url="http://169.254.169.254/"))

    def test_invalid_scheme(self) -> None:
        with pytest.raises(ValueError):
            SentrySource(_cfg(base_url="ftp://evil.com/"))

    def test_no_host(self) -> None:
        with pytest.raises(ValueError):
            SentrySource(_cfg(base_url="http:///path"))


class TestHealth:
    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([_resp(200, {})])
        src = SentrySource(_cfg(transport=t))
        r = src.health()
        assert r["ok"] is True

    def test_transport_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([RuntimeError("down")])
        src = SentrySource(_cfg(transport=t))
        r = src.health()
        assert r["ok"] is False

    def test_bad_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([_resp(401)])
        src = SentrySource(_cfg(transport=t))
        r = src.health()
        assert r["ok"] is False


class TestQuery:
    def test_returns_issues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([_resp(200, CANNED_ISSUES)])
        src = SentrySource(_cfg(transport=t))
        records = src.query({})
        assert len(records) == 1
        r = records[0]
        assert r["source"] == "sentry"
        assert r["kind"] == "logs"
        assert r["level_or_status"] == "error"
        assert "TypeError" in r["message"]
        assert r["labels"]["shortId"] == "PROJ-1"
        assert r["labels"]["project"] == "myproject"

    def test_empty_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([_resp(200, [])])
        src = SentrySource(_cfg(transport=t))
        assert src.query({}) == []

    def test_non_list_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([_resp(200, {"not": "a list"})])
        src = SentrySource(_cfg(transport=t))
        assert src.query({}) == []

    def test_http_error_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([_resp(500)])
        src = SentrySource(_cfg(transport=t))
        assert src.query({}) == []

    def test_query_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([_resp(200, [])])
        src = SentrySource(_cfg(transport=t))
        src.query({"query": "is:unresolved", "statsPeriod": "7d", "limit": 50})
        url = t.calls[0]["url"]
        assert "is%3Aunresolved" in url or "is:unresolved" in url
        assert "statsPeriod=7d" in url
        assert "limit=50" in url

    def test_limit_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        issues = [{"id": str(i), "title": "Test", "culprit": "x", "status": "resolved", "level": "warning", "count": "1"} for i in range(10)]
        t = FakeTransport([_resp(200, issues)])
        src = SentrySource(_cfg(transport=t))
        records = src.query({"limit": 3})
        assert len(records) == 3

    def test_bearer_token_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "my-token-abc")
        t = FakeTransport([_resp(200, [])])
        src = SentrySource(_cfg(transport=t))
        src.query({})
        headers = t.calls[0]["headers"]
        assert headers["Authorization"] == "Bearer my-token-abc"


class TestFetchEvent:
    def test_fetch_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        event = {"id": "evt1", "title": "Error detail", "culprit": "app.py", "level": "error", "dateCreated": "2026-01-01T00:00:00Z"}
        t = FakeTransport([_resp(200, event)])
        src = SentrySource(_cfg(transport=t))
        result = src.fetch_event("issue-1")
        assert result is not None
        assert result["kind"] == "logs"
        assert result["labels"]["issueId"] == "issue-1"

    def test_fetch_event_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([_resp(404)])
        src = SentrySource(_cfg(transport=t))
        assert src.fetch_event("issue-1") is None

    def test_fetch_event_non_dict_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        t = FakeTransport([_resp(200, ["list not dict"])])
        src = SentrySource(_cfg(transport=t))
        assert src.fetch_event("issue-1") is None

    def test_fetch_event_trace_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TOKEN", "tok")
        event = {
            "id": "evt1", "title": "Test", "culprit": "x",
            "contexts": {"trace": {"trace_id": "abc123", "span_id": "span1"}},
            "dateCreated": "2026-01-01T00:00:00Z",
        }
        t = FakeTransport([_resp(200, event)])
        src = SentrySource(_cfg(transport=t))
        result = src.fetch_event("issue-1")
        assert result is not None
        assert result["labels"]["trace_id"] == "abc123"
        assert result["labels"]["span_id"] == "span1"
