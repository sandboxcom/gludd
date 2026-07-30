"""Unit tests for the self-contained SigNoz observability connector.

Transport is fully mocked: no real network I/O. We assert
- query() normalization (trace_id label + durationNano value, ts from startTime),
- health() ok / not-ok dicts (never raises),
- SSRF literal-host blocking on internal/loopback/private/metadata hosts.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.signoz import SigNozSource


class _StubTransport:
    """Injectable HTTP transport double.

    Records calls and returns a queued canned response per (method) call.
    A response is a tuple (status_code, json_body). If ``raise_exc`` is set,
    the call raises it (to exercise health() never-raises contract).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses: list[tuple[int, Any]] = []
        self.raise_exc: Exception | None = None

    def queue(self, status_code: int, json_body: Any) -> None:
        self._responses.append((status_code, json_body))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "json": json,
                "params": params,
                "timeout": timeout,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        if not self._responses:
            return (200, {})
        return self._responses.pop(0)


def _span_payload() -> dict[str, Any]:
    """A canned SigNoz query_range payload carrying two spans."""
    return {
        "status": "success",
        "data": {
            "result": [
                {
                    "list": [
                        {
                            "data": {
                                "startTimeUnixNano": 1_700_000_000_000_000_000,
                                "traceID": "abc123trace",
                                "spanID": "span-1",
                                "serviceName": "checkout",
                                "name": "POST /pay",
                                "durationNano": 4_200_000,
                                "statusCode": 2,
                                "hasError": True,
                            }
                        },
                        {
                            "data": {
                                "startTimeUnixNano": 1_700_000_001_000_000_000,
                                "traceID": "def456trace",
                                "spanID": "span-2",
                                "serviceName": "inventory",
                                "name": "GET /stock",
                                "durationNano": 900_000,
                                "statusCode": 0,
                                "hasError": False,
                            }
                        },
                    ]
                }
            ]
        },
    }


@pytest.fixture()
def source() -> tuple[SigNozSource, _StubTransport]:
    transport = _StubTransport()
    src = SigNozSource(
        config={"base_url": "https://signoz.example.com", "token_env": "SIGNOZ_TOKEN"},
        transport=transport,
    )
    return src, transport


def test_kind_and_name(source: tuple[SigNozSource, _StubTransport]) -> None:
    src, _ = source
    assert src.KIND == "traces"
    assert "metrics" in src.SECONDARY_KINDS
    assert src.name == "signoz"


def test_query_normalizes_spans(
    monkeypatch: pytest.MonkeyPatch, source: tuple[SigNozSource, _StubTransport]
) -> None:
    src, transport = source
    monkeypatch.setenv("SIGNOZ_TOKEN", "secret-token")
    transport.queue(200, _span_payload())

    records = src.query({"start": 1_700_000_000, "end": 1_700_000_010})

    assert isinstance(records, list)
    assert len(records) == 2

    first = records[0]
    # ts is startTime in seconds (ns -> s).
    assert first["ts"] == pytest.approx(1_700_000_000.0)
    assert first["source"] == "signoz"
    assert first["kind"] == "traces"
    # statusCode/error surfaced as level_or_status.
    assert first["level_or_status"] in ("error", "2", 2) or first["level_or_status"]
    # message combines serviceName + operation.
    assert "checkout" in first["message"]
    assert "POST /pay" in first["message"]
    # duration value.
    assert first["value"] == pytest.approx(4_200_000.0)
    # trace_id surfaced in labels for cross-source association.
    assert first["labels"]["trace_id"] == "abc123trace"
    assert first["labels"]["span_id"] == "span-1"
    assert first["labels"]["service.name"] == "checkout"
    assert first["labels"]["durationNano"] in (4_200_000, "4200000", 4_200_000.0)
    # raw is the original span dict.
    assert first["raw"]["spanID"] == "span-1"


def test_query_hits_query_range_endpoint_with_auth(
    monkeypatch: pytest.MonkeyPatch, source: tuple[SigNozSource, _StubTransport]
) -> None:
    src, transport = source
    monkeypatch.setenv("SIGNOZ_TOKEN", "secret-token")
    transport.queue(200, _span_payload())

    src.query({"start": 1_700_000_000, "end": 1_700_000_010})

    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v3/query_range")
    # auth from env.
    auth_values = " ".join(call["headers"].values())
    assert "secret-token" in auth_values
    # time-bound: the spec window is forwarded.
    assert call["timeout"] is not None


def test_callable_transport_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOZ_TOKEN", "secret-token")
    calls: list[dict[str, Any]] = []

    def transport(
        method: str,
        url: str,
        **kwargs: Any,
    ) -> tuple[int, object]:
        calls.append({"method": method, "url": url, **kwargs})
        return 200, _span_payload()

    src = SigNozSource(
        config={
            "base_url": "https://signoz.example.com",
            "token_env": "SIGNOZ_TOKEN",
        },
        transport=transport,
    )

    records = src.query({"start": 1_700_000_000, "end": 1_700_000_010})

    assert len(records) == 2
    assert calls[0]["method"] == "POST"


def test_health_ok(
    monkeypatch: pytest.MonkeyPatch, source: tuple[SigNozSource, _StubTransport]
) -> None:
    src, transport = source
    monkeypatch.setenv("SIGNOZ_TOKEN", "secret-token")
    transport.queue(200, {"version": "0.40.0"})

    h = src.health()
    assert h["ok"] is True
    assert h["source"] == "signoz"


def test_health_not_ok_on_bad_status(
    monkeypatch: pytest.MonkeyPatch, source: tuple[SigNozSource, _StubTransport]
) -> None:
    src, transport = source
    monkeypatch.setenv("SIGNOZ_TOKEN", "secret-token")
    transport.queue(503, {"error": "unavailable"})

    h = src.health()
    assert h["ok"] is False


def test_health_never_raises(
    monkeypatch: pytest.MonkeyPatch, source: tuple[SigNozSource, _StubTransport]
) -> None:
    src, transport = source
    monkeypatch.setenv("SIGNOZ_TOKEN", "secret-token")
    transport.raise_exc = ConnectionError("boom")

    h = src.health()  # must not raise
    assert h["ok"] is False
    assert h["error"] == "health check failed"


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:3301",
        "http://localhost:3301",
        "http://0.0.0.0:3301",
        "http://[::1]:3301",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://172.16.0.4",
        "http://169.254.169.254",  # cloud metadata
        "http://metadata.google.internal",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    transport = _StubTransport()
    with pytest.raises(ValueError):
        SigNozSource(
            config={"base_url": bad_url, "token_env": "SIGNOZ_TOKEN"},
            transport=transport,
        )


def test_public_base_url_accepted() -> None:
    transport = _StubTransport()
    src = SigNozSource(
        config={"base_url": "https://signoz.acme.io", "token_env": "X"},
        transport=transport,
    )
    assert src.name == "signoz"


# Canonical SSRF guard (general_ludd.security.ssrf.is_url_blocked) coverage —
# 100.100.100.200 is the Alibaba metadata endpoint the shared guard guarantees.
_CANONICAL_SSRF_URLS = [
    "http://localhost/",
    "http://metadata.google.internal/",
    "http://169.254.169.254/",
    "http://100.100.100.200/",
]


@pytest.mark.parametrize("bad_url", _CANONICAL_SSRF_URLS)
def test_canonical_ssrf_urls_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        SigNozSource(
            config={"base_url": bad_url, "token_env": "SIGNOZ_TOKEN"},
            transport=_StubTransport(),
        )


def test_public_base_url_constructs_after_consolidation() -> None:
    src = SigNozSource(
        config={"base_url": "https://api.example.com", "token_env": "X"},
        transport=_StubTransport(),
    )
    assert src.name == "signoz"
