"""Unit tests for the Elastic APM connector (mocked transport)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.elastic_apm import (
    KIND,
    ElasticApmSource,
    SSRFError,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


class RecordingTransport:
    """Captures the last call and returns a queued response."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def _canned_search() -> dict[str, Any]:
    return {
        "hits": {
            "hits": [
                {
                    "_id": "abc",
                    "_source": {
                        "@timestamp": "2026-06-12T00:00:00.000Z",
                        "trace": {"id": "trace-1"},
                        "span": {"id": "span-1", "type": "db", "duration": {"us": 1500}},
                        "service": {"name": "checkout"},
                        "event": {"outcome": "success"},
                    },
                },
                {
                    "_id": "def",
                    "_source": {
                        "@timestamp": "2026-06-12T00:00:01.000Z",
                        "trace": {"id": "trace-2"},
                        "transaction": {
                            "id": "txn-2",
                            "name": "GET /api",
                            "duration": {"us": 9000},
                            "result": "failure",
                        },
                        "service": {"name": "gateway"},
                        "error": {"message": "boom"},
                        "event": {"outcome": "failure"},
                    },
                },
            ]
        }
    }


def test_kind_and_name() -> None:
    src = ElasticApmSource({"name": "apm-prod", "base_url": "https://es.example.com"})
    assert src.KIND == "traces"
    assert KIND == "traces"
    assert src.name == "apm-prod"


def test_query_normalizes_spans_and_transactions() -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_search()))
    src = ElasticApmSource(
        {"name": "apm", "base_url": "https://es.example.com", "index": "apm-*"},
        transport=transport,
    )
    records = src.query({"body": {"query": {"match_all": {}}}})

    assert len(records) == 2
    expected_keys = {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    for rec in records:
        assert set(rec) == expected_keys
        assert rec["kind"] == "traces"
        assert rec["source"] == "apm"

    span_rec, txn_rec = records
    assert span_rec["value"] == 1500.0
    assert span_rec["level_or_status"] == "success"  # event.outcome surfaced
    assert span_rec["labels"]["trace_id"] == "trace-1"
    assert span_rec["labels"]["span_id"] == "span-1"
    assert span_rec["labels"]["service"] == "checkout"

    assert txn_rec["value"] == 9000.0
    assert txn_rec["level_or_status"] == "error"
    assert txn_rec["message"] == "boom"
    assert txn_rec["labels"]["service"] == "gateway"

    # POSTs to the search endpoint.
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://es.example.com/apm-*/_search"


def test_tuple_transport_compatibility() -> None:
    calls: list[dict[str, Any]] = []

    def transport(
        method: str,
        url: str,
        **kwargs: Any,
    ) -> tuple[int, object]:
        calls.append({"method": method, "url": url, **kwargs})
        return 200, _canned_search()

    src = ElasticApmSource(
        {"base_url": "https://es.example.com"},
        transport=transport,
    )

    records = src.query({})

    assert len(records) == 2
    assert calls[0]["method"] == "POST"


def test_auth_api_key_from_env() -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_search()))
    src = ElasticApmSource(
        {"base_url": "https://es.example.com", "api_key_env": "APM_KEY"},
        transport=transport,
        environ={"APM_KEY": "secret-key"},
    )
    src.query({})
    assert transport.calls[0]["headers"]["Authorization"] == "ApiKey secret-key"


def test_auth_bearer_from_env() -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_search()))
    src = ElasticApmSource(
        {"base_url": "https://es.example.com", "bearer_token_env": "APM_TOKEN"},
        transport=transport,
        environ={"APM_TOKEN": "tok"},
    )
    src.query({})
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_no_auth_when_env_missing() -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_search()))
    src = ElasticApmSource(
        {"base_url": "https://es.example.com", "bearer_token_env": "MISSING"},
        transport=transport,
        environ={},
    )
    src.query({})
    assert "Authorization" not in transport.calls[0]["headers"]


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:9200",
        "https://10.0.0.5",
        "http://169.254.169.254",  # cloud metadata
        "https://192.168.1.1:9200",
        "http://[::1]:9200",
        "https://0.0.0.0",
        # Loopback / metadata by *name* (not IP literal) must also be rejected.
        "http://localhost:9200",
        "https://ip6-localhost",
        "http://metadata.google.internal",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(SSRFError):
        ElasticApmSource({"base_url": bad_url})


def test_public_host_allowed() -> None:
    # A hostname (no DNS) and a public IP literal both pass.
    ElasticApmSource({"base_url": "https://es.example.com"})
    ElasticApmSource({"base_url": "https://8.8.8.8"})


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
    with pytest.raises(SSRFError):
        ElasticApmSource({"base_url": bad_url})


def test_public_base_url_constructs_after_consolidation() -> None:
    src = ElasticApmSource({"base_url": "https://api.example.com"})
    assert src.name == "elastic_apm"


def test_health_ok() -> None:
    transport = RecordingTransport(FakeResponse(200, {}))
    src = ElasticApmSource({"base_url": "https://es.example.com"}, transport=transport)
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok_on_bad_status() -> None:
    transport = RecordingTransport(FakeResponse(503, {}))
    src = ElasticApmSource({"base_url": "https://es.example.com"}, transport=transport)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "HTTP 503"


def test_health_never_raises_on_transport_error() -> None:
    def boom(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise ConnectionError("network down")

    src = ElasticApmSource({"base_url": "https://es.example.com"}, transport=boom)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "health check failed"
