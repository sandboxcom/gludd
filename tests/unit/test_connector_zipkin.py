"""Unit tests for ZipkinSource — mocked transport, no network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.zipkin import SSRFError, ZipkinSource, _ZipkinResponse


class FakeTransport:
    def __init__(self, responses: list[tuple[str, int, Any]]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _ZipkinResponse:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        for substr, status, body in self._responses:
            if substr in url:
                return _ZipkinResponse(status, json.dumps(body).encode("utf-8"))
        return _ZipkinResponse(404, b"[]")


class RaisingTransport:
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _ZipkinResponse:
        raise TimeoutError("slow")


# Zipkin v2: list of traces; each trace is a list of spans.
CANNED_TRACES = [
    [
        {
            "traceId": "t1",
            "id": "s1",
            "name": "get /api",
            "timestamp": 1718000000000000,
            "duration": 5300,
            "kind": "SERVER",
            "localEndpoint": {"serviceName": "gateway"},
            "tags": {"error": "500 internal"},
        },
        {
            "traceId": "t1",
            "id": "s2",
            "name": "rpc.call",
            "timestamp": 1718000000200000,
            "duration": 1200,
            "kind": "CLIENT",
            "localEndpoint": {"serviceName": "auth"},
            "tags": {"http.method": "POST"},
        },
    ]
]


def _source(transport: Any, **overrides: Any) -> ZipkinSource:
    config: dict[str, Any] = {
        "name": "zipkin-prod",
        "base_url": "https://zipkin.example.com",
        "service_name": "gateway",
    }
    config.update(overrides)
    return ZipkinSource(config, transport=transport)


class TestContract:
    def test_kind_and_name(self) -> None:
        assert ZipkinSource.KIND == "traces"
        assert _source(FakeTransport([])).name == "zipkin-prod"


class TestNormalization:
    def test_callable_tuple_transport_compatibility(self) -> None:
        calls: list[tuple[str, str]] = []

        def transport(method: str, url: str, **_: object) -> tuple[int, object]:
            calls.append((method, url))
            return 200, CANNED_TRACES

        records = _source(transport).query()

        assert len(records) == 2
        assert calls == [
            (
                "GET",
                "https://zipkin.example.com/api/v2/traces"
                "?serviceName=gateway&lookback=3600000&limit=20",
            )
        ]

    def test_one_record_per_span(self) -> None:
        transport = FakeTransport([("/api/v2/traces", 200, CANNED_TRACES)])
        assert len(_source(transport).query()) == 2

    def test_error_span(self) -> None:
        transport = FakeTransport([("/api/v2/traces", 200, CANNED_TRACES)])
        rec = _source(transport).query()[0]
        assert rec["kind"] == "traces"
        assert rec["source"] == "zipkin-prod"
        assert rec["level_or_status"] == "error"
        assert rec["message"] == "get /api"
        assert rec["value"] == 5300
        assert rec["ts"] == 1718000000000000
        assert rec["labels"] == {
            "service": "gateway",
            "trace_id": "t1",
            "span_id": "s1",
            "kind": "SERVER",
        }
        assert rec["raw"]["id"] == "s1"

    def test_ok_span(self) -> None:
        transport = FakeTransport([("/api/v2/traces", 200, CANNED_TRACES)])
        rec = _source(transport).query()[1]
        assert rec["level_or_status"] == "ok"
        assert rec["labels"]["service"] == "auth"
        assert rec["labels"]["kind"] == "CLIENT"

    def test_query_params_sent(self) -> None:
        transport = FakeTransport([("/api/v2/traces", 200, CANNED_TRACES)])
        _source(transport).query({"service_name": "cart", "limit": 7})
        url = transport.calls[-1]["url"]
        assert "serviceName=cart" in url
        assert "limit=7" in url


class TestSsrf:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:9411",
            "http://localhost:9411",
            "http://10.1.2.3",
            "http://169.254.169.254",
            "http://172.16.0.1",
        ],
    )
    def test_private_host_rejected(self, url: str) -> None:
        with pytest.raises(SSRFError):
            _source(FakeTransport([]), base_url=url)

    def test_private_allowed_with_optin(self) -> None:
        src = _source(FakeTransport([]), base_url="http://10.1.2.3:9411", allow_private=True)
        assert src.base_url == "http://10.1.2.3:9411"


class TestAuth:
    def test_bearer_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZIPKIN_TOKEN", "abc")
        transport = FakeTransport([("/api/v2/traces", 200, CANNED_TRACES)])
        _source(transport, token_env="ZIPKIN_TOKEN").query()
        assert transport.calls[-1]["headers"]["Authorization"] == "Bearer abc"

    def test_no_auth_without_env(self) -> None:
        transport = FakeTransport([("/api/v2/traces", 200, CANNED_TRACES)])
        _source(transport).query()
        assert "Authorization" not in transport.calls[-1]["headers"]


class TestHealth:
    def test_ok(self) -> None:
        transport = FakeTransport([("/api/v2/traces", 200, [])])
        assert _source(transport).health()["ok"] is True

    def test_not_ok_http_error(self) -> None:
        transport = FakeTransport([("/api/v2/traces", 500, [])])
        h = _source(transport).health()
        assert h["ok"] is False
        assert h["detail"] == "http 500"

    def test_never_raises(self) -> None:
        h = _source(RaisingTransport()).health()
        assert h["ok"] is False
        assert h["detail"] == "TimeoutError: slow"
