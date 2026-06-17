"""Unit tests for JaegerSource tracing connector (mocked transport).

Self-contained: imports ONLY the module under test. No base/sibling imports.
Transport is a fake injected object — no real network, no DNS, no shell.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.jaeger import JaegerSource


# --------------------------------------------------------------------------- #
# Fake transport
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeTransport:
    """Records the URLs/params requested and replays canned responses."""

    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append((url, params))
        for key, resp in self._responses.items():
            if key in url:
                return resp
        return FakeResponse(404, {})


# --------------------------------------------------------------------------- #
# Canned Jaeger payloads
# --------------------------------------------------------------------------- #
TRACE_PAYLOAD = {
    "data": [
        {
            "traceID": "abc123",
            "spans": [
                {
                    "traceID": "abc123",
                    "spanID": "span-root",
                    "operationName": "GET /checkout",
                    "startTime": 1_700_000_000_000_000,  # microseconds
                    "duration": 4500,  # microseconds
                    "processID": "p1",
                    "tags": [{"key": "http.status_code", "value": 200}],
                },
                {
                    "traceID": "abc123",
                    "spanID": "span-child",
                    "operationName": "db.query",
                    "startTime": 1_700_000_000_500_000,
                    "duration": 1200,
                    "processID": "p2",
                    "tags": [{"key": "error", "value": True}],
                },
            ],
            "processes": {
                "p1": {"serviceName": "frontend"},
                "p2": {"serviceName": "postgres"},
            },
        }
    ]
}

SERVICES_PAYLOAD = {"data": ["frontend", "postgres"]}


def make_source(transport: FakeTransport, **cfg: Any) -> JaegerSource:
    base = {"base_url": "https://jaeger.example.com"}
    base.update(cfg)
    return JaegerSource(base, transport=transport)


# --------------------------------------------------------------------------- #
# Contract: identity
# --------------------------------------------------------------------------- #
def test_kind_is_traces() -> None:
    src = make_source(FakeTransport({}))
    assert src.KIND == "traces"


def test_name_attr_from_config() -> None:
    src = make_source(FakeTransport({}), name="prod-jaeger")
    assert src.name == "prod-jaeger"


def test_name_defaults_when_absent() -> None:
    src = make_source(FakeTransport({}))
    assert isinstance(src.name, str)
    assert src.name


# --------------------------------------------------------------------------- #
# query() normalization
# --------------------------------------------------------------------------- #
def test_query_normalizes_each_span() -> None:
    t = FakeTransport({"/api/traces": FakeResponse(200, TRACE_PAYLOAD)})
    src = make_source(t)
    records = src.query({"service": "frontend", "lookback": "1h", "limit": 20})

    assert isinstance(records, list)
    assert len(records) == 2  # one record per span across the trace

    for rec in records:
        assert rec["source"] == src.name
        assert rec["kind"] == "traces"
        # required normalized keys
        for field in (
            "ts",
            "source",
            "kind",
            "level_or_status",
            "message",
            "value",
            "labels",
            "raw",
        ):
            assert field in rec


def test_query_ts_converted_micros_to_seconds() -> None:
    t = FakeTransport({"/api/traces": FakeResponse(200, TRACE_PAYLOAD)})
    src = make_source(t)
    root = next(r for r in src.query({"service": "frontend"}) if r["labels"]["span_id"] == "span-root")
    # 1_700_000_000_000_000 us -> 1_700_000_000.0 s
    assert root["ts"] == pytest.approx(1_700_000_000.0)


def test_query_value_is_duration() -> None:
    t = FakeTransport({"/api/traces": FakeResponse(200, TRACE_PAYLOAD)})
    src = make_source(t)
    root = next(r for r in src.query({"service": "frontend"}) if r["labels"]["span_id"] == "span-root")
    assert root["value"] == 4500


def test_query_labels_include_correlation_fields() -> None:
    t = FakeTransport({"/api/traces": FakeResponse(200, TRACE_PAYLOAD)})
    src = make_source(t)
    rec = src.query({"service": "frontend"})[0]
    labels = rec["labels"]
    assert "trace_id" in labels
    assert "span_id" in labels
    assert "service" in labels
    assert labels["trace_id"] == "abc123"


def test_query_message_joins_service_and_operation() -> None:
    t = FakeTransport({"/api/traces": FakeResponse(200, TRACE_PAYLOAD)})
    src = make_source(t)
    root = next(r for r in src.query({"service": "frontend"}) if r["labels"]["span_id"] == "span-root")
    assert "frontend" in root["message"]
    assert "GET /checkout" in root["message"]
    assert root["labels"]["service"] == "frontend"


def test_query_error_tag_sets_error_status() -> None:
    t = FakeTransport({"/api/traces": FakeResponse(200, TRACE_PAYLOAD)})
    src = make_source(t)
    child = next(r for r in src.query({"service": "frontend"}) if r["labels"]["span_id"] == "span-child")
    root = next(r for r in src.query({"service": "frontend"}) if r["labels"]["span_id"] == "span-root")
    assert child["level_or_status"] == "error"
    assert root["level_or_status"] == "ok"


def test_query_by_trace_id_hits_single_trace_endpoint() -> None:
    single = {"data": [TRACE_PAYLOAD["data"][0]]}
    t = FakeTransport({"/api/traces/abc123": FakeResponse(200, single)})
    src = make_source(t)
    records = src.query({"trace_id": "abc123"})
    assert len(records) == 2
    assert any("/api/traces/abc123" in url for url, _ in t.calls)


def test_query_passes_service_and_limit_params() -> None:
    t = FakeTransport({"/api/traces": FakeResponse(200, TRACE_PAYLOAD)})
    src = make_source(t)
    src.query({"service": "frontend", "operation": "GET /checkout", "lookback": "2h", "limit": 5})
    _, params = t.calls[0]
    assert params is not None
    assert params.get("service") == "frontend"
    assert params.get("operation") == "GET /checkout"
    assert params.get("lookback") == "2h"
    assert params.get("limit") == 5


def test_query_empty_on_non_200() -> None:
    t = FakeTransport({"/api/traces": FakeResponse(500, {})})
    src = make_source(t)
    assert src.query({"service": "frontend"}) == []


def test_query_never_raises_on_bad_payload() -> None:
    t = FakeTransport({"/api/traces": FakeResponse(200, {"data": [{"bogus": 1}]})})
    src = make_source(t)
    assert src.query({"service": "frontend"}) == []


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #
def test_health_ok() -> None:
    t = FakeTransport({"/api/services": FakeResponse(200, SERVICES_PAYLOAD)})
    src = make_source(t)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_error_status() -> None:
    t = FakeTransport({"/api/services": FakeResponse(503, {})})
    src = make_source(t)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_failure() -> None:
    class BoomTransport:
        def get(self, *a: Any, **k: Any) -> Any:
            raise ConnectionError("network down")

    src = make_source(BoomTransport())  # type: ignore[arg-type]
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


# --------------------------------------------------------------------------- #
# token from env
# --------------------------------------------------------------------------- #
def test_token_read_from_env_into_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAEGER_API_TOKEN", "s3cr3t")

    captured: dict[str, Any] = {}

    class HeaderTransport:
        def get(
            self,
            url: str,
            params: dict[str, Any] | None = None,
            timeout: float | None = None,
            headers: dict[str, str] | None = None,
        ) -> FakeResponse:
            captured["headers"] = headers
            return FakeResponse(200, SERVICES_PAYLOAD)

    src = JaegerSource(
        {"base_url": "https://jaeger.example.com", "token_env": "JAEGER_API_TOKEN"},
        transport=HeaderTransport(),  # type: ignore[arg-type]
    )
    src.health()
    assert captured["headers"] is not None
    assert "s3cr3t" in str(captured["headers"].values())


def test_no_token_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAEGER_API_TOKEN", raising=False)
    src = JaegerSource(
        {"base_url": "https://jaeger.example.com", "token_env": "JAEGER_API_TOKEN"},
        transport=FakeTransport({"/api/services": FakeResponse(200, SERVICES_PAYLOAD)}),
    )
    assert src._auth_header() == {}


# --------------------------------------------------------------------------- #
# SSRF literal-host block (no DNS)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:16686",
        "http://localhost:16686",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://172.16.0.1",
        "http://[::1]:16686",
        "http://0.0.0.0",
        "ftp://example.com",  # bad scheme
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        JaegerSource({"base_url": bad_url}, transport=FakeTransport({}))


def test_public_base_url_allowed() -> None:
    # Should NOT raise.
    JaegerSource({"base_url": "https://jaeger.example.com"}, transport=FakeTransport({}))
