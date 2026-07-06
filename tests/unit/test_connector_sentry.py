"""Unit tests for the Sentry connector.

Uses a direct package import (matching ``test_connector_rollbar.py``) so
coverage is attributed to ``general_ludd.connectors.sentry``. Transport is
mocked — no network access occurs.
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.connectors.sentry import (
    SentrySource,
    Transport,
    _SentryResponse,
    _UrllibTransport,
)

# Backwards-compat alias used by the canned-response helpers below.
HttpResponse = _SentryResponse


# --------------------------------------------------------------------------- #
# Mocked transport
# --------------------------------------------------------------------------- #
class RecordingTransport:
    """A mocked :class:`Transport` that records calls and replays canned responses."""

    def __init__(
        self, responses: dict[str, HttpResponse] | None = None, default: HttpResponse | None = None
    ) -> None:
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


def _json_response(status: int, payload: Any) -> HttpResponse:
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
    src = SentrySource(
        {"token_env": "X", "org": "o", "project": "p", "transport": RecordingTransport()}
    )
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
def test_query_normalizes_issues(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_query_respects_limit(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(200, CANNED_ISSUES))
    src = SentrySource({**base_config, "transport": transport})
    records = src.query({"limit": 1})
    assert len(records) == 1


def test_query_non_2xx_returns_empty(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
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
    transport = RecordingTransport(
        responses={"/events/latest/": _json_response(200, CANNED_EVENT)}
    )
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


def test_fetch_event_non_2xx_returns_none(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(404, {"detail": "not found"}))
    src = SentrySource({**base_config, "transport": transport})
    assert src.fetch_event("nope") is None


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #
def test_health_ok_on_200(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(responses={"/api/0/": _json_response(200, {"version": "0"})})
    src = SentrySource({**base_config, "transport": transport})
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok_on_401(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
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


# =========================================================================== #
# EDGE-CASE TESTS — coverage for error branches and unusual inputs.
# =========================================================================== #


# --------------------------------------------------------------------------- #
# Construction-time input validation
# --------------------------------------------------------------------------- #
def test_non_dict_config_raises_type_error() -> None:
    """A non-mapping config must raise TypeError (not a silent coercion)."""
    with pytest.raises(TypeError, match="config must be a dict"):
        SentrySource(["not", "a", "dict"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="config must be a dict"):
        SentrySource("token_env=X")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="config must be a dict"):
        SentrySource(None)  # type: ignore[arg-type]


def test_non_numeric_timeout_raises_value_error() -> None:
    """A timeout that cannot be coerced to float must raise ValueError."""
    with pytest.raises(ValueError, match="timeout"):
        SentrySource(
            {
                "token_env": "X",
                "org": "o",
                "project": "p",
                "timeout": "not-a-number",
            }
        )
    # A list is not numeric and must also be rejected.
    with pytest.raises(ValueError, match="timeout"):
        SentrySource(
            {
                "token_env": "X",
                "org": "o",
                "project": "p",
                "timeout": ["30"],
            }
        )


def test_timeout_is_numeric_and_passes_through_to_transport(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured timeout must be forwarded verbatim to transport.get()."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(200, CANNED_ISSUES))
    src = SentrySource({**base_config, "timeout": 12.5, "transport": transport})
    assert src.timeout == 12.5
    src.query({"query": "is:unresolved"})
    assert transport.calls, "transport was never called"
    assert transport.calls[0]["timeout"] == 12.5


def test_invalid_base_url_scheme_rejected() -> None:
    """Non-http(s) schemes must be rejected by the SSRF/scheme validator."""
    with pytest.raises(ValueError, match=r"http\(s\)"):
        SentrySource(
            {
                "token_env": "X",
                "org": "o",
                "project": "p",
                "base_url": "ftp://sentry.example.com",
            }
        )


def test_base_url_without_host_rejected() -> None:
    """A base_url with no host must be rejected."""
    with pytest.raises(ValueError, match="host"):
        SentrySource(
            {
                "token_env": "X",
                "org": "o",
                "project": "p",
                "base_url": "http://",
            }
        )


# --------------------------------------------------------------------------- #
# fetch_event() — URL-encoding of hostile issue IDs (path-injection guard)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "hostile_id",
    [
        "../../../etc/passwd",
        "1/2/3",
        "1001?evil=1",
        "1001#fragment",
        "1001;rm -rf",
        "1001%00null",
    ],
)
def test_fetch_event_url_encodes_hostile_issue_ids(
    base_config: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    hostile_id: str,
) -> None:
    """Hostile issue IDs must be percent-encoded so they cannot break out of the path segment."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(200, CANNED_EVENT))
    src = SentrySource({**base_config, "transport": transport})
    src.fetch_event(hostile_id)
    assert transport.calls, "transport was never called"
    url = transport.calls[0]["url"]
    # The encoded ID sits between "/issues/" and "/events/latest/".
    # No raw "/" or "?" may appear inside that segment — that would indicate
    # path traversal or query injection.
    segment = url.split("/issues/", 1)[1].split("/events/latest/", 1)[0]
    assert "/" not in segment, f"raw '/' leaked into issue-id segment: {segment!r}"
    assert "?" not in segment, f"raw '?' leaked into issue-id segment: {segment!r}"
    # And the encoded form must be present in the URL somewhere.
    import urllib.parse

    assert urllib.parse.quote(hostile_id, safe="") in url


# --------------------------------------------------------------------------- #
# query() — malformed payloads and inputs
# --------------------------------------------------------------------------- #
def test_query_with_non_dict_payload_returns_empty(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 2xx response whose body is JSON but not a list must yield [] (not crash)."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(
        default=_json_response(200, {"detail": "unexpected envelope"})
    )
    src = SentrySource({**base_config, "transport": transport})
    assert src.query({"query": "is:unresolved"}) == []


def test_query_with_non_list_items_in_payload_skipped(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-dict entries inside the issues list must be silently skipped."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    payload = [CANNED_ISSUES[0], "not-a-dict", None, 42, CANNED_ISSUES[1]]
    transport = RecordingTransport(default=_json_response(200, payload))
    src = SentrySource({**base_config, "transport": transport})
    records = src.query({})
    assert len(records) == 2
    assert {r["labels"]["shortId"] for r in records} == {"PROJ-7Q", "PROJ-8R"}


def test_query_invalid_limit_falls_back_to_default(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-numeric ``limit`` must fall back to the default (100), not crash."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(200, CANNED_ISSUES))
    src = SentrySource({**base_config, "transport": transport})
    records = src.query({"limit": "not-a-number"})
    assert len(records) == 2  # both canned issues (under the default cap)
    # The URL must carry the default limit value.
    assert "limit=100" in transport.calls[0]["url"]


def test_query_with_none_spec_uses_defaults(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``query(None)`` must behave like ``query({})`` — no crash, default params."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(200, CANNED_ISSUES))
    src = SentrySource({**base_config, "transport": transport})
    records = src.query(None)
    assert len(records) == 2
    url = transport.calls[0]["url"]
    # statsPeriod and limit defaults must be present.
    assert "statsPeriod=24h" in url
    assert "limit=100" in url


# --------------------------------------------------------------------------- #
# _normalize_issue — malformed / partial payloads
# --------------------------------------------------------------------------- #
def test_normalize_issue_with_non_numeric_count_sets_value_none(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``count`` that cannot be parsed as float must yield ``value=None``."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    payload = [
        {"id": "x", "count": "not-a-number", "lastSeen": "2026-01-01T00:00:00Z", "level": "error"},
        {"id": "y", "count": None, "lastSeen": "2026-01-01T00:00:00Z", "level": "info"},
    ]
    transport = RecordingTransport(default=_json_response(200, payload))
    src = SentrySource({**base_config, "transport": transport})
    records = src.query({})
    assert [r["value"] for r in records] == [None, None]


def test_normalize_issue_with_missing_project_uses_config_project(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An issue with no ``project`` field must fall back to the configured project slug."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    payload = [{"id": "x", "lastSeen": "2026-01-01T00:00:00Z", "level": "error"}]
    transport = RecordingTransport(default=_json_response(200, payload))
    src = SentrySource({**base_config, "transport": transport})
    records = src.query({})
    assert records[0]["labels"]["project"] == base_config["project"]


# --------------------------------------------------------------------------- #
# _normalize_event — missing / malformed contexts
# --------------------------------------------------------------------------- #
def test_normalize_event_without_contexts_still_returns_record(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An event with no ``contexts`` key must still normalize (no trace labels)."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    event = {
        "eventID": "ev-no-ctx",
        "title": "boom",
        "level": "error",
        "dateCreated": "2026-06-15T12:00:01Z",
    }
    transport = RecordingTransport(default=_json_response(200, event))
    src = SentrySource({**base_config, "transport": transport})
    rec = src.fetch_event("1001")
    assert rec is not None
    assert rec["labels"]["issueId"] == "1001"
    assert rec["labels"]["eventId"] == "ev-no-ctx"
    # No contexts -> no trace_id / span_id labels.
    assert "trace_id" not in rec["labels"]
    assert "span_id" not in rec["labels"]
    assert "runtime" not in rec["labels"]


def test_normalize_event_with_non_dict_contexts_is_safe(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-dict ``contexts`` value must not crash normalization."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    event = {
        "eventID": "ev-bad-ctx",
        "title": "boom",
        "level": "error",
        "dateCreated": "2026-06-15T12:00:01Z",
        "contexts": "not-a-dict",
    }
    transport = RecordingTransport(default=_json_response(200, event))
    src = SentrySource({**base_config, "transport": transport})
    rec = src.fetch_event("1001")
    assert rec is not None
    assert "trace_id" not in rec["labels"]


def test_normalize_event_with_top_level_trace_id(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``contexts.trace`` is absent, a top-level ``trace_id`` must still be surfaced."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    event = {
        "eventID": "ev-top-trace",
        "title": "boom",
        "level": "error",
        "dateCreated": "2026-06-15T12:00:01Z",
        "trace_id": "top-trace-xyz",
    }
    transport = RecordingTransport(default=_json_response(200, event))
    src = SentrySource({**base_config, "transport": transport})
    rec = src.fetch_event("1001")
    assert rec is not None
    assert rec["labels"]["trace_id"] == "top-trace-xyz"


def test_normalize_event_with_non_dict_response_returns_none(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 2xx response whose JSON body is not a dict must yield None."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    transport = RecordingTransport(default=_json_response(200, ["not", "a", "dict"]))
    src = SentrySource({**base_config, "transport": transport})
    assert src.fetch_event("1001") is None


def test_normalize_event_surfaces_runtime_and_os_contexts(
    base_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime/OS context names must be surfaced as labels when present."""
    monkeypatch.setenv("SENTRY_TEST_TOKEN", "tok")
    event = {
        "eventID": "ev-ctx-full",
        "title": "boom",
        "level": "error",
        "dateCreated": "2026-06-15T12:00:01Z",
        "contexts": {
            "runtime": {"name": "CPython"},
            "os": {"name": "Linux"},
            "trace": {"trace_id": "t", "span_id": "s"},
        },
    }
    transport = RecordingTransport(default=_json_response(200, event))
    src = SentrySource({**base_config, "transport": transport})
    rec = src.fetch_event("1001")
    assert rec is not None
    assert rec["labels"]["runtime"] == "CPython"
    assert rec["labels"]["os"] == "Linux"


# --------------------------------------------------------------------------- #
# _SentryResponse — body decoding edge cases
# --------------------------------------------------------------------------- #
def test_sentry_response_text_decodes_bytes() -> None:
    """A bytes body must be decoded as utf-8 (errors='replace') by ``text``."""
    # b"\xff" is not valid utf-8 -> decoded to the replacement character U+FFFD.
    resp = _SentryResponse(status=200, body=b"hello\xff")
    assert resp.text == "hello\ufffd"
    # A clean bytes body round-trips intact.
    assert _SentryResponse(status=200, body=b"world").text == "world"
    # A str body is returned as-is.
    assert _SentryResponse(status=200, body="already-str").text == "already-str"


def test_sentry_response_json_empty_body_returns_none() -> None:
    """An empty body must yield ``json() == None``, not a JSONDecodeError."""
    resp = _SentryResponse(status=204, body=b"   ")
    assert resp.json() is None


# --------------------------------------------------------------------------- #
# _UrllibTransport — wiring (mocked urllib, no network)
# --------------------------------------------------------------------------- #
def test_urllib_transport_wiring_success() -> None:
    """``_UrllibTransport.get`` builds a Request and decodes the response."""
    transport = _UrllibTransport()
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.__enter__ = MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.read = MagicMock(return_value=b'{"ok": true}')

    with patch("general_ludd.connectors.sentry.urllib.request.urlopen", return_value=fake_resp) as m:
        resp = transport.get(
            "https://sentry.example.com/api/0/",
            headers={"Authorization": "Bearer x"},
            timeout=5.0,
        )
    assert resp.status == 200
    assert resp.json() == {"ok": True}
    # urlopen must receive the timeout verbatim.
    assert m.call_args.kwargs.get("timeout") == 5.0


def test_urllib_transport_wiring_http_error_returns_response() -> None:
    """An HTTPError (4xx/5xx) must be surfaced as a response, not raised."""
    import urllib.error

    transport = _UrllibTransport()
    err = urllib.error.HTTPError(
        url="https://sentry.example.com/api/0/",
        code=404,
        msg="Not Found",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"detail": "missing"}'),
    )

    with patch("general_ludd.connectors.sentry.urllib.request.urlopen", side_effect=err):
        resp = transport.get(
            "https://sentry.example.com/api/0/",
            headers={},
            timeout=5.0,
        )
    assert resp.status == 404
    assert resp.json() == {"detail": "missing"}


# --------------------------------------------------------------------------- #
# Transport protocol shape
# --------------------------------------------------------------------------- #
def test_recording_transport_satisfies_protocol() -> None:
    """The mocked RecordingTransport must satisfy the runtime Transport protocol."""
    rt: Any = RecordingTransport()
    assert isinstance(rt, Transport)
