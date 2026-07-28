"""Unit tests for the GCP observability connector (mocked transport).

These tests exercise ``GcpObservabilitySource`` with a fully injected, in-memory
HTTP transport so no real network call is ever made. They assert:

* contract surface (``KIND`` class attribute, ``name`` instance attribute);
* config-driven construction with the Bearer token sourced from the environment;
* SSRF literal-host rejection (internal endpoints refused without DNS);
* ``health()`` returns ``{'ok', 'detail'}`` and never raises;
* ``query(spec)`` normalization for both ``'logs'`` and ``'metrics'`` modes,
  including severity/value/timestamp mapping and the ``trace`` association label;
* the ``Authorization: Bearer <token>`` header is sent on every request.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.gcp_observability import (
    GcpObservabilitySource,
    _point_value,
)


class FakeResponse:
    """Minimal canned HTTP response."""

    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self.status_code = status
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class RecordingTransport:
    """Injectable transport that records calls and returns canned payloads.

    ``responses`` maps the canonical ``(method, url)`` to a :class:`FakeResponse`.
    Every invocation is appended to ``calls`` so tests can assert on the method,
    url, headers, body, and timeout that the connector actually used.
    """

    def __init__(self, responses: dict[tuple[str, str], FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json_body": json_body,
                "timeout": timeout,
            }
        )
        key = (method.upper(), url)
        if key not in self.responses:
            raise AssertionError(f"unexpected request: {key}")
        return self.responses[key]


PROJECT = "my-proj"
LOGGING_URL = "https://logging.googleapis.com/v2/entries:list"
MONITORING_URL = (
    f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries"
)

LOGS_PAYLOAD: dict[str, Any] = {
    "entries": [
        {
            "timestamp": "2026-06-16T10:00:00Z",
            "severity": "ERROR",
            "textPayload": "disk full on node-7",
            "logName": f"projects/{PROJECT}/logs/syslog",
            "trace": f"projects/{PROJECT}/traces/abc123",
            "resource": {"type": "gce_instance", "labels": {"zone": "us-1"}},
        },
        {
            "timestamp": "2026-06-16T10:01:00Z",
            "severity": "INFO",
            "jsonPayload": {"message": "structured hello"},
            "logName": f"projects/{PROJECT}/logs/app",
            "resource": {"type": "k8s_container"},
        },
    ]
}

METRICS_PAYLOAD: dict[str, Any] = {
    "timeSeries": [
        {
            "metric": {
                "type": "compute.googleapis.com/instance/cpu/utilization",
                "labels": {"instance_name": "node-7"},
            },
            "resource": {
                "type": "gce_instance",
                "labels": {"zone": "us-1", "project_id": PROJECT},
            },
            "points": [
                {
                    "interval": {
                        "startTime": "2026-06-16T10:00:00Z",
                        "endTime": "2026-06-16T10:01:00Z",
                    },
                    "value": {"doubleValue": 0.73},
                }
            ],
        }
    ]
}


def make_source(
    transport: RecordingTransport,
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str = "secret-tok",
    config: dict[str, Any] | None = None,
) -> GcpObservabilitySource:
    monkeypatch.setenv("GCP_TOKEN", token)
    cfg: dict[str, Any] = {
        "project": PROJECT,
        "token_env": "GCP_TOKEN",
    }
    if config:
        cfg.update(config)
    return GcpObservabilitySource(cfg, transport=transport)


# --------------------------------------------------------------------------- #
# Contract surface
# --------------------------------------------------------------------------- #
def test_kind_is_class_attribute() -> None:
    assert isinstance(GcpObservabilitySource.KIND, str)
    assert GcpObservabilitySource.KIND


def test_name_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport({})
    src = make_source(transport, monkeypatch)
    assert isinstance(src.name, str)
    assert src.name


def test_token_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(
        {(("GET"), MONITORING_URL): FakeResponse(200, {"timeSeries": []})}
    )
    src = make_source(transport, monkeypatch, token="env-tok-123")
    src.health()
    assert transport.calls, "health() must make a request"
    auth = transport.calls[0]["headers"]["Authorization"]
    assert auth == "Bearer env-tok-123"


# --------------------------------------------------------------------------- #
# SSRF literal-host block (no DNS)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_endpoint",
    [
        "http://169.254.169.254/v2/entries:list",  # cloud metadata
        "http://localhost/v2/entries:list",
        "http://127.0.0.1/v2/entries:list",
        "http://10.0.0.5/v2/entries:list",
        "http://192.168.1.1/v2/entries:list",
        "http://[::1]/v2/entries:list",
        "http://metadata.google.internal/v2/entries:list",
    ],
)
def test_ssrf_internal_endpoint_rejected(
    monkeypatch: pytest.MonkeyPatch, bad_endpoint: str
) -> None:
    transport = RecordingTransport({})
    with pytest.raises(ValueError):
        make_source(
            transport,
            monkeypatch,
            config={"logging_endpoint": bad_endpoint},
        )
    # No request was attempted (block is literal, pre-DNS, pre-flight).
    assert transport.calls == []


def test_public_googleapis_endpoint_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = RecordingTransport(
        {("POST", LOGGING_URL): FakeResponse(200, {"entries": []})}
    )
    # Construction with default public endpoints must not raise.
    src = make_source(transport, monkeypatch)
    assert src is not None


# Canonical SSRF guard coverage — 100.100.100.200 is the Alibaba metadata IP
# the shared general_ludd.security.ssrf.is_url_blocked guarantees.
@pytest.mark.parametrize(
    "bad_endpoint",
    [
        "http://localhost/",
        "http://metadata.google.internal/",
        "http://169.254.169.254/",
        "http://100.100.100.200/",
    ],
)
def test_canonical_ssrf_endpoints_rejected(
    monkeypatch: pytest.MonkeyPatch, bad_endpoint: str
) -> None:
    transport = RecordingTransport({})
    with pytest.raises(ValueError):
        make_source(transport, monkeypatch, config={"logging_endpoint": bad_endpoint})
    assert transport.calls == []


def test_public_endpoint_constructs_after_consolidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = RecordingTransport({})
    src = make_source(transport, monkeypatch)  # default public endpoints
    assert src.name


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #
def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(
        {("GET", MONITORING_URL): FakeResponse(200, {"timeSeries": []})}
    )
    src = make_source(transport, monkeypatch)
    result = src.health()
    assert set(result.keys()) == {"ok", "detail"}
    assert result["ok"] is True


def test_health_never_raises_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BoomTransport(RecordingTransport):
        def request(self, *args: Any, **kwargs: Any) -> FakeResponse:
            raise ConnectionError("network down")

    transport = BoomTransport({})
    src = make_source(transport, monkeypatch)
    result = src.health()
    assert result["ok"] is False
    assert isinstance(result["detail"], str)
    assert result["detail"]


def test_health_never_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = RecordingTransport(
        {("GET", MONITORING_URL): FakeResponse(403, {"error": "forbidden"})}
    )
    src = make_source(transport, monkeypatch)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "health check failed"


# --------------------------------------------------------------------------- #
# query() — logs mode
# --------------------------------------------------------------------------- #
def test_query_logs_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(
        {("POST", LOGGING_URL): FakeResponse(200, LOGS_PAYLOAD)}
    )
    src = make_source(transport, monkeypatch)
    rows = src.query(
        {"mode": "logs", "filter": 'severity>="INFO"', "page_size": 50}
    )
    assert len(rows) == 2

    first = rows[0]
    assert first["ts"] == "2026-06-16T10:00:00Z"
    assert first["level_or_status"] == "ERROR"
    assert first["message"] == "disk full on node-7"
    assert first["source"] == src.name
    assert first["kind"] == "logs"
    assert first["labels"]["resource.type"] == "gce_instance"
    assert first["labels"]["logName"] == f"projects/{PROJECT}/logs/syslog"
    # trace surfaced for association
    assert first["labels"]["trace"] == f"projects/{PROJECT}/traces/abc123"
    assert first["raw"] == LOGS_PAYLOAD["entries"][0]

    # jsonPayload.message fallback when textPayload absent
    second = rows[1]
    assert second["message"] == "structured hello"
    assert second["level_or_status"] == "INFO"


def test_query_logs_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(
        {("POST", LOGGING_URL): FakeResponse(200, {"entries": []})}
    )
    src = make_source(transport, monkeypatch)
    src.query({"mode": "logs", "filter": "x", "page_size": 10})
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == LOGGING_URL
    body = call["json_body"]
    assert body is not None
    assert body["resourceNames"] == [f"projects/{PROJECT}"]
    assert body["filter"] == "x"
    assert body["pageSize"] == 10
    assert "orderBy" in body
    assert call["timeout"] > 0
    assert call["headers"]["Authorization"] == "Bearer secret-tok"


# --------------------------------------------------------------------------- #
# query() — metrics mode
# --------------------------------------------------------------------------- #
def test_query_metrics_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(
        {("GET", MONITORING_URL): FakeResponse(200, METRICS_PAYLOAD)}
    )
    src = make_source(transport, monkeypatch)
    rows = src.query(
        {
            "mode": "metrics",
            "filter": 'metric.type="compute.googleapis.com/instance/cpu/utilization"',
            "interval_start": "2026-06-16T10:00:00Z",
            "interval_end": "2026-06-16T10:01:00Z",
        }
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "metrics"
    assert row["value"] == 0.73
    assert row["ts"] == "2026-06-16T10:01:00Z"  # point interval end
    assert row["message"] == "compute.googleapis.com/instance/cpu/utilization"
    # metric.labels + resource.labels merged
    assert row["labels"]["instance_name"] == "node-7"
    assert row["labels"]["zone"] == "us-1"
    assert row["labels"]["project_id"] == PROJECT
    assert row["source"] == src.name
    assert row["raw"] == METRICS_PAYLOAD["timeSeries"][0]


def test_query_metrics_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(
        {("GET", MONITORING_URL): FakeResponse(200, {"timeSeries": []})}
    )
    src = make_source(transport, monkeypatch)
    src.query(
        {
            "mode": "metrics",
            "filter": "metric.type=\"x\"",
            "interval_start": "2026-06-16T10:00:00Z",
            "interval_end": "2026-06-16T10:01:00Z",
        }
    )
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == MONITORING_URL
    params = call["params"]
    assert params is not None
    assert params["filter"] == 'metric.type="x"'
    assert params["interval.startTime"] == "2026-06-16T10:00:00Z"
    assert params["interval.endTime"] == "2026-06-16T10:01:00Z"
    assert call["headers"]["Authorization"] == "Bearer secret-tok"


def test_query_metrics_int64_and_distribution_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "timeSeries": [
            {
                "metric": {"type": "custom/req_count", "labels": {}},
                "resource": {"type": "global", "labels": {}},
                "points": [
                    {
                        "interval": {"endTime": "2026-06-16T10:05:00Z"},
                        "value": {"int64Value": "42"},
                    }
                ],
            }
        ]
    }
    transport = RecordingTransport(
        {("GET", MONITORING_URL): FakeResponse(200, payload)}
    )
    src = make_source(transport, monkeypatch)
    rows = src.query(
        {
            "mode": "metrics",
            "filter": "x",
            "interval_start": "a",
            "interval_end": "b",
        }
    )
    assert rows[0]["value"] == 42.0


@pytest.mark.parametrize(("raw", "expected"), [(True, 1.0), (False, 0.0)])
def test_point_value_normalizes_boolean_metrics(raw: bool, expected: float) -> None:
    assert _point_value({"boolValue": raw}) == expected


# --------------------------------------------------------------------------- #
# query() — error handling
# --------------------------------------------------------------------------- #
def test_query_unknown_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport({})
    src = make_source(transport, monkeypatch)
    with pytest.raises(ValueError):
        src.query({"mode": "traces"})


def test_query_missing_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport({})
    src = make_source(transport, monkeypatch)
    with pytest.raises(ValueError):
        src.query({})
