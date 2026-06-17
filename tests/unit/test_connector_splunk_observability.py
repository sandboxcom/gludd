"""Unit tests for the Splunk Observability Cloud (SignalFx) connector.

Self-contained: imports only the connector module under test and uses an
injectable, fully-mocked HTTP transport (no real network, no DNS, no shell).
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.splunk_observability import SplunkObservabilitySource

# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------


class RecordingTransport:
    """Contract:

    http_request(method, url, params, headers, json, timeout) -> (status, json)
    """

    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params or {},
                "headers": headers or {},
                "json": json,
                "timeout": timeout,
            }
        )
        if not self._responses:
            raise AssertionError("transport called more times than responses queued")
        return self._responses.pop(0)


def signalflow_messages() -> list[dict[str, Any]]:
    return [
        {
            "type": "metadata",
            "tsId": "AAA",
            "properties": {
                "sf_metric": "cpu.utilization",
                "host": "web01",
                "region": "us-east-1",
            },
        },
        {
            "type": "metadata",
            "tsId": "BBB",
            "properties": {"sf_metric": "cpu.utilization", "host": "web02"},
        },
        {
            "type": "data",
            "logicalTimestampMs": 1718000000000,
            "data": [
                {"tsId": "AAA", "value": 42.5},
                {"tsId": "BBB", "value": 17.0},
            ],
        },
    ]


def timeserieswindow_payload() -> dict[str, Any]:
    return {
        "data": {
            "AAA": [[1718000000000, 42.5], [1718000010000, 43.0]],
        },
        "metadata": {
            "AAA": {"sf_metric": "memory.used", "host": "db01"},
        },
    }


GOOD_URL = "https://api.us1.signalfx.com"


# ---------------------------------------------------------------------------
# Construction / contract
# ---------------------------------------------------------------------------


def test_kind_and_name_and_construction():
    src = SplunkObservabilitySource(
        {"base_url": GOOD_URL}, http_request=RecordingTransport([])
    )
    assert SplunkObservabilitySource.KIND == "metrics"
    assert src.kind == "metrics"
    assert isinstance(src.name, str) and src.name


def test_requires_injected_transport():
    with pytest.raises(ValueError):
        SplunkObservabilitySource({"base_url": GOOD_URL})


# ---------------------------------------------------------------------------
# Auth from env (X-SF-Token)
# ---------------------------------------------------------------------------


def test_token_env_injects_sf_token_header(monkeypatch):
    monkeypatch.setenv("SFX_TOKEN", "tok-123")
    t = RecordingTransport([(200, signalflow_messages())])
    src = SplunkObservabilitySource(
        {"base_url": GOOD_URL, "token_env": "SFX_TOKEN"}, http_request=t
    )
    src.query({"program": "data('cpu.utilization').publish()"})
    assert t.calls[0]["headers"].get("X-SF-Token") == "tok-123"


def test_missing_token_env_is_tolerated(monkeypatch):
    monkeypatch.delenv("SFX_TOKEN", raising=False)
    t = RecordingTransport([(200, signalflow_messages())])
    src = SplunkObservabilitySource(
        {"base_url": GOOD_URL, "token_env": "SFX_TOKEN"}, http_request=t
    )
    src.query({"program": "data('x').publish()"})
    assert "X-SF-Token" not in t.calls[0]["headers"]


def test_timeout_is_passed_to_transport():
    t = RecordingTransport([(200, signalflow_messages())])
    src = SplunkObservabilitySource(
        {"base_url": GOOD_URL}, http_request=t, timeout=4.25
    )
    src.query({"program": "data('x').publish()"})
    assert t.calls[0]["timeout"] == 4.25


# ---------------------------------------------------------------------------
# SSRF literal-host blocking (no DNS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "https://localhost",
        "http://[::1]",
        "http://169.254.169.254/",
        "http://10.0.0.1",
        "http://192.168.50.1",
        "http://172.20.0.1",
        "http://0.0.0.0",
        "http://metadata.google.internal/",
    ],
)
def test_ssrf_rejects_internal_hosts(bad_url):
    with pytest.raises(ValueError):
        SplunkObservabilitySource(
            {"base_url": bad_url}, http_request=RecordingTransport([])
        )


def test_ssrf_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        SplunkObservabilitySource(
            {"base_url": "gopher://api.signalfx.com"},
            http_request=RecordingTransport([]),
        )


# ---------------------------------------------------------------------------
# query() — SignalFlow (POST /v2/signalflow)
# ---------------------------------------------------------------------------


def test_query_signalflow_normalizes_datapoints():
    t = RecordingTransport([(200, signalflow_messages())])
    src = SplunkObservabilitySource({"base_url": GOOD_URL}, http_request=t)
    records = src.query({"program": "data('cpu.utilization').publish()"})

    assert t.calls[0]["method"] == "POST"
    assert t.calls[0]["url"] == f"{GOOD_URL}/v2/signalflow"
    assert t.calls[0]["json"]["program"] == "data('cpu.utilization').publish()"
    assert len(records) == 2

    by_host = {r["labels"].get("host"): r for r in records}
    web01 = by_host["web01"]
    assert web01["kind"] == "metrics"
    assert web01["source"] == src.name
    assert web01["message"] == "cpu.utilization"
    assert web01["value"] == 42.5
    assert isinstance(web01["value"], float)
    # ms -> seconds
    assert web01["ts"] == 1718000000.0
    assert web01["labels"]["region"] == "us-east-1"
    assert web01["level_or_status"] == ""

    assert by_host["web02"]["value"] == 17.0


def test_query_signalflow_passes_start_stop():
    t = RecordingTransport([(200, signalflow_messages())])
    src = SplunkObservabilitySource({"base_url": GOOD_URL}, http_request=t)
    src.query({"program": "data('x').publish()", "start": 1, "stop": 2})
    assert t.calls[0]["json"]["start"] == 1
    assert t.calls[0]["json"]["stop"] == 2


# ---------------------------------------------------------------------------
# query() — timeserieswindow (GET /v1/timeserieswindow)
# ---------------------------------------------------------------------------


def test_query_timeserieswindow_normalizes_datapoints():
    t = RecordingTransport([(200, timeserieswindow_payload())])
    src = SplunkObservabilitySource({"base_url": GOOD_URL}, http_request=t)
    records = src.query(
        {"metric": "memory.used", "startMs": 1718000000000, "endMs": 1718000020000}
    )

    assert t.calls[0]["method"] == "GET"
    assert t.calls[0]["url"] == f"{GOOD_URL}/v1/timeserieswindow"
    assert t.calls[0]["params"]["query"] == "memory.used"
    assert len(records) == 2

    assert records[0]["message"] == "memory.used"
    assert records[0]["value"] == 42.5
    assert records[0]["ts"] == 1718000000.0
    assert records[0]["labels"]["host"] == "db01"
    assert records[1]["value"] == 43.0


# ---------------------------------------------------------------------------
# query() error handling
# ---------------------------------------------------------------------------


def test_query_missing_spec_returns_error_record():
    src = SplunkObservabilitySource(
        {"base_url": GOOD_URL}, http_request=RecordingTransport([])
    )
    records = src.query({})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"


def test_query_signalflow_error_status_returns_error_record():
    t = RecordingTransport([(401, {"message": "invalid token"})])
    src = SplunkObservabilitySource({"base_url": GOOD_URL}, http_request=t)
    records = src.query({"program": "data('x').publish()"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "invalid token" in records[0]["message"]


def test_query_timeserieswindow_error_status_returns_error_record():
    t = RecordingTransport([(500, {"message": "boom"})])
    src = SplunkObservabilitySource({"base_url": GOOD_URL}, http_request=t)
    records = src.query({"metric": "memory.used"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"


def test_query_transport_exception_yields_error_record_not_raise():
    def boom(method, url, params=None, headers=None, json=None, timeout=None):
        raise RuntimeError("connection refused")

    src = SplunkObservabilitySource({"base_url": GOOD_URL}, http_request=boom)
    records = src.query({"program": "data('x').publish()"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "connection refused" in records[0]["message"]


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_ok_returns_ok_and_detail():
    t = RecordingTransport([(200, {"count": 1, "results": []})])
    src = SplunkObservabilitySource({"base_url": GOOD_URL}, http_request=t)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h
    assert t.calls[0]["url"] == f"{GOOD_URL}/v2/metric"


def test_health_not_ok_on_error_status():
    t = RecordingTransport([(403, {"message": "forbidden"})])
    src = SplunkObservabilitySource({"base_url": GOOD_URL}, http_request=t)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_exception():
    def boom(method, url, params=None, headers=None, json=None, timeout=None):
        raise RuntimeError("dns/socket failure")

    src = SplunkObservabilitySource({"base_url": GOOD_URL}, http_request=boom)
    h = src.health()
    assert h["ok"] is False
    assert "dns/socket failure" in h["detail"]
