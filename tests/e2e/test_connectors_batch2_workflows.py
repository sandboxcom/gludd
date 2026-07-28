"""E2E tests for connectors batch 2 — Web/HTTP, Cloud Observability,
Notification, CI/CD, and Database connectors.

Uses mock transports/executors so no real network I/O or external services are
required. Tests the full connector lifecycle: config validation, SSRF guards,
credential resolution, query normalization, health checks, and error resilience.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass
from typing import cast

import pytest

# ============================================================================
# Test helpers — shared mock transports
# ============================================================================


def _make_transport(
    status: int = 200,
    body: object = None,
    raise_err: Exception | None = None,
):
    """Factory for a callable transport returning (status, body) or raising."""
    class Transport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def __call__(
            self,
            url: str,
            headers: dict[str, str] | None = None,
            **kwargs: object,
        ) -> tuple[int, object]:
            self.calls.append({"url": url, "headers": headers, "kwargs": kwargs})
            if raise_err:
                raise raise_err
            return status, body

    return Transport()


@dataclass
class _FakeResponse:
    status_code: int
    _body: object = None

    def json(self) -> object:
        return self._body

    @property
    def text(self) -> str:
        if self._body is None:
            return ""
        if isinstance(self._body, str):
            return self._body
        return _json.dumps(self._body)


def _fake_response(status: int, body: object = None) -> _FakeResponse:
    return _FakeResponse(status, body)


def _make_req_transport(
    status: int = 200,
    body: object = None,
):
    """Factory returning a .request(method, url, **kw) -> _FakeResponse transport."""
    class Transport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
            self.calls.append({"method": method, "url": url, "kwargs": kwargs})
            return _fake_response(status, body)

    return Transport()


def _make_get_transport(
    status: int = 200,
    body: object = None,
):
    """Factory returning a GET/POST-capable response-object transport."""
    class Transport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def get(self, url: str, **kwargs: object) -> _FakeResponse:
            self.calls.append({"url": url, "kwargs": kwargs})
            return _fake_response(status, body)

        def post(self, url: str, **kwargs: object) -> _FakeResponse:
            self.calls.append({"url": url, "kwargs": kwargs})
            return _fake_response(status, body)

    return Transport()


# ============================================================================
# 1. SearX Connector
# ============================================================================


class TestSearXConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.searx import SearXConnector

        conn = SearXConnector({"base_url": "https://searx.example.com"})
        assert conn.base_url == "https://searx.example.com"
        assert conn.timeout > 0

    def test_rejects_empty_base_url(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        from general_ludd.connectors.searx import SearXConnector

        with pytest.raises(ConnectorConfigError):
            SearXConnector({"base_url": ""})

    def test_allows_loopback_host_for_local_instance(self):
        from general_ludd.connectors.searx import SearXConnector

        conn = SearXConnector({"base_url": "http://127.0.0.1/searx"})
        assert conn.base_url == "http://127.0.0.1/searx"

    def test_rejects_metadata_ip(self):
        from general_ludd.connectors._errors import ConnectorConfigError
        from general_ludd.connectors.searx import SearXConnector

        with pytest.raises(ConnectorConfigError, match="blocked"):
            SearXConnector({"base_url": "http://169.254.169.254/searx"})

    def test_health_ok(self, monkeypatch: pytest.MonkeyPatch):
        import httpx

        def fake_get(_client: object, url: str, **kwargs: object) -> object:
            class R:
                status_code = 200
                content = b'{"version": "1.0"}'
            return R()

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        from general_ludd.connectors.searx import SearXConnector

        conn = SearXConnector({"base_url": "https://searx.example.com"})
        result = conn.health()
        assert result["ok"] is True

    def test_health_not_ok_on_error(self, monkeypatch: pytest.MonkeyPatch):
        import httpx

        def fake_get(_client: object, url: str, **kwargs: object) -> object:
            class R:
                status_code = 500
                content = b""
            return R()

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        from general_ludd.connectors.searx import SearXConnector

        conn = SearXConnector({"base_url": "https://searx.example.com"})
        result = conn.health()
        assert result["ok"] is False

    def test_search_returns_results(self, monkeypatch: pytest.MonkeyPatch):
        import httpx
        body = _json.dumps({
            "results": [
                {"title": "T1", "url": "https://a.com", "content": "snippet",
                 "engine": "google", "score": 0.9},
                {"title": "T2", "url": "https://b.com", "snippet": "desc",
                 "engine": "bing"},
            ]
        }).encode()

        def fake_get(_client: object, url: str, **kwargs: object) -> object:
            class R:
                status_code = 200
                content = body
            return R()

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        from general_ludd.connectors.searx import SearXConnector

        conn = SearXConnector({"base_url": "https://searx.example.com"})
        results = conn.search("test query")
        assert len(results) == 2
        assert results[0].title == "T1"
        assert results[0].engine == "google"
        assert results[0].score == 0.9
        assert results[1].title == "T2"

    def test_search_empty_on_non_2xx(self, monkeypatch: pytest.MonkeyPatch):
        import httpx

        def fake_get(_client: object, url: str, **kwargs: object) -> object:
            class R:
                status_code = 403
                content = b""
            return R()

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        from general_ludd.connectors.searx import SearXConnector

        conn = SearXConnector({"base_url": "https://searx.example.com"})
        results = conn.search("test")
        assert results == []

    def test_extract_results_handles_non_dict(self):
        from general_ludd.connectors.searx import _extract_results

        assert _extract_results("not a dict") == []
        assert _extract_results({"results": "not a list"}) == []
        assert _extract_results({"results": [{"title": "ok", "url": "http://x"}]}) != []


# ============================================================================
# 2. Webhook Buffer Connector
# ============================================================================


class TestWebhookBufferSource:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource()
        assert source.name == "webhook-buffer"
        assert source.KIND == "logs"
        assert source.capacity == 1000

    def test_constructs_custom_name_and_maxlen(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(name="custom-buf", maxlen=50, kind="metrics")
        assert source.name == "custom-buf"
        assert source.KIND == "metrics"
        assert source.capacity == 50

    def test_rejects_zero_maxlen(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        with pytest.raises(ValueError, match="maxlen must be positive"):
            WebhookBufferSource(maxlen=0)

    def test_push_one_stores_record(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        rec = {"ts": 100, "message": "hello"}
        assert source.push_one(rec) is True
        assert len(source) == 1

    def test_push_one_rejects_non_dict(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        assert source.push_one("not a dict") is False  # type: ignore[arg-type]
        assert len(source) == 0

    def test_push_stores_iterable(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        count = source.push([{"a": 1}, {"b": 2}])
        assert count == 2
        assert len(source) == 2

    def test_push_skips_non_dicts(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        count = source.push([{"a": 1}, "skip-me", {"b": 2}])
        assert count == 2
        assert len(source) == 2

    def test_push_non_iterable(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        assert source.push(None) == 0  # type: ignore[arg-type]
        assert len(source) == 0

    def test_ring_buffer_evicts_oldest(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=3)
        for i in range(5):
            source.push_one({"n": i})
        assert len(source) == 3
        records = source.query({})
        assert records[0]["n"] == 2
        assert records[-1]["n"] == 4

    def test_query_filters_by_kind(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        source.push_one({"kind": "logs", "msg": "a"})
        source.push_one({"kind": "metrics", "msg": "b"})
        source.push_one({"kind": "logs", "msg": "c"})

        logs = source.query({"kind": "logs"})
        assert len(logs) == 2
        assert all(r["kind"] == "logs" for r in logs)

    def test_query_filters_by_kinds(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        source.push_one({"kind": "logs"})
        source.push_one({"kind": "metrics"})
        source.push_one({"kind": "traces"})

        result = source.query({"kinds": ["logs", "traces"]})
        assert len(result) == 2

    def test_query_filters_by_since(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        source.push_one({"ts": 100, "msg": "old"})
        source.push_one({"ts": 200, "msg": "new1"})
        source.push_one({"ts": 300, "msg": "new2"})

        recent = source.query({"since": "200"})
        assert len(recent) == 2
        assert all(r.get("ts", 0) >= 200 for r in recent)

    def test_query_returns_deep_copies(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        source.push_one({"msg": "original"})
        records = source.query({})
        records[0]["msg"] = "mutated"
        records2 = source.query({})
        assert records2[0]["msg"] == "original"

    def test_health_reports_size_and_capacity(self):
        from general_ludd.connectors.webhook_buffer import WebhookBufferSource

        source = WebhookBufferSource(maxlen=10)
        source.push_one({"a": 1})
        result = source.health()
        assert result["ok"] is True
        assert result["size"] == 1
        assert result["capacity"] == 10


# ============================================================================
# 3. AWS Observability Connector
# ============================================================================


class TestAwsObservabilitySource:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        source = AwsObservabilitySource()
        assert source.name == "aws_observability"
        assert source.KIND == "aws_observability"

    def test_constructs_custom_name_and_region(self):
        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        source = AwsObservabilitySource({"name": "my-aws", "region": "us-east-1"})
        assert source.name == "my-aws"
        assert source.region == "us-east-1"

    def test_health_ok_with_injected_factory(self):
        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        def factory(service: str) -> object:
            return type("MockClient", (), {})()

        source = AwsObservabilitySource(client_factory=factory)
        result = source.health()
        assert result["ok"] is True

    def test_health_boto3_unavailable(self):
        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        def factory(service: str) -> object:
            raise ImportError("boto3 unavailable")

        source = AwsObservabilitySource(client_factory=factory)
        result = source.health()
        assert result["ok"] is False
        assert "boto3" in cast(str, result["detail"])

    def test_health_catches_generic_error(self):
        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        def factory(service: str) -> object:
            raise RuntimeError("unexpected error")

        source = AwsObservabilitySource(client_factory=factory)
        result = source.health()
        assert result["ok"] is False

    def test_query_logs_mode(self):
        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        class FakeLogsClient:
            @staticmethod
            def filter_log_events(**kwargs: object) -> dict[str, object]:
                return {
                    "events": [
                        {"timestamp": 1712345678000, "message": "hello world",
                         "logStreamName": "stream-1"},
                    ]
                }

        factories: dict[str, object] = {"logs": FakeLogsClient()}

        def factory(service: str) -> object:
            return factories[service]

        source = AwsObservabilitySource({"name": "aws"}, client_factory=factory)
        records = source.query({"mode": "logs", "logGroupName": "/aws/test"})
        assert len(records) == 1
        assert records[0]["kind"] == "logs"
        assert records[0]["message"] == "hello world"

    def test_query_metrics_mode(self):
        from datetime import datetime

        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        t1 = datetime(2025, 1, 1, 0, 0, 0)
        t2 = datetime(2025, 1, 1, 0, 1, 0)

        class FakeCwClient:
            @staticmethod
            def get_metric_data(**kwargs: object) -> dict[str, object]:
                return {
                    "MetricDataResults": [
                        {
                            "Id": "m1",
                            "Label": "CPUUtilization",
                            "Timestamps": [t1, t2],
                            "Values": [50.0, 55.0],
                        }
                    ]
                }

        factories: dict[str, object] = {"cloudwatch": FakeCwClient()}

        def factory(service: str) -> object:
            return factories[service]

        source = AwsObservabilitySource(client_factory=factory)
        records = source.query({"mode": "metrics"})
        assert len(records) == 2
        assert all(r["kind"] == "metrics" for r in records)
        assert records[0]["value"] == 50.0

    def test_query_traces_mode(self):
        from datetime import datetime

        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        class FakeXrayClient:
            @staticmethod
            def get_trace_summaries(**kwargs: object) -> dict[str, object]:
                return {
                    "TraceSummaries": [
                        {
                            "Id": "trace-1",
                            "Duration": 100.5,
                            "HasError": True,
                            "ServiceIds": [{"Name": "my-svc"}],
                            "StartTime": datetime(2025, 1, 1, 0, 0, 0),
                        }
                    ]
                }

        factories: dict[str, object] = {"xray": FakeXrayClient()}

        def factory(service: str) -> object:
            return factories[service]

        source = AwsObservabilitySource(client_factory=factory)
        records = source.query({"mode": "traces"})
        assert len(records) == 1
        assert records[0]["kind"] == "traces"
        assert records[0]["level_or_status"] == "error"
        assert records[0]["value"] == 100.5

    def test_query_events_mode(self):
        from datetime import datetime

        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        class FakeCtClient:
            @staticmethod
            def lookup_events(**kwargs: object) -> dict[str, object]:
                return {
                    "Events": [
                        {
                            "EventName": "RunInstances",
                            "Username": "admin",
                            "EventTime": datetime(2025, 1, 1, 0, 0, 0),
                            "EventSource": "ec2.amazonaws.com",
                            "AwsRegion": "us-east-1",
                            "Resources": [],
                        }
                    ]
                }

        factories: dict[str, object] = {"cloudtrail": FakeCtClient()}

        def factory(service: str) -> object:
            return factories[service]

        source = AwsObservabilitySource(client_factory=factory)
        records = source.query({"mode": "events"})
        assert len(records) == 1
        assert records[0]["kind"] == "logs"
        assert records[0]["level_or_status"] == "audit"

    def test_query_unknown_mode_raises(self):
        from general_ludd.connectors.aws_observability import AwsObservabilitySource

        source = AwsObservabilitySource()
        with pytest.raises(ValueError, match="unknown or missing query mode"):
            source.query({"mode": "bogus"})


# ============================================================================
# 4. GCP Observability Connector
# ============================================================================


class TestGcpObservabilitySource:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.gcp_observability import GcpObservabilitySource

        transport = _make_req_transport(status=200, body={})
        source = GcpObservabilitySource(
            {"project": "my-project"},
            transport=transport,
            token="test-token",
        )
        assert source.name == "gcp:my-project"
        assert source.KIND == "gcp_observability"

    def test_rejects_loopback_endpoint(self):
        from general_ludd.connectors.gcp_observability import GcpObservabilitySource

        transport = _make_req_transport()
        with pytest.raises(ValueError, match="internal/SSRF"):
            GcpObservabilitySource(
                {"project": "p", "logging_endpoint": "http://127.0.0.1/v2/entries:list"},
                transport=transport,
                token="t",
            )

    def test_health_ok(self):
        from general_ludd.connectors.gcp_observability import GcpObservabilitySource

        transport = _make_req_transport(status=200, body={})
        source = GcpObservabilitySource(
            {"project": "p"}, transport=transport, token="t"
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_error(self):
        from general_ludd.connectors.gcp_observability import GcpObservabilitySource

        transport = _make_req_transport(status=500, body={})
        source = GcpObservabilitySource(
            {"project": "p"}, transport=transport, token="t"
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_logs_normalizes_entries(self):
        from general_ludd.connectors.gcp_observability import GcpObservabilitySource

        body = {
            "entries": [
                {
                    "timestamp": "2025-01-01T00:00:00Z",
                    "severity": "ERROR",
                    "textPayload": "disk full",
                    "resource": {"type": "gce_instance"},
                    "trace": "projects/p/traces/abc",
                }
            ]
        }
        transport = _make_req_transport(status=200, body=body)
        source = GcpObservabilitySource(
            {"project": "p"}, transport=transport, token="t"
        )
        records = source.query({"mode": "logs"})
        assert len(records) == 1
        assert records[0]["kind"] == "logs"
        assert records[0]["message"] == "disk full"
        assert records[0]["level_or_status"] == "ERROR"
        assert records[0]["labels"]["resource.type"] == "gce_instance"

    def test_query_logs_json_payload_message(self):
        from general_ludd.connectors.gcp_observability import GcpObservabilitySource

        body = {
            "entries": [
                {
                    "timestamp": "2025-01-01T00:00:00Z",
                    "severity": "INFO",
                    "jsonPayload": {"message": "from json"},
                    "resource": {},
                }
            ]
        }
        transport = _make_req_transport(status=200, body=body)
        source = GcpObservabilitySource(
            {"project": "p"}, transport=transport, token="t"
        )
        records = source.query({"mode": "logs"})
        assert records[0]["message"] == "from json"

    def test_query_metrics_normalizes_time_series(self):
        from general_ludd.connectors.gcp_observability import GcpObservabilitySource

        body = {
            "timeSeries": [
                {
                    "metric": {"type": "compute.googleapis.com/instance/cpu/utilization",
                               "labels": {"instance_name": "vm-1"}},
                    "resource": {"type": "gce_instance",
                                 "labels": {"project_id": "p"}},
                    "points": [
                        {"interval": {"endTime": "2025-01-01T00:00:00Z"},
                         "value": {"doubleValue": 0.75}},
                        {"interval": {"endTime": "2025-01-01T00:01:00Z"},
                         "value": {"int64Value": "60"}},
                    ],
                }
            ]
        }
        transport = _make_req_transport(status=200, body=body)
        source = GcpObservabilitySource(
            {"project": "p"}, transport=transport, token="t"
        )
        records = source.query({"mode": "metrics"})
        assert len(records) == 2
        assert all(r["kind"] == "metrics" for r in records)
        assert records[0]["value"] == 0.75
        assert records[1]["value"] == 60.0

    def test_query_unknown_mode_raises(self):
        from general_ludd.connectors.gcp_observability import GcpObservabilitySource

        transport = _make_req_transport()
        source = GcpObservabilitySource(
            {"project": "p"}, transport=transport, token="t"
        )
        with pytest.raises(ValueError, match="unknown query mode"):
            source.query({"mode": "bogus"})

    def test_missing_token_raises(self):
        from general_ludd.connectors.gcp_observability import GcpObservabilitySource

        transport = _make_req_transport()
        with pytest.raises(ValueError, match="missing Bearer token"):
            GcpObservabilitySource(
                {"project": "p", "token_env": "MISSING_VAR_XYZ"},
                transport=transport,
            )

    def test_point_value_bool(self):
        from general_ludd.connectors.gcp_observability import _point_value

        assert _point_value({"boolValue": True}) == 1.0

    def test_point_value_distribution(self):
        from general_ludd.connectors.gcp_observability import _point_value

        assert _point_value({"distributionValue": {"mean": 42.5}}) == 42.5


# ============================================================================
# 5. Azure Monitor Connector
# ============================================================================


class TestAzureMonitorSource:
    def test_constructs_with_valid_config(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AZURE_TOKEN", "test-token")
        from general_ludd.connectors.azure_monitor import AzureMonitorSource

        source = AzureMonitorSource({
            "workspace_id": "ws-123",
            "token_env": "AZURE_TOKEN",
        })
        assert source.name == "azure-monitor"
        assert source.KIND == "logs"

    def test_rejects_missing_workspace_id(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AZURE_TOKEN", "t")
        from general_ludd.connectors.azure_monitor import AzureMonitorSource

        with pytest.raises(ValueError, match="workspace_id"):
            AzureMonitorSource({"token_env": "AZURE_TOKEN"})

    def test_rejects_missing_token_env(self):
        from general_ludd.connectors.azure_monitor import AzureMonitorSource

        with pytest.raises(ValueError, match="token_env"):
            AzureMonitorSource({"workspace_id": "ws-123"})

    def test_health_ok_with_injected_transport(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AZURE_TOKEN", "t")
        from general_ludd.connectors.azure_monitor import AzureMonitorSource

        body = {"tables": [{"name": "PrimaryResult", "columns": [{"name": "print_1"}], "rows": [["1"]]}]}

        def transport(method: str, url: str,
                      headers: object, json_body: object, timeout: float) -> _FakeResponse:
            return _fake_response(200, body)

        source = AzureMonitorSource({
            "workspace_id": "ws-123",
            "token_env": "AZURE_TOKEN",
            "transport": transport,
        })
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AZURE_TOKEN", "t")
        from general_ludd.connectors.azure_monitor import AzureMonitorSource

        def transport(method: str, url: str,
                      headers: object, json_body: object, timeout: float) -> _FakeResponse:
            return _fake_response(500, {})

        source = AzureMonitorSource({
            "workspace_id": "ws-123",
            "token_env": "AZURE_TOKEN",
            "transport": transport,
        })
        result = source.health()
        assert result["ok"] is False

    def test_query_normalizes_table_rows(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AZURE_TOKEN", "t")
        from general_ludd.connectors.azure_monitor import AzureMonitorSource

        body = {
            "tables": [
                {
                    "name": "PrimaryResult",
                    "columns": [
                        {"name": "TimeGenerated"},
                        {"name": "Level"},
                        {"name": "Message"},
                        {"name": "Value"},
                    ],
                    "rows": [
                        ["2025-01-01T00:00:00Z", "Error", "Something broke", 42],
                        ["2025-01-01T00:01:00Z", "Info", "All good", None],
                    ],
                }
            ]
        }

        def transport(method: str, url: str,
                      headers: object, json_body: object, timeout: float) -> _FakeResponse:
            return _fake_response(200, body)

        source = AzureMonitorSource({
            "workspace_id": "ws-123",
            "token_env": "AZURE_TOKEN",
            "transport": transport,
        })
        records = source.query({"query": "sometable | take 10", "timespan": "PT1H"})
        assert len(records) == 2
        assert records[0]["level_or_status"] == "Error"
        assert records[0]["value"] == 42.0
        assert records[1]["level_or_status"] == "Info"
        assert records[1]["value"] is None

    def test_query_accepts_string_spec(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AZURE_TOKEN", "t")
        from general_ludd.connectors.azure_monitor import AzureMonitorSource

        body = {"tables": []}

        def transport(method: str, url: str,
                      headers: object, json_body: object, timeout: float) -> _FakeResponse:
            return _fake_response(200, body)

        source = AzureMonitorSource({
            "workspace_id": "ws-123",
            "token_env": "AZURE_TOKEN",
            "transport": transport,
        })
        records = source.query("sometable | take 1")
        assert records == []

    def test_rejects_empty_kql(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AZURE_TOKEN", "t")
        from general_ludd.connectors.azure_monitor import AzureMonitorSource

        def transport(method: str, url: str,
                      headers: object, json_body: object, timeout: float) -> _FakeResponse:
            return _fake_response(200, {})

        source = AzureMonitorSource({
            "workspace_id": "ws-123",
            "token_env": "AZURE_TOKEN",
            "transport": transport,
        })
        with pytest.raises(ValueError, match="empty KQL"):
            source.query({"query": "", "timespan": "PT1H"})


# ============================================================================
# 6. Slack Connector
# ============================================================================

TEST_BASE_URL = "https://slack.example.com"


class TestSlackConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport(status=200, body={"ok": True})
        source = SlackSource(
            {"base_url": TEST_BASE_URL, "token_env": "SLACK_TOKEN"},
            transport=transport,
        )
        assert source.name == "slack"

    def test_rejects_missing_base_url(self):
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport()
        with pytest.raises(ValueError, match="base_url"):
            SlackSource(
                {"token_env": "SLACK_TOKEN"},
                transport=transport,
            )

    def test_rejects_missing_token_env(self):
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport()
        with pytest.raises(ValueError, match="token_env"):
            SlackSource(
                {"base_url": TEST_BASE_URL},
                transport=transport,
            )

    def test_rejects_loopback_base_url(self):
        from general_ludd.connectors._errors import SSRFError
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport()
        with pytest.raises(SSRFError):
            SlackSource(
                {"base_url": "http://127.0.0.1", "token_env": "SLACK_TOKEN"},
                transport=transport,
            )

    def test_health_ok(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport(status=200, body={"ok": True})
        source = SlackSource(
            {"base_url": TEST_BASE_URL, "token_env": "SLACK_TOKEN"},
            transport=transport,
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_401(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SLACK_TOKEN", "bad-token")
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport(status=401, body={})
        source = SlackSource(
            {"base_url": TEST_BASE_URL, "token_env": "SLACK_TOKEN"},
            transport=transport,
        )
        result = source.health()
        assert result["ok"] is False
        assert "authentication" in str(result.get("error", ""))

    def test_send_notification_via_webhook(self):
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport(status=200, body={"ok": True})
        source = SlackSource(
            {
                "base_url": TEST_BASE_URL,
                "token_env": "SLACK_TOKEN",
                "webhook_url": "https://hooks.slack.com/services/T/B/Q",
            },
            transport=transport,
        )
        result = source.send_notification("Hello from test")
        assert result["ok"] is True

    def test_send_notification_via_api(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport(status=200, body={"ok": True})
        source = SlackSource(
            {
                "base_url": TEST_BASE_URL,
                "token_env": "SLACK_TOKEN",
                "channel_id": "C12345",
            },
            transport=transport,
        )
        result = source.send_notification("Hello via API")
        assert result["ok"] is True

    def test_send_notification_fails_without_channel_or_webhook(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport()
        source = SlackSource(
            {"base_url": TEST_BASE_URL, "token_env": "SLACK_TOKEN"},
            transport=transport,
        )
        with pytest.raises(ValueError, match="webhook_url or channel_id"):
            source.send_notification("test")

    def test_read_channel_history_normalizes(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
        from general_ludd.connectors.slack import SlackSource

        body = {
            "ok": True,
            "messages": [
                {"ts": "1712345678.000000", "user": "U1",
                 "text": "hello", "subtype": "bot_message"},
            ]
        }
        transport = _make_get_transport(status=200, body=body)
        source = SlackSource(
            {
                "base_url": TEST_BASE_URL,
                "token_env": "SLACK_TOKEN",
                "channel_id": "C12345",
            },
            transport=transport,
        )
        records = source.read_channel_history(count=10)
        assert len(records) == 1
        assert records[0]["kind"] == "chat"
        assert records[0]["message"] == "hello"
        assert records[0]["level_or_status"] == "bot_message"

    def test_read_channel_history_empty_on_transport_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
        from general_ludd.connectors.slack import SlackSource

        class FailingTransport:
            def get(self, url: str, **kwargs: object) -> _FakeResponse:
                raise ConnectionError("timeout")

            def post(self, url: str, **kwargs: object) -> _FakeResponse:
                raise ConnectionError("timeout")

        source = SlackSource(
            {
                "base_url": TEST_BASE_URL,
                "token_env": "SLACK_TOKEN",
                "channel_id": "C12345",
            },
            transport=FailingTransport(),
        )
        records = source.read_channel_history()
        assert records == []

    def test_read_channel_history_needs_channel_id(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
        from general_ludd.connectors.slack import SlackSource

        transport = _make_get_transport()
        source = SlackSource(
            {"base_url": TEST_BASE_URL, "token_env": "SLACK_TOKEN"},
            transport=transport,
        )
        with pytest.raises(ValueError, match="channel_id"):
            source.read_channel_history()


# ============================================================================
# 7. PagerDuty Connector
# ============================================================================


class TestPagerDutySource:
    def test_constructs_with_defaults(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PAGERDUTY_TOKEN", "pd-test")
        from general_ludd.connectors.pagerduty import PagerDutySource

        transport = _make_get_transport(status=200, body={"incidents": []})
        source = PagerDutySource({}, transport=transport)
        assert source.name == "pagerduty"
        assert source.KIND == "incidents"

    def test_rejects_internal_base_url(self):
        from general_ludd.connectors.pagerduty import PagerDutySource

        transport = _make_get_transport()
        with pytest.raises(ValueError, match="internal/loopback"):
            PagerDutySource({"base_url": "http://10.0.0.1"}, transport=transport)

    def test_health_ok(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PAGERDUTY_TOKEN", "pd-test")
        from general_ludd.connectors.pagerduty import PagerDutySource

        transport = _make_get_transport(status=200, body={"incidents": []})
        source = PagerDutySource({}, transport=transport)
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_500(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PAGERDUTY_TOKEN", "pd-test")
        from general_ludd.connectors.pagerduty import PagerDutySource

        transport = _make_get_transport(status=500, body={})
        source = PagerDutySource({}, transport=transport)
        result = source.health()
        assert result["ok"] is False

    def test_health_not_ok_on_transport_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PAGERDUTY_TOKEN", "pd-test")
        from general_ludd.connectors.pagerduty import PagerDutySource

        class ErrTransport:
            def get(self, url: str, **kwargs: object) -> _FakeResponse:
                raise OSError("refused")

        source = PagerDutySource({}, transport=ErrTransport())
        result = source.health()
        assert result["ok"] is False

    def test_query_normalizes_incidents(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PAGERDUTY_TOKEN", "pd-test")
        from general_ludd.connectors.pagerduty import PagerDutySource

        body = {
            "incidents": [
                {
                    "id": "INC001",
                    "title": "Disk full on web-01",
                    "status": "triggered",
                    "urgency": "high",
                    "created_at": "2025-01-01T00:00:00Z",
                    "service": {"summary": "Web Tier"},
                    "escalation_policy": {"summary": "OnCall Rotation"},
                    "assignments": [
                        {"assignee": {"summary": "John Doe"}},
                    ],
                }
            ]
        }
        transport = _make_get_transport(status=200, body=body)
        source = PagerDutySource({}, transport=transport)
        records = source.query({"statuses": ["triggered"]})
        assert len(records) == 1
        assert records[0]["kind"] == "incidents"
        assert "Disk full" in str(records[0]["message"])
        assert records[0]["level_or_status"] == "triggered"
        assert "John Doe" in str(records[0]["labels"]["assignees"])

    def test_query_empty_on_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PAGERDUTY_TOKEN", "pd-test")
        from general_ludd.connectors.pagerduty import PagerDutySource

        class ErrTransport:
            def get(self, url: str, **kwargs: object) -> _FakeResponse:
                return _fake_response(500, {})

        source = PagerDutySource({}, transport=ErrTransport())
        with pytest.raises(RuntimeError):
            source.query({})

    def test_fetch_log_entries(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PAGERDUTY_TOKEN", "pd-test")
        from general_ludd.connectors.pagerduty import PagerDutySource

        body = {
            "log_entries": [
                {"id": "L1", "channel": {"type": "web"},
                 "created_at": "2025-01-01T00:00:00Z"},
            ]
        }
        transport = _make_get_transport(status=200, body=body)
        source = PagerDutySource({}, transport=transport)
        entries = source.fetch_log_entries("INC001")
        assert len(entries) == 1
        assert entries[0]["id"] == "L1"


# ============================================================================
# 8. Opsgenie Connector
# ============================================================================


class TestOpsgenieSource:
    def test_constructs_with_defaults(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPSGENIE_API_KEY", "og-test")
        from general_ludd.connectors.opsgenie import OpsgenieSource

        transport = _make_get_transport(status=200, body={"data": []})
        source = OpsgenieSource({}, transport=transport)
        assert source.name == "opsgenie"
        assert source.KIND == "incidents"

    def test_health_ok(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPSGENIE_API_KEY", "og-test")
        from general_ludd.connectors.opsgenie import OpsgenieSource

        transport = _make_get_transport(status=200, body={"data": []})
        source = OpsgenieSource({}, transport=transport)
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_401(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPSGENIE_API_KEY", "bad-key")
        from general_ludd.connectors.opsgenie import OpsgenieSource

        transport = _make_get_transport(status=401, body={})
        source = OpsgenieSource({}, transport=transport)
        result = source.health()
        assert result["ok"] is False

    def test_query_normalizes_alerts(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPSGENIE_API_KEY", "og-test")
        from general_ludd.connectors.opsgenie import OpsgenieSource

        body = {
            "data": [
                {
                    "id": "alert-1",
                    "tinyId": "123",
                    "message": "CPU > 90%",
                    "status": "open",
                    "priority": "P1",
                    "createdAt": "2025-01-01T00:00:00.000Z",
                    "owner": "team-a",
                    "tags": ["production", "critical"],
                    "source": "prometheus",
                }
            ]
        }
        transport = _make_get_transport(status=200, body=body)
        source = OpsgenieSource({}, transport=transport)
        records = source.query({"limit": 10})
        assert len(records) == 1
        assert records[0]["message"] == "CPU > 90%"
        assert records[0]["level_or_status"] == "open/P1"
        assert records[0]["labels"]["id"] == "alert-1"
        assert records[0]["ts"] is not None


# ============================================================================
# 9. Jenkins Connector
# ============================================================================


class TestJenkinsConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        transport = _make_transport(status=200, body={"builds": []})
        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com", "job": "my-job"},
            http_get=transport,
        )
        assert "jenkins" in source.name
        assert "my-job" in source.name
        assert source.KIND == "pipeline"

    def test_rejects_empty_base_url(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        with pytest.raises(ValueError, match="base_url"):
            JenkinsSource({"base_url": ""})

    def test_rejects_internal_base_url(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        with pytest.raises(ValueError, match="SSRF"):
            JenkinsSource({"base_url": "http://169.254.169.254"})

    def test_health_ok(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        transport = _make_transport(status=200, body={"builds": []})
        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_500(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        transport = _make_transport(status=500, body={})
        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is False

    def test_health_not_ok_on_transport_error(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        def transport(url: str, headers: dict[str, str]) -> tuple[int, object]:
            raise OSError("timeout")

        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_normalizes_builds(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        body = {
            "builds": [
                {
                    "number": 42,
                    "result": "SUCCESS",
                    "timestamp": 1712345678000,
                    "url": "https://jenkins/job/test/42",
                    "duration": 120000,
                },
                {
                    "number": 43,
                    "result": None,
                    "timestamp": 1712345700000,
                    "url": "https://jenkins/job/test/43",
                    "duration": 30000,
                },
            ]
        }
        transport = _make_transport(status=200, body=body)
        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com", "job": "test"},
            http_get=transport,
        )
        records = source.query({})
        assert len(records) == 2
        assert records[0]["level_or_status"] == "SUCCESS"
        assert records[0]["message"] == "test#42"
        assert records[1]["level_or_status"] == "UNKNOWN"

    def test_query_filters_by_result(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        body = {
            "builds": [
                {"number": 1, "result": "SUCCESS", "timestamp": 1000},
                {"number": 2, "result": "FAILURE", "timestamp": 2000},
            ]
        }
        transport = _make_transport(status=200, body=body)
        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"}, http_get=transport
        )
        records = source.query({"result": "FAILURE"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "FAILURE"

    def test_query_respects_limit(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        body = {
            "builds": [
                {"number": i, "result": "SUCCESS", "timestamp": 1000 * i}
                for i in range(10)
            ]
        }
        transport = _make_transport(status=200, body=body)
        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"}, http_get=transport
        )
        records = source.query({"limit": 3})
        assert len(records) == 3

    def test_query_empty_on_non_2xx(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        transport = _make_transport(status=404, body={})
        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"}, http_get=transport
        )
        records = source.query({})
        assert records == []

    def test_auth_headers_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("JENKINS_USER", "admin")
        monkeypatch.setenv("JENKINS_TOKEN", "secret123")
        from general_ludd.connectors.jenkins import JenkinsSource

        transport = _make_transport(status=200, body={"builds": []})
        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"}, http_get=transport
        )
        headers = source._auth_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")


# ============================================================================
# 10. CircleCI Connector
# ============================================================================


class TestCircleCiConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.circleci import CircleCiSource

        transport = _make_transport(status=200, body={"items": []})
        source = CircleCiSource(
            {"project_slug": "gh/myorg/myrepo"}, http_get=transport,
        )
        assert "circleci" in source.name
        assert source.project_slug == "gh/myorg/myrepo"
        assert source.KIND == "pipeline"

    def test_rejects_missing_project_slug(self):
        from general_ludd.connectors.circleci import CircleCiSource

        with pytest.raises(ValueError, match="project_slug"):
            CircleCiSource({})

    def test_health_ok(self):
        from general_ludd.connectors.circleci import CircleCiSource

        transport = _make_transport(status=200, body={"items": []})
        source = CircleCiSource(
            {"project_slug": "gh/org/repo"}, http_get=transport,
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_500(self):
        from general_ludd.connectors.circleci import CircleCiSource

        transport = _make_transport(status=500, body={})
        source = CircleCiSource(
            {"project_slug": "gh/org/repo"}, http_get=transport,
        )
        result = source.health()
        assert result["ok"] is False

    def test_health_not_ok_on_transport_error(self):
        from general_ludd.connectors.circleci import CircleCiSource

        def transport(url: str, headers: dict[str, str]) -> tuple[int, object]:
            raise OSError("timeout")

        source = CircleCiSource(
            {"project_slug": "gh/org/repo"}, http_get=transport,
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_normalizes_pipelines(self):
        from general_ludd.connectors.circleci import CircleCiSource

        body = {
            "items": [
                {
                    "id": "pipe-1",
                    "number": 100,
                    "state": "completed",
                    "created_at": "2025-01-01T12:00:00.000Z",
                    "vcs": {"revision": "abc123def456", "branch": "main"},  # pragma: allowlist secret
                }
            ]
        }
        transport = _make_transport(status=200, body=body)
        source = CircleCiSource(
            {"project_slug": "gh/org/repo"}, http_get=transport,
        )
        records = source.query({})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "completed"
        assert "abc123def456" in str(records[0]["message"])  # pragma: allowlist secret
        assert "main" in str(records[0]["message"])

    def test_query_empty_on_transport_error(self):
        from general_ludd.connectors.circleci import CircleCiSource

        def transport(url: str, headers: dict[str, str]) -> tuple[int, object]:
            raise ConnectionError("timeout")

        source = CircleCiSource(
            {"project_slug": "gh/org/repo"}, http_get=transport,
        )
        records = source.query({})
        assert records == []

    def test_fetch_workflows_normalizes(self):
        from general_ludd.connectors.circleci import CircleCiSource

        transport = _make_transport(
            status=200,
            body={
                "items": [
                    {
                        "id": "wf-1",
                        "name": "build",
                        "status": "success",
                        "pipeline_number": 100,
                        "created_at": "2025-01-01T12:00:00.000Z",
                    }
                ]
            },
        )
        source = CircleCiSource(
            {"project_slug": "gh/org/repo"}, http_get=transport,
        )
        records = source.fetch_workflows("pipe-1")
        assert len(records) == 1
        assert records[0]["level_or_status"] == "success"
        assert records[0]["message"] == "build"


# ============================================================================
# 11. Travis CI Connector
# ============================================================================


class TestTravisCiConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.travis import TravisSource

        source = TravisSource(
            {"slug": "myorg/myrepo"},
            transport=lambda m, u, h, t: (200, b'{"builds":[]}'),
        )
        assert source.name == "travis"
        assert source.KIND == "pipeline"
        assert source.slug == "myorg/myrepo"

    def test_rejects_internal_base_url(self):
        from general_ludd.connectors.travis import TravisSource

        with pytest.raises((ValueError, RuntimeError)):
            TravisSource({"slug": "x", "base_url": "http://10.0.0.1"})

    def test_health_ok(self):
        from general_ludd.connectors.travis import TravisSource

        source = TravisSource(
            {"slug": "myorg/myrepo"},
            transport=lambda m, u, h, t: (200, b'{"builds":[]}'),
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_500(self):
        from general_ludd.connectors.travis import TravisSource

        source = TravisSource(
            {"slug": "myorg/myrepo"},
            transport=lambda m, u, h, t: (500, b""),
        )
        result = source.health()
        assert result["ok"] is False

    def test_health_not_ok_on_transport_error(self):
        from general_ludd.connectors.travis import TravisSource

        def transport(method: str, url: str,
                      headers: object, timeout: float) -> tuple[int, bytes]:
            raise OSError("timeout")

        source = TravisSource({"slug": "myorg/myrepo"}, transport=transport)
        result = source.health()
        assert result["ok"] is False

    def test_query_normalizes_builds(self):
        from general_ludd.connectors.travis import TravisSource

        body = _json.dumps({
            "builds": [
                {
                    "id": "b1",
                    "number": 42,
                    "state": "passed",
                    "finished_at": "2025-01-01T12:00:00Z",
                    "branch": {"name": "main"},
                    "commit": {"sha": "abc123def45678901234"},  # pragma: allowlist secret
                }
            ]
        })
        source = TravisSource(
            {"slug": "myorg/myrepo"},
            transport=lambda m, u, h, t: (200, body.encode()),
        )
        records = source.query({})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "passed"
        assert records[0]["kind"] == "pipeline"
        assert "main" in str(records[0]["message"])

    def test_fetch_log_returns_content(self):
        from general_ludd.connectors.travis import TravisSource

        resp_body = b'{"content": "build log line 1\\nbuild log line 2\\n"}'
        source = TravisSource(
            {"slug": "myorg/myrepo"},
            transport=lambda m, u, h, t: (200, resp_body),
        )
        log = source.fetch_log("job-1")
        assert "build log line 1" in log


# ============================================================================
# 12. MongoDB Stats Connector
# ============================================================================


class TestMongoDbStatsConnector:
    def test_constructs_with_no_config(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        source = MongoDbStatsSource()
        assert source.name == "mongodb"
        assert source.KIND == "metrics"

    def test_constructs_custom_name(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        source = MongoDbStatsSource({"name": "my-mongo"})
        assert source.name == "my-mongo"

    def test_health_ok_with_injected_executor(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        def executor(command: str) -> dict[str, object]:
            return {"ok": 1}

        source = MongoDbStatsSource(executor=executor)
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_executor_unavailable(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        source = MongoDbStatsSource({"uri_env": "MISSING_ENV_NOEXIST"})
        result = source.health()
        assert result["ok"] is False

    def test_health_not_ok_when_executor_fails(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        def executor(command: str) -> dict[str, object]:
            raise RuntimeError("db down")

        source = MongoDbStatsSource(executor=executor)
        result = source.health()
        assert result["ok"] is False

    def test_query_server_status_normalizes(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        def executor(command: str) -> dict[str, object]:
            if command == "serverStatus":
                return {
                    "connections": {"current": 10, "available": 500, "active": 2},
                    "opcounters": {"insert": 100, "query": 200, "update": 50},
                    "wiredTiger": {
                        "cache": {
                            "bytes currently in the cache": 524288000,
                            "maximum bytes configured": 1073741824,
                        }
                    },
                }
            if command == "currentOp":
                return {"inprog": [{}, {}]}
            if command == "replSetGetStatus":
                return {"members": []}
            return {}

        source = MongoDbStatsSource(executor=executor)
        records = source.query({})
        assert len(records) >= 7  # 3 connections + 3 opcounters + 2 wiredTiger + 1 currentOp

        metrics = {r["message"] for r in records}
        assert "connections.current" in metrics
        assert "opcounters.insert" in metrics
        assert "wiredTiger.cache.bytes currently in the cache" in metrics
        assert "currentOp.active" in metrics

    def test_query_replication_normalizes(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        primary_ts = 1712345678.0
        secondary_ts = primary_ts - 5.0  # 5 second lag

        def executor(command: str) -> dict[str, object]:
            if command == "serverStatus":
                return {}
            if command == "currentOp":
                return {"inprog": []}
            if command == "replSetGetStatus":
                return {
                    "members": [
                        {"name": "node1:27017", "stateStr": "PRIMARY",
                         "optimeDate": primary_ts},
                        {"name": "node2:27017", "stateStr": "SECONDARY",
                         "optimeDate": secondary_ts},
                    ]
                }
            return {}

        source = MongoDbStatsSource(executor=executor)
        records = source.query({})
        repl_records = [r for r in records if r["message"] == "replication.oplog_lag_seconds"]
        assert len(repl_records) == 2
        assert repl_records[1]["value"] == 5.0  # secondary lag

    def test_query_empty_on_executor_unavailable(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        source = MongoDbStatsSource({"uri_env": "MISSING_ENV_XYZ"})
        records = source.query({})
        assert records == []

    def test_query_survives_command_errors(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        call_count = 0

        def executor(command: str) -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if command == "serverStatus":
                raise RuntimeError("boom")
            if command == "currentOp":
                return {"inprog": [{"op": "query"}]}
            if command == "replSetGetStatus":
                return {"members": []}
            return {}

        source = MongoDbStatsSource(executor=executor)
        records = source.query({})
        # serverStatus failed, currentOp succeeded
        assert len(records) == 1
        assert records[0]["message"] == "currentOp.active"


# ============================================================================
# 13. Cassandra Stats Connector
# ============================================================================


class TestCassandraStatsConnector:
    def test_constructs_with_no_config(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        source = CassandraStatsSource()
        assert source.name == "cassandra"
        assert source.KIND == "metrics"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        source = CassandraStatsSource({"name": "cass-prod", "jmx_url": "http://cass:7070/metrics"})
        assert source.name == "cass-prod"

    def test_health_ok_with_injected_executor(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        def executor(command: str) -> list[dict[str, object]]:
            return [{"metric": "tp_active", "value": 5}]

        source = CassandraStatsSource(executor=executor)
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_without_executor(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        source = CassandraStatsSource({"jmx_url": "http://127.0.0.1:7070/metrics"})
        result = source.health()
        assert result["ok"] is False  # SSRF rejects loopback

    def test_health_not_ok_when_probe_fails(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        def executor(command: str) -> list[dict[str, object]]:
            raise RuntimeError("nodetool not found")

        source = CassandraStatsSource(executor=executor)
        result = source.health()
        assert result["ok"] is False

    def test_query_normalizes_all_commands(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        def executor(command: str) -> list[dict[str, object]]:
            rows: dict[str, list[dict[str, object]]] = {
                "compactionstats": [
                    {"metric": "pending_compactions", "value": 3, "keyspace": "ks1", "table": ""},
                ],
                "tablestats": [
                    {"metric": "read_latency", "value": 0.5, "keyspace": "ks1", "table": "t1"},
                    {"metric": "write_latency", "value": 1.2, "keyspace": "ks1", "table": "t1"},
                ],
                "tpstats": [
                    {"metric": "tp_active", "value": 2},
                    {"metric": "tp_pending", "value": 0},
                ],
            }
            return rows.get(command, [])

        source = CassandraStatsSource(executor=executor)
        records = source.query({})
        assert len(records) == 5

        messages = {r["message"] for r in records}
        assert "pending_compactions" in messages
        assert "read_latency" in messages
        assert "tp_active" in messages

    def test_query_skips_rows_without_metric(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        def executor(command: str) -> list[dict[str, object]]:
            if command != "compactionstats":
                return []
            return [
                {"metric": None, "value": 1},  # skipped
                {"metric": "valid_metric", "value": 42},
            ]

        source = CassandraStatsSource(executor=executor)
        records = source.query({})
        assert len(records) == 1
        assert records[0]["message"] == "valid_metric"

    def test_query_empty_on_executor_unavailable(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        source = CassandraStatsSource({"jmx_url": "http://127.0.0.1:7070/metrics"})
        records = source.query({})
        assert records == []

    def test_query_survives_command_errors(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        call_count = 0

        def executor(command: str) -> list[dict[str, object]]:
            nonlocal call_count
            call_count += 1
            if command == "compactionstats":
                raise RuntimeError("timeout")
            return [{"metric": f"{command}_ok", "value": 1}]

        source = CassandraStatsSource(executor=executor)
        records = source.query({})
        # compactionstats failed, tablestats + tpstats succeeded
        assert len(records) == 2
        assert all(r["level_or_status"] == "ok" for r in records)

    def test_record_structure(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        def executor(command: str) -> list[dict[str, object]]:
            if command != "compactionstats":
                return []
            return [{"metric": "test_metric", "value": 99, "keyspace": "ks", "table": "tbl"}]

        source = CassandraStatsSource(executor=executor)
        records = source.query({})
        assert len(records) == 1
        r = records[0]
        assert r["kind"] == "metrics"
        assert r["value"] == 99
        assert r["labels"]["keyspace"] == "ks"
        assert r["labels"]["table"] == "tbl"
        assert r["labels"]["command"] == "compactionstats"
        assert isinstance(r["ts"], float)


# ============================================================================
# 14. _errors — sanitization helpers
# ============================================================================


class TestConnectorErrors:
    def test_sanitize_exc_message_returns_type_name(self):
        from general_ludd.connectors._errors import sanitize_exc_message

        result = sanitize_exc_message(ValueError("secret: abc123"))
        assert result == "ValueError"

    def test_sanitize_str_redacts_tokens(self):
        from general_ludd.connectors._errors import sanitize_str

        text = "Bearer abcdefghij1234567890_token"
        result = sanitize_str(text)
        assert "REDACTED" in result
        assert "abcdefghij" not in result

    def test_sanitize_str_redacts_urls(self):
        from general_ludd.connectors._errors import sanitize_str

        text = "error at https://api.example.com/v1/secret"
        result = sanitize_str(text)
        assert "REDACTED-URL" in result

    def test_sanitize_str_redacts_paths(self):
        from general_ludd.connectors._errors import sanitize_str

        text = "Error reading /home/user/.aws/credentials"
        result = sanitize_str(text)
        assert "REDACTED-PATH" in result

    def test_ssrf_error_is_value_error(self):
        from general_ludd.connectors._errors import ConnectorConfigError, SSRFError

        assert issubclass(SSRFError, ValueError)
        assert issubclass(ConnectorConfigError, ValueError)

    def test_sanitize_str_plain_text_passthrough(self):
        from general_ludd.connectors._errors import sanitize_str

        assert sanitize_str("simple message") == "simple message"
