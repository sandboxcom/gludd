"""Unit tests for JaegerSource — mocked transport, no network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.jaeger import HttpResponse, JaegerSource, SsrfError


class FakeTransport:
    """Records requests and replays canned responses keyed by url substring."""

    def __init__(self, responses: list[tuple[str, int, Any]]) -> None:
        # (url_substring, status, json_body) matched in order
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> HttpResponse:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        for substr, status, body in self._responses:
            if substr in url:
                return HttpResponse(status, json.dumps(body).encode("utf-8"))
        return HttpResponse(404, b"{}")


class RaisingTransport:
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> HttpResponse:
        raise ConnectionError("boom")


CANNED_TRACES = {
    "data": [
        {
            "traceID": "abc123",
            "spans": [
                {
                    "traceID": "abc123",
                    "spanID": "span1",
                    "operationName": "GET /widgets",
                    "startTime": 1718000000000000,
                    "duration": 4200,
                    "processID": "p1",
                    "tags": [{"key": "error", "value": True}],
                },
                {
                    "traceID": "abc123",
                    "spanID": "span2",
                    "operationName": "db.query",
                    "startTime": 1718000000500000,
                    "duration": 900,
                    "processID": "p2",
                    "tags": [{"key": "http.status_code", "value": 200}],
                },
            ],
            "processes": {
                "p1": {"serviceName": "frontend"},
                "p2": {"serviceName": "postgres"},
            },
        }
    ]
}


def _source(transport: Any, **overrides: Any) -> JaegerSource:
    config: dict[str, Any] = {
        "name": "jaeger-prod",
        "base_url": "https://traces.example.com",
        "service": "frontend",
    }
    config.update(overrides)
    return JaegerSource(config, transport=transport)


class TestContract:
    def test_kind_and_name(self) -> None:
        src = _source(FakeTransport([]))
        assert JaegerSource.KIND == "traces"
        assert src.name == "jaeger-prod"


class TestNormalization:
    def test_one_record_per_span(self) -> None:
        transport = FakeTransport([("/api/traces", 200, CANNED_TRACES)])
        records = _source(transport).query({"service": "frontend"})
        assert len(records) == 2

    def test_error_span_status_and_duration(self) -> None:
        transport = FakeTransport([("/api/traces", 200, CANNED_TRACES)])
        rec = _source(transport).query()[0]
        assert rec["kind"] == "traces"
        assert rec["source"] == "jaeger-prod"
        assert rec["level_or_status"] == "error"
        assert rec["message"] == "GET /widgets"
        assert rec["value"] == 4200  # duration in microseconds
        assert rec["ts"] == 1718000000000000
        assert rec["labels"] == {
            "service": "frontend",
            "operation": "GET /widgets",
            "span_id": "span1",
            "trace_id": "abc123",
            "status": "error",
        }
        assert rec["raw"]["spanID"] == "span1"

    def test_ok_span(self) -> None:
        transport = FakeTransport([("/api/traces", 200, CANNED_TRACES)])
        rec = _source(transport).query()[1]
        assert rec["level_or_status"] == "ok"
        assert rec["labels"]["service"] == "postgres"
        assert rec["value"] == 900

    def test_query_params_sent(self) -> None:
        transport = FakeTransport([("/api/traces", 200, CANNED_TRACES)])
        _source(transport).query({"service": "cart", "lookback": "2h", "limit": 5})
        url = transport.calls[-1]["url"]
        assert "service=cart" in url
        assert "lookback=2h" in url
        assert "limit=5" in url


class TestSsrf:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:16686",
            "http://localhost:16686",
            "http://10.0.0.5",
            "http://169.254.169.254",  # cloud metadata
            "http://192.168.1.10",
        ],
    )
    def test_private_host_rejected(self, url: str) -> None:
        with pytest.raises(SsrfError):
            _source(FakeTransport([]), base_url=url)

    def test_private_allowed_with_optin(self) -> None:
        src = _source(FakeTransport([]), base_url="http://127.0.0.1:16686", allow_private=True)
        assert src.base_url == "http://127.0.0.1:16686"

    def test_bad_scheme_rejected(self) -> None:
        with pytest.raises(SsrfError):
            _source(FakeTransport([]), base_url="file:///etc/passwd")


class TestAuth:
    def test_bearer_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_TOKEN", "s3cr3t")
        transport = FakeTransport([("/api/traces", 200, CANNED_TRACES)])
        _source(transport, token_env="JAEGER_TOKEN").query()
        assert transport.calls[-1]["headers"]["Authorization"] == "Bearer s3cr3t"

    def test_no_auth_header_without_env(self) -> None:
        transport = FakeTransport([("/api/traces", 200, CANNED_TRACES)])
        _source(transport).query()
        assert "Authorization" not in transport.calls[-1]["headers"]


class TestHealth:
    def test_ok(self) -> None:
        transport = FakeTransport([("/api/services", 200, {"data": ["a", "b"]})])
        h = _source(transport).health()
        assert h["ok"] is True
        assert "2 services" in h["detail"]

    def test_not_ok_http_error(self) -> None:
        transport = FakeTransport([("/api/services", 503, {})])
        h = _source(transport).health()
        assert h["ok"] is False
        assert "503" in h["detail"]

    def test_never_raises(self) -> None:
        h = _source(RaisingTransport()).health()
        assert h["ok"] is False
        assert "ConnectionError" in h["detail"]
