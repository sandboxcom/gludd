"""Unit tests for the self-contained Sentry connector.

The connector is loaded by file path via :mod:`importlib` so these tests do not
require a ``connectors`` package ``__init__`` to exist. Transport is mocked —
no network access occurs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

# --------------------------------------------------------------------------- #
# Load the connector module by file path (self-contained, no package import).
# --------------------------------------------------------------------------- #
_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "general_ludd"
    / "connectors"
    / "sentry.py"
)


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("_sentry_connector_under_test", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sentry = _load_module()
SentrySource = sentry.SentrySource
HttpResponse = sentry._SentryResponse


# --------------------------------------------------------------------------- #
# Mocked transport
# --------------------------------------------------------------------------- #
class RecordingTransport:
    """A mocked :class:`Transport` that records calls and replays canned responses."""

    def __init__(self, responses: dict[str, HttpResponse] | None = None, default: HttpResponse | None = None) -> None:
        self._responses = responses or {}
        self._default = default
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> HttpResponse:
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        # Match by path-prefix so query strings don't have to be exact.
        for key, resp in self._responses.items():
            if key in url:
                return resp
        if self._default is not None:
            return self._default
        raise AssertionError(f"unexpected URL requested: {url}")


def _json_response(status: int, payload: Any) -> Any:
    import json

    return HttpResponse(status=status, body=json.dumps(payload))


# --------------------------------------------------------------------------- #
# Canned payloads
# --------------------------------------------------------------------------- #
CANNED_ISSUES = [
    {
        "id": "1001",
        "shortId": "PROJ-7Q",
        "title": "ValueError: bad input",
        "culprit": "app.handlers in process",
        "level": "error",
        "status": "unresolved",
        "count": "1234",
        "lastSeen": "2026-06-15T12:00:00Z",
        "firstSeen": "2026-06-01T00:00:00Z",
        "project": {"slug": "backend", "name": "Backend"},
        "metadata": {"type": "ValueError", "value": "bad input", "commit": "abc123def"},
    },
    {
        "id": "1002",
        "shortId": "PROJ-8R",
        "title": "TimeoutError",
        "culprit": "",
        "level": "warning",
        "status": "resolved",
        "count": "42",
        "lastSeen": "2026-06-14T08:30:00Z",
        "metadata": {"type": "TimeoutError"},
    },
]

CANNED_EVENT = {
    "eventID": "ev-deadbeef",
    "id": "ev-deadbeef",
    "title": "ValueError: bad input",
    "culprit": "app.handlers in process",
    "level": "error",
    "dateCreated": "2026-06-15T12:00:01Z",
    "contexts": {
        "trace": {"trace_id": "trace-aaa111", "span_id": "span-bbb222"},
        "runtime": {"name": "CPython", "version": "3.11"},
    },
}


@pytest.fixture
def base_config() -> dict[str, Any]:
    return {
        "name": "sentry-prod",
        "token_env": "SENTRY_TEST_TOKEN",
        "org": "acme",
        "project": "backend",
        "base_url": "https://sentry.example.com",
        "timeout": 5.0,
    }


# --------------------------------------------------------------------------- #
# Contract / construction
# --------------------------------------------------------------------------- #
def test_kind_is_logs_classattr() -> None:
    assert SentrySource.KIND == "logs"


def test_name_attr_from_config(base_config: dict[str, Any]) -> None:
    src = SentrySource({**base_config, "transport": RecordingTransport()})
    assert src.name == "sentry-prod"


def test_default_base_url_is_sentry_io() -> None:
    src = SentrySource({"token_env": "X", "org": "o", "project": "p", "transport": RecordingTransport()})
    assert src.base_url == "https://sentry.io"


def test_missing_required_config_raises() -> None:
    with pytest.raises(ValueError):
        SentrySource({"org": "o", "project": "p"})  # no token_env
    with pytest.raises(ValueError):
        SentrySource({"token_env": "X", "project": "p"})  # no org
    with pytest.raises(ValueError):
        SentrySource({"token_env": "X", "org": "o"})  # no project


# --------------------------------------------------------------------------- #
# SSRF guard — literal internal hosts rejected, no DNS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_base",
    [
        "http://127.0.0.1:9000",
        "http://localhost:8080",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://169.254.169.254",  # cloud metadata
        "http://[::1]",
        "http://0.0.0.0",
    ],
)
def test_ssrf_internal_base_url_rejected(bad_base: str) -> None:
    with pytest.raises(ValueError):
        SentrySource(
            {
                "token_env": "X",
                "org": "o",
                "project": "p",
                "base_url": bad_base,
                "transport": RecordingTransport(),
            }
        )


def test_ssrf_public_dns_host_allowed_no_resolution(base_config: dict[str, Any]) -> None:
    # A name-based host is allowed: we do not resolve DNS.
    src = SentrySource({**base_config, "transport": RecordingTransport()})
    assert src.base_url == "https://sentry.example.com"


# --------------------------------------------------------------------------- #
# Bearer header
# --------------------------------------------------------------------------- #
def test_bearer_header_sent(base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "secret-tok-123")
    transport = RecordingTransport(default=_json_response(200, CANNED_ISSUES))
    src = SentrySource({**base_config, "transport": transport})
    src.query({"query": "is:unresolved"})
    assert transport.calls, "transport was never called"
    auth = transport.calls[0]["headers"].get("Authorization")
    assert auth == "Bearer secret-tok-123"


# --------------------------------------------------------------------------- #
# query() normalization
# --------------------------------------------------------------------------- #
def test_query_normalizes_issues(base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(responses={"/issues/": _json_response(200, CANNED_ISSUES)})
    src = SentrySource({**base_config, "transport": transport})

    records = src.query({"query": "is:unresolved", "statsPeriod": "14d", "limit": 10})
    assert len(records) == 2

    first = records[0]
    # ts == lastSeen
    assert first["ts"] == "2026-06-15T12:00:00Z"
    assert first["source"] == "sentry-prod"
    assert first["kind"] == "logs"
    # level
    assert first["level_or_status"] == "error"
    # message = title + culprit
    assert "ValueError: bad input" in first["message"]
    assert "app.handlers in process" in first["message"]
    # value = count (events) as a number
    assert first["value"] == 1234.0
    # labels
    assert first["labels"]["shortId"] == "PROJ-7Q"
    assert first["labels"]["status"] == "unresolved"
    assert first["labels"]["project"] == "backend"
    assert first["labels"]["commit"] == "abc123def"
    # raw is the untouched issue
    assert first["raw"] is CANNED_ISSUES[0] or first["raw"] == CANNED_ISSUES[0]

    second = records[1]
    assert second["level_or_status"] == "warning"
    assert second["value"] == 42.0
    assert second["labels"]["shortId"] == "PROJ-8R"
    # no commit in metadata -> no commit label
    assert "commit" not in second["labels"]


def test_query_request_url_carries_query_and_stats_period(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(200, CANNED_ISSUES))
    src = SentrySource({**base_config, "transport": transport})
    src.query({"query": "is:unresolved", "statsPeriod": "14d", "limit": 5})
    url = transport.calls[0]["url"]
    assert "/api/0/projects/acme/backend/issues/" in url
    assert "statsPeriod=14d" in url
    assert "is%3Aunresolved" in url or "is:unresolved" in url
    assert "limit=5" in url


def test_query_respects_limit(base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(200, CANNED_ISSUES))
    src = SentrySource({**base_config, "transport": transport})
    records = src.query({"limit": 1})
    assert len(records) == 1


def test_query_non_2xx_returns_empty(base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(500, {"detail": "boom"}))
    src = SentrySource({**base_config, "transport": transport})
    assert src.query({"query": "is:unresolved"}) == []


# --------------------------------------------------------------------------- #
# fetch_event()
# --------------------------------------------------------------------------- #
def test_fetch_event_normalizes_and_surfaces_trace(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(responses={"/events/latest/": _json_response(200, CANNED_EVENT)})
    src = SentrySource({**base_config, "transport": transport})

    rec = src.fetch_event("1001")
    assert rec is not None
    assert rec["kind"] == "logs"
    assert rec["source"] == "sentry-prod"
    assert rec["level_or_status"] == "error"
    assert rec["ts"] == "2026-06-15T12:00:01Z"
    assert rec["labels"]["trace_id"] == "trace-aaa111"
    assert rec["labels"]["span_id"] == "span-bbb222"
    assert rec["labels"]["issueId"] == "1001"
    assert rec["labels"]["eventId"] == "ev-deadbeef"
    # event endpoint URL is correct
    url = transport.calls[0]["url"]
    assert "/api/0/issues/1001/events/latest/" in url


def test_fetch_event_non_2xx_returns_none(base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(404, {"detail": "not found"}))
    src = SentrySource({**base_config, "transport": transport})
    assert src.fetch_event("nope") is None


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #
def test_health_ok_on_200(base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(responses={"/api/0/": _json_response(200, {"version": "0"})})
    src = SentrySource({**base_config, "transport": transport})
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok_on_401(base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "")
    transport = RecordingTransport(default=_json_response(401, {"detail": "Invalid token"}))
    src = SentrySource({**base_config, "transport": transport})
    result = src.health()
    assert result["ok"] is False
    assert "401" in result["detail"]


def test_health_never_raises_on_transport_error(base_config: dict[str, Any]) -> None:
    class BoomTransport:
        def get(self, url: str, *, headers: dict[str, str], timeout: float) -> Any:
            raise ConnectionError("network down")

    src = SentrySource({**base_config, "transport": BoomTransport()})
    result = src.health()
    assert result["ok"] is False
    assert "detail" in result
