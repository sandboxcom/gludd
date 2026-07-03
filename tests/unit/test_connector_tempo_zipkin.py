"""Unit tests for TempoZipkinSource tracing connector (mocked transport).

Self-contained: imports ONLY the module under test. No base/sibling imports.
Transport is a fake injected object — no real network, no DNS, no shell.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.tempo_zipkin import TempoZipkinSource


# --------------------------------------------------------------------------- #
# Fake transport
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code: int, payload: Any, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


class FakeTransport:
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
# Canned payloads
# --------------------------------------------------------------------------- #
# Tempo: OTLP-style JSON keyed by resourceSpans/scopeSpans, nanosecond timestamps.
TEMPO_PAYLOAD = {
    "batches": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "cartservice"}}
                ]
            },
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "traceId": "trace-deadbeef",
                            "spanId": "span-aaa",
                            "name": "PlaceOrder",
                            "startTimeUnixNano": "1700000000000000000",  # ns
                            "endTimeUnixNano": "1700000000003000000",  # +3ms
                            "status": {"code": 0},  # OK
                        },
                        {
                            "traceId": "trace-deadbeef",
                            "spanId": "span-bbb",
                            "name": "ChargeCard",
                            "startTimeUnixNano": "1700000000001000000",
                            "endTimeUnixNano": "1700000000002500000",  # +1.5ms
                            "status": {"code": 2},  # ERROR
                        },
                    ]
                }
            ],
        }
    ]
}

# Zipkin: /api/v2/traces returns a list of traces, each a list of spans.
# Zipkin timestamps + duration are in MICROSECONDS.
ZIPKIN_PAYLOAD = [
    [
        {
            "traceId": "zk-trace-1",
            "id": "zk-span-1",
            "name": "get /cart",
            "timestamp": 1_700_000_000_000_000,  # us
            "duration": 5000,  # us
            "localEndpoint": {"serviceName": "web"},
            "tags": {"http.status_code": "200"},
        },
        {
            "traceId": "zk-trace-1",
            "id": "zk-span-2",
            "name": "rpc.checkout",
            "timestamp": 1_700_000_000_002_000,
            "duration": 2000,
            "localEndpoint": {"serviceName": "checkout"},
            "tags": {"error": "boom"},
        },
    ]
]

ZIPKIN_SERVICES = ["web", "checkout"]


def make_tempo(transport: FakeTransport, **cfg: Any) -> TempoZipkinSource:
    base = {"base_url": "https://tempo.example.com"}
    base.update(cfg)
    return TempoZipkinSource(base, transport=transport)


def make_zipkin(transport: FakeTransport, **cfg: Any) -> TempoZipkinSource:
    base = {"base_url": "https://zipkin.example.com"}
    base.update(cfg)
    return TempoZipkinSource(base, transport=transport)


# --------------------------------------------------------------------------- #
# Identity contract
# --------------------------------------------------------------------------- #
def test_kind_is_traces() -> None:
    src = make_zipkin(FakeTransport({}))
    assert src.KIND == "traces"


def test_name_from_config() -> None:
    src = make_zipkin(FakeTransport({}), name="prod-zipkin")
    assert src.name == "prod-zipkin"


def test_name_defaults() -> None:
    src = make_zipkin(FakeTransport({}))
    assert isinstance(src.name, str)
    assert src.name


# --------------------------------------------------------------------------- #
# Zipkin flavor
# --------------------------------------------------------------------------- #
def test_zipkin_normalizes_spans() -> None:
    t = FakeTransport({"/api/v2/traces": FakeResponse(200, ZIPKIN_PAYLOAD)})
    src = make_zipkin(t)
    records = src.query({"flavor": "zipkin", "service": "web", "limit": 10})
    assert len(records) == 2
    for rec in records:
        assert rec["kind"] == "traces"
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


def test_zipkin_correlation_labels() -> None:
    t = FakeTransport({"/api/v2/traces": FakeResponse(200, ZIPKIN_PAYLOAD)})
    src = make_zipkin(t)
    rec = src.query({"flavor": "zipkin"})[0]
    assert rec["labels"]["trace_id"] == "zk-trace-1"
    assert rec["labels"]["span_id"] == "zk-span-1"
    assert rec["labels"]["service"] == "web"


def test_zipkin_value_is_duration() -> None:
    t = FakeTransport({"/api/v2/traces": FakeResponse(200, ZIPKIN_PAYLOAD)})
    src = make_zipkin(t)
    rec = next(r for r in src.query({"flavor": "zipkin"}) if r["labels"]["span_id"] == "zk-span-1")
    assert rec["value"] == 5000


def test_zipkin_ts_micros_to_seconds() -> None:
    t = FakeTransport({"/api/v2/traces": FakeResponse(200, ZIPKIN_PAYLOAD)})
    src = make_zipkin(t)
    rec = next(r for r in src.query({"flavor": "zipkin"}) if r["labels"]["span_id"] == "zk-span-1")
    assert rec["ts"] == pytest.approx(1_700_000_000.0)


def test_zipkin_error_detection() -> None:
    t = FakeTransport({"/api/v2/traces": FakeResponse(200, ZIPKIN_PAYLOAD)})
    src = make_zipkin(t)
    err = next(r for r in src.query({"flavor": "zipkin"}) if r["labels"]["span_id"] == "zk-span-2")
    ok = next(r for r in src.query({"flavor": "zipkin"}) if r["labels"]["span_id"] == "zk-span-1")
    assert err["level_or_status"] == "error"
    assert ok["level_or_status"] == "ok"


def test_zipkin_passes_params() -> None:
    t = FakeTransport({"/api/v2/traces": FakeResponse(200, ZIPKIN_PAYLOAD)})
    src = make_zipkin(t)
    src.query({"flavor": "zipkin", "service": "web", "limit": 7})
    url, params = t.calls[0]
    assert "/api/v2/traces" in url
    assert params is not None
    assert params.get("serviceName") == "web"
    assert params.get("limit") == 7


def test_zipkin_health_ok() -> None:
    t = FakeTransport({"/api/v2/services": FakeResponse(200, ZIPKIN_SERVICES)})
    src = make_zipkin(t, flavor="zipkin")
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_zipkin_health_not_ok() -> None:
    t = FakeTransport({"/api/v2/services": FakeResponse(500, {})})
    src = make_zipkin(t, flavor="zipkin")
    h = src.health()
    assert h["ok"] is False


# --------------------------------------------------------------------------- #
# Tempo flavor
# --------------------------------------------------------------------------- #
def test_tempo_normalizes_spans() -> None:
    t = FakeTransport({"/api/traces/trace-deadbeef": FakeResponse(200, TEMPO_PAYLOAD)})
    src = make_tempo(t)
    records = src.query({"flavor": "tempo", "trace_id": "trace-deadbeef"})
    assert len(records) == 2
    for rec in records:
        assert rec["kind"] == "traces"
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


def test_tempo_hits_trace_endpoint() -> None:
    t = FakeTransport({"/api/traces/trace-deadbeef": FakeResponse(200, TEMPO_PAYLOAD)})
    src = make_tempo(t)
    src.query({"flavor": "tempo", "trace_id": "trace-deadbeef"})
    assert any("/api/traces/trace-deadbeef" in url for url, _ in t.calls)


def test_tempo_correlation_labels() -> None:
    t = FakeTransport({"/api/traces/trace-deadbeef": FakeResponse(200, TEMPO_PAYLOAD)})
    src = make_tempo(t)
    rec = next(
        r
        for r in src.query({"flavor": "tempo", "trace_id": "trace-deadbeef"})
        if r["labels"]["span_id"] == "span-aaa"
    )
    assert rec["labels"]["trace_id"] == "trace-deadbeef"
    assert rec["labels"]["span_id"] == "span-aaa"
    assert rec["labels"]["service"] == "cartservice"


def test_tempo_ts_nanos_to_seconds() -> None:
    t = FakeTransport({"/api/traces/trace-deadbeef": FakeResponse(200, TEMPO_PAYLOAD)})
    src = make_tempo(t)
    rec = next(
        r
        for r in src.query({"flavor": "tempo", "trace_id": "trace-deadbeef"})
        if r["labels"]["span_id"] == "span-aaa"
    )
    # 1700000000000000000 ns -> 1700000000.0 s
    assert rec["ts"] == pytest.approx(1_700_000_000.0)


def test_tempo_value_is_duration_micros() -> None:
    t = FakeTransport({"/api/traces/trace-deadbeef": FakeResponse(200, TEMPO_PAYLOAD)})
    src = make_tempo(t)
    rec = next(
        r
        for r in src.query({"flavor": "tempo", "trace_id": "trace-deadbeef"})
        if r["labels"]["span_id"] == "span-aaa"
    )
    # 3ms = 3000us; end(...003000000ns) - start(...000000000ns) = 3_000_000 ns = 3000 us
    assert rec["value"] == 3000


def test_tempo_error_detection() -> None:
    t = FakeTransport({"/api/traces/trace-deadbeef": FakeResponse(200, TEMPO_PAYLOAD)})
    src = make_tempo(t)
    recs = src.query({"flavor": "tempo", "trace_id": "trace-deadbeef"})
    err = next(r for r in recs if r["labels"]["span_id"] == "span-bbb")
    ok = next(r for r in recs if r["labels"]["span_id"] == "span-aaa")
    assert err["level_or_status"] == "error"
    assert ok["level_or_status"] == "ok"


def test_tempo_health_ok() -> None:
    t = FakeTransport({"/ready": FakeResponse(200, {}, text="ready")})
    src = make_tempo(t, flavor="tempo")
    h = src.health()
    assert h["ok"] is True


def test_tempo_health_not_ok() -> None:
    t = FakeTransport({"/ready": FakeResponse(503, {})})
    src = make_tempo(t, flavor="tempo")
    h = src.health()
    assert h["ok"] is False


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #
def test_unknown_flavor_returns_empty() -> None:
    src = make_zipkin(FakeTransport({}))
    assert src.query({"flavor": "wat"}) == []


def test_query_empty_on_non_200() -> None:
    t = FakeTransport({"/api/v2/traces": FakeResponse(500, {})})
    src = make_zipkin(t)
    assert src.query({"flavor": "zipkin"}) == []


def test_query_never_raises_on_bad_payload() -> None:
    t = FakeTransport({"/api/v2/traces": FakeResponse(200, [[{"junk": 1}]])})
    src = make_zipkin(t)
    # malformed span: skipped, not raised
    assert src.query({"flavor": "zipkin"}) == []


def test_health_never_raises() -> None:
    class BoomTransport:
        def get(self, *a: Any, **k: Any) -> Any:
            raise ConnectionError("down")

    src = make_zipkin(BoomTransport(), flavor="zipkin")  # type: ignore[arg-type]
    h = src.health()
    assert h["ok"] is False


# --------------------------------------------------------------------------- #
# token + SSRF
# --------------------------------------------------------------------------- #
def test_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMPO_API_TOKEN", "tk-123")
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
            return FakeResponse(200, ZIPKIN_SERVICES)

    src = TempoZipkinSource(
        {
            "base_url": "https://zipkin.example.com",
            "flavor": "zipkin",
            "token_env": "TEMPO_API_TOKEN",
        },
        transport=HeaderTransport(),  # type: ignore[arg-type]
    )
    src.health()
    assert captured["headers"] is not None
    assert "tk-123" in str(captured["headers"].values())


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:3200",
        "http://localhost:9411",
        "http://169.254.169.254/",
        "http://10.1.2.3",
        "http://192.168.0.1",
        "http://172.31.255.255",
        "http://[::1]",
        "gopher://evil",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        TempoZipkinSource({"base_url": bad_url}, transport=FakeTransport({}))


def test_public_base_url_allowed() -> None:
    TempoZipkinSource({"base_url": "https://tempo.example.com"}, transport=FakeTransport({}))


# Canonical SSRF guard coverage — 100.100.100.200 is the Alibaba metadata IP
# the shared general_ludd.security.ssrf.is_url_blocked guarantees.
_CANONICAL_SSRF_URLS = [
    "http://localhost/",
    "http://metadata.google.internal/",
    "http://169.254.169.254/",
    "http://100.100.100.200/",
]


@pytest.mark.parametrize("bad_url", _CANONICAL_SSRF_URLS)
def test_canonical_ssrf_urls_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        TempoZipkinSource({"base_url": bad_url}, transport=FakeTransport({}))


def test_public_base_url_constructs_after_consolidation() -> None:
    src = TempoZipkinSource(
        {"base_url": "https://api.example.com"}, transport=FakeTransport({})
    )
    assert src.name
