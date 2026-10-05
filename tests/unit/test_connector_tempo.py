"""Unit tests for TempoSource — mocked transport, no network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.tempo import SSRFError, TempoSource, _TempoResponse


class FakeTransport:
    def __init__(self, responses: list[tuple[str, int, Any]]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _TempoResponse:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        for substr, status, body in self._responses:
            if substr in url:
                return _TempoResponse(status, json.dumps(body).encode("utf-8"))
        return _TempoResponse(404, b"{}")


class RaisingTransport:
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _TempoResponse:
        raise OSError("dns fail")


CANNED_SEARCH = {
    "traces": [
        {
            "traceID": "tr1",
            "rootServiceName": "checkout",
            "rootTraceName": "POST /order",
            "durationMs": 128,
            "startTimeUnixNano": "1718000000000000000",
        },
        {
            "traceID": "tr2",
            "rootServiceName": "search",
            "rootTraceName": "GET /query",
            "durationMs": 42,
            "startTimeUnixNano": "1718000001000000000",
        },
    ]
}

# Full-trace fetch (OTLP-ish batches) for fetch_spans mode.
CANNED_TRACE_TR1 = {
    "batches": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "checkout"}}
                ]
            },
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "spanId": "sp1",
                            "name": "validate-cart",
                            "startTimeUnixNano": "1718000000000000000",
                            "endTimeUnixNano": "1718000000050000000",
                            "status": {"code": "STATUS_CODE_ERROR"},
                        },
                        {
                            "spanId": "sp2",
                            "name": "charge-card",
                            "startTimeUnixNano": "1718000000050000000",
                            "endTimeUnixNano": "1718000000070000000",
                            "status": {"code": "STATUS_CODE_OK"},
                        },
                    ]
                }
            ],
        }
    ]
}


def _source(transport: Any, **overrides: Any) -> TempoSource:
    config: dict[str, Any] = {
        "name": "tempo-prod",
        "base_url": "https://tempo.example.com",
        "tags": "service.name=checkout",
        "start": 1718000000,
        "end": 1718003600,
    }
    config.update(overrides)
    return TempoSource(config, transport=transport)


class TestContract:
    def test_kind_and_name(self) -> None:
        assert TempoSource.KIND == "traces"
        assert _source(FakeTransport([])).name == "tempo-prod"


class TestSummaryNormalization:
    def test_callable_tuple_transport_compatibility(self) -> None:
        calls: list[tuple[str, str]] = []

        def transport(method: str, url: str, **_: object) -> tuple[int, object]:
            calls.append((method, url))
            return 200, CANNED_SEARCH

        records = _source(transport).query({"traceql": "{}"})

        assert len(records) == 2
        assert calls == [
            (
                "GET",
                "https://tempo.example.com/api/search?q=%7B%7D",
            )
        ]

    def test_one_record_per_trace(self) -> None:
        transport = FakeTransport([("/api/search", 200, CANNED_SEARCH)])
        recs = _source(transport).query()
        assert len(recs) == 2

    def test_summary_fields(self) -> None:
        transport = FakeTransport([("/api/search", 200, CANNED_SEARCH)])
        rec = _source(transport).query()[0]
        assert rec["kind"] == "traces"
        assert rec["source"] == "tempo-prod"
        assert rec["level_or_status"] == "ok"
        assert rec["message"] == "POST /order"
        assert rec["value"] == 128
        assert rec["labels"] == {
            "service": "checkout",
            "trace_id": "tr1",
            "span_id": "",
            "status": "ok",
        }

    def test_search_params_sent(self) -> None:
        transport = FakeTransport([("/api/search", 200, CANNED_SEARCH)])
        _source(transport).query()
        url = transport.calls[-1]["url"]
        assert "tags=" in url
        assert "start=1718000000" in url
        assert "end=1718003600" in url

    def test_traceql_param(self) -> None:
        transport = FakeTransport([("/api/search", 200, CANNED_SEARCH)])
        _source(transport).query({"traceql": '{ .service.name = "checkout" }'})
        assert "q=" in transport.calls[-1]["url"]


class TestSpanFetch:
    def test_fetch_spans_per_span_records(self) -> None:
        transport = FakeTransport(
            [
                ("/api/traces/tr1", 200, CANNED_TRACE_TR1),
                ("/api/traces/tr2", 200, {"batches": []}),
                ("/api/search", 200, CANNED_SEARCH),
            ]
        )
        recs = _source(transport).query({"fetch_spans": True})
        assert len(recs) == 2
        err, ok = recs
        assert err["level_or_status"] == "error"
        assert err["message"] == "validate-cart"
        assert err["labels"] == {
            "service": "checkout",
            "trace_id": "tr1",
            "span_id": "sp1",
            "status": "error",
        }
        # duration computed from start/end nanos -> milliseconds
        assert err["value"] == pytest.approx(50.0)
        assert ok["level_or_status"] == "ok"
        assert ok["message"] == "charge-card"

    def test_span_helpers_filter_malformed_batches_scopes_and_resources(self) -> None:
        assert TempoSource._iter_batches(None) == []
        assert TempoSource._iter_batches({"batches": [None, {}]}) == [{}]
        assert TempoSource._iter_scope_spans({"scopeSpans": "invalid"}) == []
        assert TempoSource._resource_service(None) == ""
        assert TempoSource._resource_service({"attributes": [None, {"key": "other"}]}) == ""

    def test_span_fetch_iterates_multiple_batches(self) -> None:
        trace = {
            "batches": [
                {"scopeSpans": [{"spans": []}]},
                {"scopeSpans": [{"spans": []}]},
            ]
        }
        transport = FakeTransport(
            [
                ("/api/traces/tr1", 200, trace),
                ("/api/search", 200, {"traces": [{"traceID": "tr1"}]}),
            ]
        )

        assert _source(transport).query({"fetch_spans": True}) == []


class TestSsrf:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:3200",
            "http://localhost:3200",
            "http://10.0.0.1",
            "http://169.254.169.254",
        ],
    )
    def test_private_host_rejected(self, url: str) -> None:
        with pytest.raises(SSRFError):
            _source(FakeTransport([]), base_url=url)

    def test_private_allowed_with_optin(self) -> None:
        src = _source(FakeTransport([]), base_url="http://127.0.0.1:3200", allow_private=True)
        assert src.base_url == "http://127.0.0.1:3200"


class TestAuth:
    def test_bearer_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEMPO_TOKEN", "tok")
        transport = FakeTransport([("/api/search", 200, CANNED_SEARCH)])
        _source(transport, token_env="TEMPO_TOKEN").query()
        assert transport.calls[-1]["headers"]["Authorization"] == "Bearer tok"

    def test_no_auth_without_env(self) -> None:
        transport = FakeTransport([("/api/search", 200, CANNED_SEARCH)])
        _source(transport).query()
        assert "Authorization" not in transport.calls[-1]["headers"]


class TestHealth:
    def test_ok(self) -> None:
        transport = FakeTransport([("/api/search", 200, {"traces": []})])
        assert _source(transport).health()["ok"] is True

    def test_not_ok_http_error(self) -> None:
        transport = FakeTransport([("/api/search", 502, {})])
        h = _source(transport).health()
        assert h["ok"] is False
        assert h["detail"] == "http 502"

    def test_never_raises(self) -> None:
        h = _source(RaisingTransport()).health()
        assert h["ok"] is False
        assert h["detail"] == "health check failed"
