"""Unit tests for the Thanos observability connector.

Self-contained: imports only the connector module under test and uses an
injectable, fully-mocked HTTP transport (no real network, no DNS, no shell).
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.thanos import ThanosSource

# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------


class RecordingTransport:
    """Records calls and returns a queued (status, json) tuple per call.

    Contract: http_get(url, params, headers, timeout) -> (status:int, json).
    """

    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        self.calls.append(
            {
                "url": url,
                "params": params or {},
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        if not self._responses:
            raise AssertionError("transport called more times than responses queued")
        return self._responses.pop(0)


def vector_payload() -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "up", "job": "api", "instance": "h1:9090"},
                    "value": [1718000000.0, "1"],
                },
                {
                    "metric": {"__name__": "up", "job": "db", "instance": "h2:9090"},
                    "value": [1718000000.0, "0"],
                },
            ],
        },
    }


def matrix_payload() -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"__name__": "rate_req", "job": "api"},
                    "values": [[1718000000.0, "10"], [1718000060.0, "12.5"]],
                }
            ],
        },
    }


def error_payload() -> dict[str, Any]:
    return {"status": "error", "errorType": "bad_data", "error": "invalid query"}


GOOD_URL = "https://thanos.internal.example.com:10902"


# ---------------------------------------------------------------------------
# Construction / contract
# ---------------------------------------------------------------------------


def test_kind_and_name_and_construction():
    src = ThanosSource({"base_url": GOOD_URL}, http_get=RecordingTransport([]))
    assert ThanosSource.KIND == "metrics"
    assert src.kind == "metrics"
    assert isinstance(src.name, str) and src.name


def test_constructs_with_default_transport():
    # No transport injected: the connector falls back to a real stdlib transport
    # so the registry's single-arg ``factory(config)`` build succeeds (the source
    # is NOT silently dropped from ``list_sources()``). No network at construction.
    from general_ludd.connectors.thanos import _default_http_get

    src = ThanosSource({"base_url": GOOD_URL})
    assert src._http_get is _default_http_get
    assert src.name


def test_transport_and_query_alias_compatibility():
    transport = RecordingTransport([(200, vector_payload())])
    src = ThanosSource(
        {"base_url": GOOD_URL},
        transport=transport,
    )

    records = src.query({"query": "up"})

    assert len(records) == 2
    assert records[0]["message"] == 'up{instance="h1:9090", job="api"}'
    assert transport.calls[0]["params"]["query"] == "up"


def test_http_get_and_transport_are_mutually_exclusive():
    callback = RecordingTransport([])
    with pytest.raises(ValueError, match="http_get or transport"):
        ThanosSource(
            {"base_url": GOOD_URL},
            http_get=callback,
            transport=callback,
        )


# ---------------------------------------------------------------------------
# Auth from env
# ---------------------------------------------------------------------------


def test_token_env_injects_bearer_header(monkeypatch):
    monkeypatch.setenv("THANOS_TOKEN", "s3cret")
    t = RecordingTransport([(200, vector_payload())])
    src = ThanosSource({"base_url": GOOD_URL, "token_env": "THANOS_TOKEN"}, http_get=t)
    src.query({"promql": "up"})
    assert t.calls[0]["headers"].get("Authorization") == "Bearer s3cret"


def test_missing_token_env_is_tolerated(monkeypatch):
    monkeypatch.delenv("THANOS_TOKEN", raising=False)
    t = RecordingTransport([(200, vector_payload())])
    src = ThanosSource({"base_url": GOOD_URL, "token_env": "THANOS_TOKEN"}, http_get=t)
    src.query({"promql": "up"})
    assert "Authorization" not in t.calls[0]["headers"]


def test_timeout_is_passed_to_transport():
    t = RecordingTransport([(200, vector_payload())])
    src = ThanosSource({"base_url": GOOD_URL}, http_get=t, timeout=3.5)
    src.query({"promql": "up"})
    assert t.calls[0]["timeout"] == 3.5


# ---------------------------------------------------------------------------
# SSRF literal-host blocking (no DNS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:10902",
        "https://localhost:10902",
        "http://[::1]:10902",
        "http://169.254.169.254/api/v1/query",
        "http://10.0.0.5:10902",
        "http://192.168.1.10:10902",
        "http://172.16.0.9:10902",
        "http://0.0.0.0:10902",
        "http://metadata.google.internal/",
    ],
)
def test_ssrf_rejects_internal_hosts(bad_url):
    with pytest.raises(ValueError):
        ThanosSource({"base_url": bad_url}, http_get=RecordingTransport([]))


def test_ssrf_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        ThanosSource({"base_url": "file:///etc/passwd"}, http_get=RecordingTransport([]))


def test_ssrf_allows_external_host():
    src = ThanosSource(
        {"base_url": "http://thanos.example.com:10902"},
        http_get=RecordingTransport([]),
    )
    assert src.name


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost/",
        "http://metadata.google.internal/",
        "http://169.254.169.254/",
        "http://100.100.100.200/",  # Alibaba metadata IP — canonical guard catches it
    ],
)
def test_canonical_metadata_hosts_rejected(bad_url):
    # Regression for task #40 (SSRF-guard consolidation): the private/metadata
    # decision delegates to security.ssrf.is_url_blocked, which adds the
    # Alibaba metadata IP the bespoke literal guard used to miss.
    with pytest.raises(ValueError):
        ThanosSource({"base_url": bad_url}, http_get=RecordingTransport([]))


# ---------------------------------------------------------------------------
# query() normalization
# ---------------------------------------------------------------------------


def test_query_vector_per_sample_normalization():
    t = RecordingTransport([(200, vector_payload())])
    src = ThanosSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"promql": "up"})

    assert t.calls[0]["url"] == f"{GOOD_URL}/api/v1/query"
    assert t.calls[0]["params"]["query"] == "up"
    assert len(records) == 2

    r0 = records[0]
    assert r0["kind"] == "metrics"
    assert r0["source"] == src.name
    assert r0["value"] == 1.0
    assert isinstance(r0["value"], float)
    assert r0["ts"] == 1718000000.0
    assert r0["labels"] == {"__name__": "up", "job": "api", "instance": "h1:9090"}
    assert r0["message"] == 'up{instance="h1:9090", job="api"}'
    assert r0["level_or_status"] == ""
    assert records[1]["value"] == 0.0


def test_query_matrix_uses_range_endpoint_and_expands_samples():
    t = RecordingTransport([(200, matrix_payload())])
    src = ThanosSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query(
        {"promql": "rate_req", "start": 1718000000.0, "end": 1718000060.0, "step": "60s"}
    )
    assert t.calls[0]["url"] == f"{GOOD_URL}/api/v1/query_range"
    assert len(records) == 2
    assert records[0]["value"] == 10.0
    assert records[1]["value"] == 12.5


def test_query_scalar_normalization():
    payload = {
        "status": "success",
        "data": {"resultType": "scalar", "result": [1718000000.0, "7.5"]},
    }
    src = ThanosSource(
        {"base_url": GOOD_URL},
        http_get=RecordingTransport([(200, payload)]),
    )

    records = src.query({"promql": "scalar(7.5)"})

    assert len(records) == 1
    assert records[0]["ts"] == 1718000000.0
    assert records[0]["value"] == 7.5


def test_query_passes_thanos_dedup_param():
    t = RecordingTransport([(200, vector_payload())])
    src = ThanosSource({"base_url": GOOD_URL}, http_get=t)
    src.query({"promql": "up", "dedup": "true"})
    assert t.calls[0]["params"]["dedup"] == "true"


# ---------------------------------------------------------------------------
# query() error handling
# ---------------------------------------------------------------------------


def test_query_error_payload_returns_error_record():
    t = RecordingTransport([(400, error_payload())])
    src = ThanosSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"promql": "bad{"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "invalid query" in records[0]["message"]


def test_query_missing_promql_returns_error_record():
    src = ThanosSource({"base_url": GOOD_URL}, http_get=RecordingTransport([]))
    records = src.query({})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"


def test_query_transport_exception_yields_error_record_not_raise():
    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("connection refused")

    src = ThanosSource({"base_url": GOOD_URL}, http_get=boom)
    records = src.query({"promql": "up"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert records[0]["message"] == "transport error: RuntimeError"


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_ok_returns_ok_and_detail():
    t = RecordingTransport(
        [(200, {"status": "success", "data": {"resultType": "scalar", "result": [1.0, "1"]}})]
    )
    src = ThanosSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h
    assert isinstance(h["detail"], str)


def test_health_not_ok_on_error_payload():
    t = RecordingTransport([(500, error_payload())])
    src = ThanosSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_exception():
    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("dns/socket failure")

    src = ThanosSource({"base_url": GOOD_URL}, http_get=boom)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"
