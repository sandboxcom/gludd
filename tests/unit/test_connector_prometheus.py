"""Unit tests for the Prometheus observability connector.

Self-contained: imports only the connector module under test and uses an
injectable, fully-mocked HTTP transport (no real network, no DNS, no shell).
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.prometheus import PrometheusSource

# ---------------------------------------------------------------------------
# Mock transport helpers
# ---------------------------------------------------------------------------


class RecordingTransport:
    """Records calls and returns a queued (status, json) tuple per call.

    Matches the injectable transport contract:
        http_get(url, params, headers) -> (status:int, json:dict)
    """

    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
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
                    "values": [
                        [1718000000.0, "10"],
                        [1718000060.0, "12.5"],
                    ],
                }
            ],
        },
    }


def scalar_payload() -> dict[str, Any]:
    return {
        "status": "success",
        "data": {"resultType": "scalar", "result": [1718000000.0, "42"]},
    }


def error_payload() -> dict[str, Any]:
    return {
        "status": "error",
        "errorType": "bad_data",
        "error": "invalid parameter 'query'",
    }


GOOD_URL = "https://prom.internal.example.com:9090"


# ---------------------------------------------------------------------------
# Construction / contract
# ---------------------------------------------------------------------------


def test_kind_and_name_and_construction():
    src = PrometheusSource({"base_url": GOOD_URL}, http_get=RecordingTransport([]))
    assert PrometheusSource.KIND == "metrics"
    assert src.kind == "metrics"
    assert isinstance(src.name, str) and src.name


def test_token_env_injects_bearer_header(monkeypatch):
    monkeypatch.setenv("PROM_TOKEN", "s3cret")
    t = RecordingTransport([(200, vector_payload())])
    src = PrometheusSource(
        {"base_url": GOOD_URL, "token_env": "PROM_TOKEN"}, http_get=t
    )
    src.query({"promql": "up"})
    auth = t.calls[0]["headers"].get("Authorization", "")
    assert auth == "Bearer s3cret"


def test_missing_token_env_is_tolerated(monkeypatch):
    monkeypatch.delenv("PROM_TOKEN", raising=False)
    t = RecordingTransport([(200, vector_payload())])
    src = PrometheusSource(
        {"base_url": GOOD_URL, "token_env": "PROM_TOKEN"}, http_get=t
    )
    # no Authorization header, but still works
    src.query({"promql": "up"})
    assert "Authorization" not in t.calls[0]["headers"]


# ---------------------------------------------------------------------------
# SSRF literal-host blocking (no DNS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:9090",
        "https://localhost:9090",
        "http://[::1]:9090",
        "http://169.254.169.254/api/v1/query",  # cloud metadata
        "http://10.0.0.5:9090",
        "http://192.168.1.10:9090",
        "http://172.16.0.9:9090",
        "http://0.0.0.0:9090",
        "http://metadata.google.internal/",
    ],
)
def test_ssrf_rejects_internal_hosts(bad_url):
    with pytest.raises(ValueError):
        PrometheusSource({"base_url": bad_url}, http_get=RecordingTransport([]))


def test_ssrf_allows_http_for_internal_allowlisted_prom():
    # http is allowed (prometheus is often plain-http internal), as long as the
    # literal host is not loopback/private/metadata.
    src = PrometheusSource(
        {"base_url": "http://prom.example.com:9090"}, http_get=RecordingTransport([])
    )
    assert src.name


def test_ssrf_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        PrometheusSource(
            {"base_url": "file:///etc/passwd"}, http_get=RecordingTransport([])
        )


# ---------------------------------------------------------------------------
# query() normalization — vector
# ---------------------------------------------------------------------------


def test_query_vector_per_sample_normalization():
    t = RecordingTransport([(200, vector_payload())])
    src = PrometheusSource({"base_url": GOOD_URL}, http_get=t)
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
    assert r0["raw"]["metric"]["job"] == "api"

    assert records[1]["value"] == 0.0


def test_query_vector_passes_time_param():
    t = RecordingTransport([(200, vector_payload())])
    src = PrometheusSource({"base_url": GOOD_URL}, http_get=t)
    src.query({"promql": "up", "time": 1718000000.0})
    assert t.calls[0]["params"]["time"] == 1718000000.0


# ---------------------------------------------------------------------------
# query() normalization — matrix (range)
# ---------------------------------------------------------------------------


def test_query_matrix_uses_range_endpoint_and_expands_samples():
    t = RecordingTransport([(200, matrix_payload())])
    src = PrometheusSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query(
        {
            "promql": "rate_req",
            "start": 1718000000.0,
            "end": 1718000060.0,
            "step": "60s",
        }
    )
    assert t.calls[0]["url"] == f"{GOOD_URL}/api/v1/query_range"
    assert t.calls[0]["params"]["start"] == 1718000000.0
    assert t.calls[0]["params"]["end"] == 1718000060.0
    assert t.calls[0]["params"]["step"] == "60s"

    # one series, two samples -> two records
    assert len(records) == 2
    assert records[0]["value"] == 10.0
    assert records[0]["ts"] == 1718000000.0
    assert records[1]["value"] == 12.5
    assert records[1]["ts"] == 1718000060.0
    assert records[0]["labels"] == {"__name__": "rate_req", "job": "api"}
    assert records[0]["message"] == 'rate_req{job="api"}'


# ---------------------------------------------------------------------------
# query() normalization — scalar
# ---------------------------------------------------------------------------


def test_query_scalar_single_record():
    t = RecordingTransport([(200, scalar_payload())])
    src = PrometheusSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"promql": "1"})
    assert len(records) == 1
    assert records[0]["value"] == 42.0
    assert records[0]["ts"] == 1718000000.0
    assert records[0]["kind"] == "metrics"


# ---------------------------------------------------------------------------
# query() error handling
# ---------------------------------------------------------------------------


def test_query_error_payload_returns_error_record():
    t = RecordingTransport([(400, error_payload())])
    src = PrometheusSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"promql": "bad{"})
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "metrics"
    assert rec["level_or_status"] == "error"
    assert "invalid parameter" in rec["message"]
    assert rec["value"] == 0.0
    assert rec["raw"]["status"] == "error"


def test_query_missing_promql_returns_error_record():
    src = PrometheusSource({"base_url": GOOD_URL}, http_get=RecordingTransport([]))
    records = src.query({})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"


def test_query_transport_exception_yields_error_record_not_raise():
    def boom(url, params=None, headers=None):
        raise RuntimeError("connection refused")

    src = PrometheusSource({"base_url": GOOD_URL}, http_get=boom)
    records = src.query({"promql": "up"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "connection refused" in records[0]["message"]


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_ok_via_query():
    t = RecordingTransport([(200, {"status": "success", "data": {"resultType": "scalar", "result": [1.0, "1"]}})])
    src = PrometheusSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is True
    assert h["source"] == src.name


def test_health_not_ok_on_error_payload():
    t = RecordingTransport([(500, error_payload())])
    src = PrometheusSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is False
    assert "error" in h


def test_health_never_raises_on_transport_exception():
    def boom(url, params=None, headers=None):
        raise RuntimeError("dns/socket failure")

    src = PrometheusSource({"base_url": GOOD_URL}, http_get=boom)
    h = src.health()
    assert h["ok"] is False
    assert "dns/socket failure" in h["error"]
