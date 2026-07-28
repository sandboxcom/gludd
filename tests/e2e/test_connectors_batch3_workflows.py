"""E2E tests for connectors batch 3 — CI/CD, messaging, monitoring, storage, cloud.

Covers 28 connector modules not yet touched by batch1 or the original
connectors_workflows suite. Uses mock transports/executors — no real network I/O.

Targets:
  CI/CD:        circleci, buildkite, jenkins, argo_workflows, aws_pipeline, travis
  Messaging:    slack, pagerduty, opsgenie
  Monitoring:   datadog, sentry, grafana_oncall, honeycomb, signoz, appdynamics,
                splunk, elastic_apm, jaeger, zipkin, tempo
  Storage:      mongodb_stats, mysql_stats, cassandra_stats, clickhouse_stats
  Cloud:        cloudflare, gcp_asset_inventory, azure_resource_graph, aws_config_trail
  Other:        mqtt, victoriametrics, opentsdb, thanos, graphite, influxdb
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

# ============================================================================
# Test helpers — shared mock transports
# ============================================================================


@dataclass
class MockHttpTransport:
    """Injectable HTTP transport returning canned (status, body) tuples.

    Records every call in ``.calls`` for later assertion.
    """

    responses: dict[str, tuple[int, object]] = field(default_factory=dict)
    default_status: int = 200
    default_body: object = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def __call__(
        self,
        method_or_url: str,
        url_or_headers: str | dict[str, str] | None = None,
        *,
        params: dict[str, object] | None = None,
        json: object = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: object,
    ) -> tuple[int, object]:
        self.calls.append(
            {
                "method": method_or_url,
                "url": url_or_headers if isinstance(url_or_headers, str) else method_or_url,
                "params": params,
                "json": json,
                "headers": headers or (url_or_headers if isinstance(url_or_headers, dict) else None),
                "timeout": timeout,
            }
        )
        return self.responses.get(
            str(url_or_headers) if isinstance(url_or_headers, str) else "default",
            (self.default_status, self.default_body),
        )


class MockMongoExecutor:
    """Injectable MongoDB admin-command executor."""

    def __init__(self, overrides: dict[str, dict[str, object]] | None = None) -> None:
        self._overrides = overrides or {}
        self.calls: list[str] = []

    def __call__(self, command: str) -> dict[str, object]:
        self.calls.append(command)
        return self._overrides.get(command, {})


class MockDbCursor:
    """Injectable database cursor for SQL-based connectors."""

    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []
        self._idx = 0
        self.calls: list[str] = []

    def execute(self, sql: str) -> None:
        self.calls.append(sql)

    def __iter__(self) -> MockDbCursor:
        return self

    def __next__(self) -> dict[str, object]:
        if self._idx >= len(self._rows):
            raise StopIteration
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def close(self) -> None:
        pass


# ============================================================================
# CI/CD connectors
# ============================================================================


class TestCircleCiConnector:
    def test_config_requires_project_slug(self):
        from general_ludd.connectors.circleci import CircleCiSource

        with pytest.raises(ValueError, match="project_slug"):
            CircleCiSource({}, http_get=lambda u, h: (200, []))

    def test_health_ok(self):
        from general_ludd.connectors.circleci import CircleCiSource

        transport = MockHttpTransport(default_status=200, default_body={"items": [{"id": "1"}]})
        src = CircleCiSource({"project_slug": "gh/owner/repo"}, http_get=transport.__call__)  # type: ignore[arg-type]
        result = src.health()
        assert result["ok"] is True

    def test_health_fails_on_error(self):
        from general_ludd.connectors.circleci import CircleCiSource

        transport = MockHttpTransport(default_status=500, default_body={})
        src = CircleCiSource({"project_slug": "gh/owner/repo"}, http_get=transport.__call__)  # type: ignore[arg-type]
        result = src.health()
        assert result["ok"] is False

    def test_query_returns_records(self):
        from general_ludd.connectors.circleci import CircleCiSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "items": [
                    {
                        "id": "abc",
                        "number": 42,
                        "state": "passed",
                        "created_at": "2025-01-01T00:00:00Z",
                        "vcs": {"revision": "deadbeef", "branch": "main"},
                    }
                ]
            },
        )
        src = CircleCiSource({"project_slug": "gh/owner/repo"}, http_get=transport.__call__)  # type: ignore[arg-type]
        records = src.query({})
        assert len(records) >= 1
        assert records[0]["level_or_status"] == "passed"

    def test_query_empty_on_transport_error(self):
        from general_ludd.connectors.circleci import CircleCiSource

        def _fail(_u: str, _h: dict[str, str]) -> tuple[int, object]:
            raise OSError("network down")

        src = CircleCiSource({"project_slug": "gh/owner/repo"}, http_get=_fail)
        records = src.query({})
        assert records == []


class TestBuildkiteConnector:
    def test_config_defaults(self):
        from general_ludd.connectors.buildkite import BuildkiteSource

        src = BuildkiteSource({"organization": "myorg"}, http_get=lambda u, h: (200, []))
        assert src.name is not None

    def test_health_ok(self):
        from general_ludd.connectors.buildkite import BuildkiteSource

        transport = MockHttpTransport(default_status=200, default_body=[])
        src = BuildkiteSource({"organization": "myorg"}, http_get=transport.__call__)  # type: ignore[arg-type]
        result = src.health()
        assert isinstance(result, dict)

    def test_health_fails(self):
        from general_ludd.connectors.buildkite import BuildkiteSource

        transport = MockHttpTransport(default_status=403, default_body={})
        src = BuildkiteSource({"organization": "myorg"}, http_get=transport.__call__)  # type: ignore[arg-type]
        result = src.health()
        assert result["ok"] is False

    def test_query_returns_records(self):
        from general_ludd.connectors.buildkite import BuildkiteSource

        transport = MockHttpTransport(
            default_status=200,
            default_body=[
                {
                    "id": "b1",
                    "number": 99,
                    "state": "passed",
                    "branch": "main",
                    "commit": "abc123",
                    "created_at": "2025-01-01T00:00:00Z",
                }
            ],
        )
        src = BuildkiteSource({"organization": "myorg"}, http_get=transport.__call__)  # type: ignore[arg-type]
        records = src.query({})
        assert len(records) >= 1


class TestJenkinsConnector:
    def test_config_requires_base_url(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        with pytest.raises(ValueError, match="base_url"):
            JenkinsSource({})

    def test_ssrf_rejects_loopback(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        with pytest.raises(ValueError, match="SSRF"):
            JenkinsSource({"base_url": "http://127.0.0.1:8080/"})

    def test_health_ok(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        transport = MockHttpTransport(default_status=200, default_body={"jobs": []})
        src = JenkinsSource({"base_url": "https://jenkins.example.com/"}, http_get=transport.__call__)  # type: ignore[arg-type]
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.jenkins import JenkinsSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "jobs": [
                    {
                        "name": "build-1",
                        "url": "https://jenkins.example.com/job/1",
                        "color": "blue",
                        "lastBuild": {"number": 1},
                    }
                ]
            },
        )
        src = JenkinsSource({"base_url": "https://jenkins.example.com/"}, http_get=transport.__call__)  # type: ignore[arg-type]
        records = src.query({})
        assert isinstance(records, list)
        assert len(records) >= 1


class TestArgoWorkflowsConnector:
    def test_health_ok(self):
        from general_ludd.connectors.argo_workflows import ArgoWorkflowsSource

        transport = MockHttpTransport(default_status=200, default_body={"items": []})
        src = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com", "namespace": "argo"},
            http_get=transport.__call__,  # type: ignore[arg-type]
        )
        result = src.health()
        assert result["ok"] is True
        assert transport.calls[0]["url"] == "https://argo.example.com/api/v1/workflows/argo"

    def test_query_returns_records(self):
        from general_ludd.connectors.argo_workflows import ArgoWorkflowsSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "items": [
                    {
                        "metadata": {"name": "wf-1", "namespace": "argo"},
                        "status": {"phase": "Succeeded", "startedAt": "2025-01-01T00:00:00Z"},
                    }
                ]
            },
        )
        src = ArgoWorkflowsSource(
            {"base_url": "https://argo.example.com", "namespace": "argo"},
            http_get=transport.__call__,  # type: ignore[arg-type]
        )
        records = src.query({})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "Succeeded"


class TestAwsPipelineConnector:
    def test_config_defaults(self):
        from general_ludd.connectors.aws_pipeline import AwsPipelineSource

        def _client(_m, **_kw):  # type: ignore[no-untyped-def]
            return (200, {"pipelineExecutionSummaries": []})

        src = AwsPipelineSource({"name": "mypipe"}, aws_client=_client)
        assert src.name is not None

    def test_health_ok(self):
        from general_ludd.connectors.aws_pipeline import AwsPipelineSource

        def _client(_m, **_kw):  # type: ignore[no-untyped-def]
            return (200, {"pipelineExecutionSummaries": []})

        src = AwsPipelineSource({"name": "mypipe"}, aws_client=_client)
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.aws_pipeline import AwsPipelineSource

        def _client(_m, **_kw):  # type: ignore[no-untyped-def]
            return (
                200,
                {
                    "pipelineExecutionSummaries": [
                        {
                            "pipelineExecutionId": "exec-1",
                            "status": "Succeeded",
                            "startTime": 1700000000.0,
                        }
                    ]
                },
            )

        src = AwsPipelineSource({"name": "mypipe"}, aws_client=_client)
        records = src.query({})
        assert isinstance(records, list)
        assert len(records) >= 1


class TestAzureDevOpsConnector:
    def test_ssrf_rejects_loopback(self):
        from general_ludd.connectors.azure_devops import AzureDevOpsSource

        with pytest.raises((ValueError, RuntimeError)):
            AzureDevOpsSource({"organization": "myorg", "base_url": "http://10.0.0.1/"})

    def test_health_ok(self):
        from general_ludd.connectors.azure_devops import AzureDevOpsSource

        transport = MockHttpTransport(default_status=200, default_body={"value": []})
        src = AzureDevOpsSource({"organization": "myorg", "project": "myproj"}, http_get=transport.__call__)  # type: ignore[arg-type]
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.azure_devops import AzureDevOpsSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "value": [
                    {
                        "id": 1,
                        "buildNumber": "2025.1",
                        "status": "completed",
                        "result": "succeeded",
                        "queueTime": "2025-01-01T00:00:00Z",
                    }
                ]
            },
        )
        src = AzureDevOpsSource({"organization": "myorg", "project": "myproj"}, http_get=transport.__call__)  # type: ignore[arg-type]
        records = src.query({})
        assert isinstance(records, list)
        assert len(records) >= 1


class TestTravisConnector:
    def test_config_requires_token_env(self):
        from general_ludd.connectors.travis import TravisSource

        src = TravisSource({"repository": "owner/repo"}, http_get=lambda u, h: (200, {"builds": []}))
        assert src.name is not None

    def test_health_ok(self):
        from general_ludd.connectors.travis import TravisSource

        transport = MockHttpTransport(default_status=200, default_body={"builds": []})
        src = TravisSource({"repository": "owner/repo"}, http_get=transport.__call__)  # type: ignore[arg-type]
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.travis import TravisSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "builds": [
                    {
                        "id": 99,
                        "number": "99",
                        "state": "passed",
                        "branch": {"name": "main"},
                        "commit": {"sha": "abc123"},
                        "started_at": "2025-01-01T00:00:00Z",
                    }
                ]
            },
        )
        src = TravisSource({"repository": "owner/repo"}, http_get=transport.__call__)  # type: ignore[arg-type]
        records = src.query({})
        assert isinstance(records, list)


# ============================================================================
# Messaging / notification connectors
# ============================================================================


class TestSlackConnector:
    def test_config_requires_base_url_and_token_env(self):
        from general_ludd.connectors.slack import HttpTransport

        with pytest.raises(ValueError, match="base_url"):
            from general_ludd.connectors.slack import SlackSource

            SlackSource({}, transport=cast(HttpTransport, object()))

    def test_health_returns_dict(self, monkeypatch):
        from general_ludd.connectors.slack import SlackSource

        transport = MockHttpTransport(default_status=200, default_body={"ok": True})
        monkeypatch.setenv("SLACK_TEST_TOKEN", "xoxb-test")
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TEST_TOKEN"},
            transport=cast(Any, transport),
        )
        result = src.health()
        assert result["ok"] is True
        assert "ok" in result

    def test_send_notification_webhook_ok(self, monkeypatch):
        from general_ludd.connectors.slack import SlackSource

        transport = MockHttpTransport(default_status=200, default_body="ok")
        monkeypatch.setenv("SLACK_TEST_TOKEN", "xoxb-test")
        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_TEST_TOKEN",
                "webhook_url": "https://hooks.slack.com/services/TEST",
            },
            transport=cast(Any, transport),
        )
        result = src.send_notification("hello")
        assert result["ok"] is True

    def test_send_notification_fail_soft(self, monkeypatch):
        from general_ludd.connectors.slack import SlackSource

        transport = MockHttpTransport(default_status=500, default_body={})
        monkeypatch.setenv("SLACK_TEST_TOKEN", "xoxb-test")
        src = SlackSource(
            {
                "base_url": "https://slack.com/api",
                "token_env": "SLACK_TEST_TOKEN",
                "webhook_url": "https://hooks.slack.com/services/TEST",
            },
            transport=cast(Any, transport),
        )
        result = src.send_notification("hello")
        assert result["ok"] is False

    def test_read_channel_history_requires_channel_id(self, monkeypatch):
        from general_ludd.connectors.slack import SlackSource

        monkeypatch.setenv("SLACK_TEST_TOKEN", "xoxb-test")
        src = SlackSource(
            {"base_url": "https://slack.com/api", "token_env": "SLACK_TEST_TOKEN"},
            transport=cast(Any, MockHttpTransport()),
        )
        with pytest.raises(ValueError, match="channel_id"):
            src.read_channel_history()


class TestPagerDutyConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.pagerduty import PagerDutySource

        transport = MockHttpTransport(default_status=200, default_body={"incidents": []})
        src = PagerDutySource(
            {"token_env": "PD_TOKEN"},
            transport=cast(Any, transport),
        )
        monkeypatch.setenv("PD_TOKEN", "test-token")
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.pagerduty import PagerDutySource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "incidents": [
                    {
                        "id": "I123",
                        "title": "Disk full",
                        "status": "triggered",
                        "urgency": "high",
                        "created_at": "2025-01-01T00:00:00Z",
                        "service": {"id": "S1", "summary": "prod"},
                        "assignments": [{"assignee": {"summary": "alice"}}],
                    }
                ]
            },
        )
        monkeypatch.setenv("PD_TOKEN", "test-token")
        src = PagerDutySource(
            {"token_env": "PD_TOKEN"},
            transport=cast(Any, transport),
        )
        records = src.query({})
        assert isinstance(records, list)
        assert len(records) >= 1

    def test_ssrf_rejects_loopback(self):
        from general_ludd.connectors.pagerduty import PagerDutySource
        from general_ludd.security.ssrf import SSRFError

        with pytest.raises((SSRFError, ValueError)):
            PagerDutySource(
                {"token_env": "PD_TOKEN", "base_url": "http://127.0.0.1/"},
                transport=cast(Any, MockHttpTransport()),
            )


class TestOpsgenieConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.opsgenie import OpsgenieSource

        transport = MockHttpTransport(default_status=200, default_body={"data": []})
        monkeypatch.setenv("OPSGENIE_TOKEN", "test-key")
        src = OpsgenieSource({"token_env": "OPSGENIE_TOKEN"}, transport=cast(Any, transport))
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.opsgenie import OpsgenieSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "data": [
                    {
                        "id": "alert-1",
                        "message": "CPU high",
                        "status": "open",
                        "priority": "P1",
                        "createdAt": "2025-01-01T00:00:00Z",
                    }
                ]
            },
        )
        monkeypatch.setenv("OPSGENIE_TOKEN", "test-key")
        src = OpsgenieSource({"token_env": "OPSGENIE_TOKEN"}, transport=cast(Any, transport))
        records = src.query({})
        assert isinstance(records, list)
        assert len(records) >= 1


# ============================================================================
# Monitoring / telemetry connectors
# ============================================================================


class TestDatadogConnector:
    def test_config_default_site(self):
        from general_ludd.connectors.datadog import DatadogSource

        transport = MockHttpTransport(default_status=200, default_body={"data": []})
        src = DatadogSource({}, http_request=transport)
        assert src.name is not None

    def test_health_ok(self):
        from general_ludd.connectors.datadog import DatadogSource

        transport = MockHttpTransport(default_status=200, default_body={"valid": True})
        src = DatadogSource({}, http_request=transport)
        result = src.health()
        assert result["ok"] is True

    def test_health_fails(self):
        from general_ludd.connectors.datadog import DatadogSource

        transport = MockHttpTransport(default_status=403, default_body={"valid": False})
        src = DatadogSource({}, http_request=transport)
        result = src.health()
        assert result["ok"] is False

    def test_query_logs(self):
        from general_ludd.connectors.datadog import DatadogSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "data": [
                    {
                        "attributes": {
                            "timestamp": "2025-01-01T00:00:00Z",
                            "status": "info",
                            "message": "deploy started",
                            "service": "api",
                            "host": "i-123",
                            "tags": ["env:prod"],
                        }
                    }
                ]
            },
        )
        src = DatadogSource({}, http_request=transport)
        records = src.query({"mode": "logs"})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"

    def test_query_metrics(self):
        from general_ludd.connectors.datadog import DatadogSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={"series": [{"metric": "cpu.user", "scope": "*", "pointlist": [[1700000000, 42.5]]}]},
        )
        src = DatadogSource({}, http_request=transport)
        records = src.query({"mode": "metrics", "query": "cpu.user"})
        assert len(records) >= 1
        assert records[0]["kind"] == "metrics"


class TestSentryConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.sentry import SentrySource

        transport = MockHttpTransport(default_status=200, default_body=[{"id": "1"}])
        monkeypatch.setenv("SENTRY_TOKEN", "test-auth")
        src = SentrySource(
            {
                "token_env": "SENTRY_TOKEN",
                "org": "myorg",
                "project": "myproject",
            },
            transport=cast(Any, transport),
        )
        result = src.health()
        assert result["ok"] is True

    def test_query_issues(self, monkeypatch):
        from general_ludd.connectors.sentry import SentrySource

        transport = MockHttpTransport(
            default_status=200,
            default_body=[
                {
                    "id": "i1",
                    "title": "NullPointer",
                    "status": "unresolved",
                    "level": "error",
                    "firstSeen": "2025-01-01T00:00:00Z",
                    "project": {"name": "backend"},
                }
            ],
        )
        monkeypatch.setenv("SENTRY_TOKEN", "test-auth")
        src = SentrySource(
            {
                "token_env": "SENTRY_TOKEN",
                "org": "myorg",
                "project": "myproject",
            },
            transport=cast(Any, transport),
        )
        records = src.query({"mode": "issues"})
        assert isinstance(records, list)
        assert len(records) >= 1

    def test_ssrf_rejects_internal(self):
        from general_ludd.connectors.sentry import SentrySource

        with pytest.raises((ValueError, RuntimeError)):
            SentrySource(
                {"token_env": "SENTRY_TOKEN", "base_url": "http://169.254.169.254/"},
                transport=cast(Any, MockHttpTransport()),
            )


class TestGrafanaOnCallConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.grafana_oncall import GrafanaOnCallSource

        transport = MockHttpTransport(default_status=200, default_body={"results": []})
        monkeypatch.setenv("GRAFANA_ONCALL_TOKEN", "test-token")
        src = GrafanaOnCallSource(
            {
                "base_url": "https://8.8.8.8",
                "token_env": "GRAFANA_ONCALL_TOKEN",
            },
            transport=cast(Any, transport),
        )
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.grafana_oncall import GrafanaOnCallSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "results": [
                    {
                        "id": "a1",
                        "title": "ping failure",
                        "state": "alerting",
                        "severity": "critical",
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ]
            },
        )
        monkeypatch.setenv("GRAFANA_ONCALL_TOKEN", "test-token")
        src = GrafanaOnCallSource(
            {
                "base_url": "https://8.8.8.8",
                "token_env": "GRAFANA_ONCALL_TOKEN",
            },
            transport=cast(Any, transport),
        )
        records = src.query({})
        assert isinstance(records, list)
        assert len(records) >= 1


class TestHoneycombConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.honeycomb import HoneycombSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={"team": {"name": "test-team"}},
        )
        monkeypatch.setenv("HONEYCOMB_KEY", "test-key")
        src = HoneycombSource({"api_key_env": "HONEYCOMB_KEY", "dataset": "prod"}, transport=cast(Any, transport))
        result = src.health()
        assert result["ok"] is True

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.honeycomb import HoneycombSource

        transport = MockHttpTransport(
            responses={
                "https://api.honeycomb.io/1/queries/prod": (
                    201,
                    {"id": "query-1"},
                ),
                "https://api.honeycomb.io/1/query_results/prod": (
                    201,
                    {"id": "result-1", "complete": False},
                ),
                "https://api.honeycomb.io/1/query_results/prod/result-1": (
                    200,
                    {
                        "data": {
                            "results": [
                                {
                                    "time": "2025-01-01T00:00:00Z",
                                    "data": {
                                        "name": "checkout",
                                        "COUNT": 1,
                                    },
                                }
                            ]
                        }
                    },
                ),
            },
        )
        monkeypatch.setenv("HONEYCOMB_KEY", "test-key")
        src = HoneycombSource({"api_key_env": "HONEYCOMB_KEY", "dataset": "prod"}, transport=cast(Any, transport))
        records = src.query({})
        assert len(records) == 1
        assert records[0]["kind"] == "traces"
        assert records[0]["value"] == 1


class TestSigNozConnector:
    def test_health_ok(self):
        from general_ludd.connectors.signoz import SigNozSource

        transport = MockHttpTransport(default_status=200, default_body={"data": []})
        src = SigNozSource({"base_url": "https://signoz.example.com/"}, transport=cast(Any, transport))
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.signoz import SigNozSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "data": {
                    "result": [
                        {
                            "startTime": 1_735_689_600.0,
                            "spanId": "s1",
                            "traceId": "t1",
                            "serviceName": "checkout",
                            "name": "POST /pay",
                        }
                    ]
                }
            },
        )
        src = SigNozSource({"base_url": "https://signoz.example.com/"}, transport=cast(Any, transport))
        records = src.query({"mode": "traces"})
        assert len(records) == 1
        assert records[0]["labels"]["trace_id"] == "t1"


class TestAppDynamicsConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.appdynamics import AppDynamicsSource

        transport = MockHttpTransport(default_status=200, default_body={"items": []})
        monkeypatch.setenv("APPD_TOKEN", "test-token")
        src = AppDynamicsSource(
            {
                "token_env": "APPD_TOKEN",
                "base_url": "https://appd.example.com/",
                "application": "checkout",
            },
            transport=cast(Any, transport),
        )
        result = src.health()
        assert result["ok"] is True

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.appdynamics import AppDynamicsSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "data": [
                    {
                        "metricId": 1,
                        "metricName": "CPU",
                        "metricPath": "Application|CPU",
                        "metricValues": [
                            {
                                "startTimeInMillis": 1_735_689_600_000,
                                "value": 75.0,
                            }
                        ],
                    }
                ]
            },
        )
        monkeypatch.setenv("APPD_TOKEN", "test-token")
        src = AppDynamicsSource(
            {
                "token_env": "APPD_TOKEN",
                "base_url": "https://appd.example.com/",
                "application": "checkout",
            },
            transport=cast(Any, transport),
        )
        records = src.query({"metric_path": "Application|CPU"})
        assert len(records) == 1
        assert records[0]["kind"] == "metrics"
        assert records[0]["value"] == 75.0


class TestSplunkConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.splunk import SplunkSource

        transport = MockHttpTransport(default_status=200, default_body={"results": []})
        monkeypatch.setenv("SPLUNK_TOKEN", "test-token")
        src = SplunkSource(
            {
                "base_url": "https://splunk.example.com",
                "token_env": "SPLUNK_TOKEN",
            },
            transport=cast(Any, transport),
        )
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.splunk import SplunkSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "results": [
                    {
                        "_time": "2025-01-01T00:00:00Z",
                        "_raw": "error: disk full",
                        "sourcetype": "syslog",
                    }
                ]
            },
        )
        monkeypatch.setenv("SPLUNK_TOKEN", "test-token")
        src = SplunkSource(
            {
                "base_url": "https://splunk.example.com",
                "token_env": "SPLUNK_TOKEN",
            },
            transport=cast(Any, transport),
        )
        records = src.query({"search": "error"})
        assert isinstance(records, list)
        assert len(records) >= 1

    def test_ssrf_rejects_internal(self):
        from general_ludd.connectors.splunk import SplunkSource

        with pytest.raises((ValueError, RuntimeError)):
            SplunkSource(
                {"token_env": "SPLUNK_TOKEN", "base_url": "http://10.0.0.1/"},
                transport=cast(Any, MockHttpTransport()),
            )


class TestElasticApmConnector:
    def test_health_ok(self):
        from general_ludd.connectors.elastic_apm import ElasticApmSource

        transport = MockHttpTransport(default_status=200, default_body={"data": []})
        src = ElasticApmSource(
            {"base_url": "https://elastic.example.com/", "token_env": "ELASTIC_TOKEN"},
            transport=cast(Any, transport),
        )
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.elastic_apm import ElasticApmSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "hits": {
                    "hits": [
                        {
                            "_id": "tx1",
                            "_source": {
                                "@timestamp": "2025-01-01T00:00:00Z",
                                "transaction": {
                                    "id": "tx1",
                                    "name": "checkout",
                                    "duration": {"us": 250},
                                },
                                "event": {"outcome": "success"},
                            },
                        }
                    ]
                }
            },
        )
        src = ElasticApmSource(
            {"base_url": "https://elastic.example.com/", "token_env": "ELASTIC_TOKEN"},
            transport=cast(Any, transport),
        )
        records = src.query({})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "success"
        assert records[0]["value"] == 250.0


# ============================================================================
# Observability / tracing connectors
# ============================================================================


class TestJaegerConnector:
    def test_health_ok(self):
        from general_ludd.connectors.jaeger import JaegerSource

        transport = MockHttpTransport(default_status=200, default_body={"data": []})
        src = JaegerSource({"base_url": "https://jaeger.example.com/"}, transport=cast(Any, transport))
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.jaeger import JaegerSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "data": [
                    {
                        "traceID": "trace1",
                        "spans": [
                            {"spanID": "span1", "operationName": "get", "startTime": 1700000000000, "duration": 500000}
                        ],
                    }
                ]
            },
        )
        src = JaegerSource({"base_url": "https://jaeger.example.com/"}, transport=cast(Any, transport))
        records = src.query({})
        assert isinstance(records, list)


class TestZipkinConnector:
    def test_health_ok(self):
        from general_ludd.connectors.zipkin import ZipkinSource

        transport = MockHttpTransport(default_status=200, default_body=[])
        src = ZipkinSource({"base_url": "https://zipkin.example.com/"}, transport=cast(Any, transport))
        result = src.health()
        assert result["ok"] is True

    def test_query_returns_records(self):
        from general_ludd.connectors.zipkin import ZipkinSource

        transport = MockHttpTransport(
            default_status=200,
            default_body=[
                [
                    {
                        "traceId": "trace1",
                        "id": "span1",
                        "name": "get",
                        "timestamp": 1700000000000,
                        "duration": 250,
                        "kind": "SERVER",
                        "localEndpoint": {"serviceName": "checkout"},
                    }
                ]
            ],
        )
        src = ZipkinSource({"base_url": "https://zipkin.example.com/"}, transport=cast(Any, transport))
        records = src.query({})
        assert len(records) == 1
        assert records[0]["message"] == "get"
        assert records[0]["value"] == 250
        assert records[0]["labels"]["service"] == "checkout"


class TestTempoConnector:
    def test_health_ok(self):
        from general_ludd.connectors.tempo import TempoSource

        transport = MockHttpTransport(default_status=200, default_body={"traces": []})
        src = TempoSource({"base_url": "https://tempo.example.com/"}, transport=cast(Any, transport))
        result = src.health()
        assert result["ok"] is True

    def test_query_returns_records(self):
        from general_ludd.connectors.tempo import TempoSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "traces": [
                    {
                        "traceID": "t1",
                        "rootServiceName": "api",
                        "rootTraceName": "GET /orders",
                        "durationMs": 42,
                        "startTimeUnixNano": "1700000000000000000",
                    }
                ]
            },
        )
        src = TempoSource({"base_url": "https://tempo.example.com/"}, transport=cast(Any, transport))
        records = src.query({})
        assert len(records) == 1
        assert records[0]["message"] == "GET /orders"
        assert records[0]["value"] == 42
        assert records[0]["labels"]["service"] == "api"


# ============================================================================
# Database / storage connectors
# ============================================================================


class TestMongodbStatsConnector:
    def test_health_ok(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        executor = MockMongoExecutor({"serverStatus": {"connections": {"current": 5}}})
        src = MongoDbStatsSource(executor=executor)
        result = src.health()
        assert result["ok"] is True

    def test_health_fails_on_missing_driver(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        # No executor, no env URI => driver unavailable
        src = MongoDbStatsSource()
        result = src.health()
        assert result["ok"] is False

    def test_query_returns_connection_metrics(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        executor = MockMongoExecutor(
            {
                "serverStatus": {"connections": {"current": 10, "available": 50000}},
                "currentOp": {"inprog": [{"op": "query"}]},
            }
        )
        src = MongoDbStatsSource(executor=executor)
        records = src.query()
        assert len(records) >= 1
        assert any("connections" in str(r.get("message")) for r in records)

    def test_query_empty_on_executor_none(self):
        from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

        src = MongoDbStatsSource()
        records = src.query()
        assert records == []


class TestMysqlStatsConnector:
    def test_health_ok(self):
        from general_ludd.connectors.mysql_stats import MysqlStatsSource

        cursor = MockDbCursor([{"Variable_name": "Uptime", "Value": "3600"}])
        src = MysqlStatsSource(cursor=cursor)  # type: ignore[arg-type]
        result = src.health()
        assert result["ok"] is True

    def test_query_returns_records(self):
        from general_ludd.connectors.mysql_stats import MysqlStatsSource

        cursor = MockDbCursor(
            [
                {"Variable_name": "Threads_connected", "Value": "4"},
                {"Variable_name": "Bytes_received", "Value": "1024"},
            ]
        )
        src = MysqlStatsSource(cursor=cursor)  # type: ignore[arg-type]
        records = src.query()
        assert len(records) == 2
        assert records[0]["message"] == "global status Threads_connected"
        assert records[0]["value"] == 4.0
        assert records[1]["value"] == 1024.0


class TestCassandraStatsConnector:
    def test_config_defaults(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        cursor = MockDbCursor([])
        src = CassandraStatsSource(cursor=cursor)  # type: ignore[arg-type]
        assert src.name is not None

    def test_health_ok(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        cursor = MockDbCursor([])
        src = CassandraStatsSource(cursor=cursor)  # type: ignore[arg-type]
        result = src.health()
        assert result["ok"] is True

    def test_query_returns_records(self):
        from general_ludd.connectors.cassandra_stats import CassandraStatsSource

        cursor = MockDbCursor(
            [
                {"metric": "ReadLatency", "value": 1.5},
                {"metric": "WriteLatency", "value": 2.0},
            ]
        )
        src = CassandraStatsSource(cursor=cursor)  # type: ignore[arg-type]
        records = src.query()
        assert len(records) == 2
        assert records[0]["message"] == "ReadLatency"
        assert records[0]["value"] == 1.5
        assert records[0]["labels"]["command"] == "compactionstats"


class TestClickhouseStatsConnector:
    def test_health_ok(self):
        from general_ludd.connectors.clickhouse_stats import ClickHouseStatsSource

        cursor = MockDbCursor([{"metric": "Query", "value": "42"}])
        src = ClickHouseStatsSource(cursor=cursor)  # type: ignore[arg-type]
        result = src.health()
        assert result["ok"] is True

    def test_query_returns_records(self):
        from general_ludd.connectors.clickhouse_stats import ClickHouseStatsSource

        cursor = MockDbCursor(
            [
                {"metric": "Query", "value": "100"},
                {"metric": "Merge", "value": "20"},
            ]
        )
        src = ClickHouseStatsSource(cursor=cursor)  # type: ignore[arg-type]
        records = src.query()
        assert len(records) == 2
        assert records[0]["message"] == "Query"
        assert records[0]["value"] == 100
        assert records[1]["value"] == 20


# ============================================================================
# Cloud-specific connectors
# ============================================================================


class TestCloudflareConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.cloudflare import CloudflareSource

        transport = MockHttpTransport(default_status=200, default_body={"success": True, "result": []})
        monkeypatch.setenv("CF_TOKEN", "test-token")
        src = CloudflareSource(
            {"account_id": "abc123", "token_env": "CF_TOKEN"},
            transport=cast(Any, transport),
        )
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.cloudflare import CloudflareSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "success": True,
                "result": [
                    {
                        "id": "log-1",
                        "action": {"type": "login", "result": "success"},
                        "actor": {"email": "user@example.com"},
                        "resource": {"type": "account"},
                        "when": "2025-01-01T00:00:00Z",
                    }
                ],
            },
        )
        monkeypatch.setenv("CF_TOKEN", "test-token")
        src = CloudflareSource(
            {"account_id": "abc123", "token_env": "CF_TOKEN"},
            transport=cast(Any, transport),
        )
        records = src.query({})
        assert len(records) == 1
        assert records[0]["message"] == "login"
        assert records[0]["level_or_status"] == "success"
        assert records[0]["labels"]["actor_email"] == "user@example.com"

    def test_ssrf_rejects_internal(self, monkeypatch):
        from general_ludd.connectors.cloudflare import CloudflareSource

        monkeypatch.setenv("CF_TOKEN", "test-token")
        with pytest.raises((ValueError, RuntimeError)):
            CloudflareSource(
                {"account_id": "abc123", "token_env": "CF_TOKEN", "base_url": "http://10.0.0.1/"},
                transport=cast(Any, MockHttpTransport()),
            )


class TestGcpAssetInventoryConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.gcp_asset_inventory import GcpAssetInventorySource

        transport = MockHttpTransport(default_status=200, default_body={"results": []})
        monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", "test-token")
        src = GcpAssetInventorySource(
            {"project_id": "my-project"},
            transport=cast(Any, transport),
        )
        result = src.health()
        assert result["ok"] is True

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.gcp_asset_inventory import GcpAssetInventorySource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "results": [
                    {
                        "name": "//compute/instance-1",
                        "assetType": "compute.googleapis.com/Instance",
                        "project": "projects/my-project",
                        "location": "us-central1-a",
                        "state": "RUNNING",
                        "updateTime": "2025-01-01T00:00:00Z",
                    }
                ]
            },
        )
        monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", "test-token")
        src = GcpAssetInventorySource(
            {"project_id": "my-project"},
            transport=cast(Any, transport),
        )
        records = src.query({})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "RUNNING"
        assert records[0]["labels"]["project"] == "projects/my-project"
        assert records[0]["labels"]["location"] == "us-central1-a"


class TestAzureResourceGraphConnector:
    def test_ssrf_rejects_loopback(self):
        from general_ludd.connectors.azure_resource_graph import AzureResourceGraphSource

        with pytest.raises((ValueError, RuntimeError)):
            AzureResourceGraphSource({"base_url": "http://127.0.0.1/"})

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.azure_resource_graph import AzureResourceGraphSource

        transport = MockHttpTransport(default_status=200, default_body={"data": []})
        monkeypatch.setenv("AZURE_GRAPH_TOKEN", "test-token")
        src = AzureResourceGraphSource(
            {"subscription_id": "sub-1", "token_env": "AZURE_GRAPH_TOKEN"},
            http_get=transport.__call__,
        )
        result = src.health()
        assert result["ok"] is True

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.azure_resource_graph import AzureResourceGraphSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "data": [
                    {
                        "id": "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
                        "name": "vm1",
                        "type": "microsoft.compute/virtualmachines",
                        "resourceGroup": "rg",
                        "location": "eastus",
                        "properties": {"provisioningState": "Succeeded"},
                    }
                ]
            },
        )
        monkeypatch.setenv("AZURE_GRAPH_TOKEN", "test-token")
        src = AzureResourceGraphSource(
            {"subscription_id": "sub-1", "token_env": "AZURE_GRAPH_TOKEN"},
            http_get=transport.__call__,
        )
        records = src.query({"query": "Resources"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "Succeeded"
        assert records[0]["labels"]["resourceGroup"] == "rg"
        assert records[0]["labels"]["location"] == "eastus"


class TestAwsConfigTrailConnector:
    def test_config_defaults(self):
        from general_ludd.connectors.aws_config_trail import AwsConfigTrailSource

        def _client(_m, **_kw):  # type: ignore[no-untyped-def]
            return (200, {})

        src = AwsConfigTrailSource({"region": "us-east-1"}, aws_client=_client)
        assert src.name is not None

    def test_health_ok(self):
        from general_ludd.connectors.aws_config_trail import AwsConfigTrailSource

        def _client(_m, **_kw):  # type: ignore[no-untyped-def]
            return (200, {})

        src = AwsConfigTrailSource({"region": "us-east-1"}, aws_client=_client)
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.aws_config_trail import AwsConfigTrailSource

        def _client(_m, **_kw):  # type: ignore[no-untyped-def]
            return (
                200,
                {
                    "Events": [
                        {
                            "EventId": "evt-1",
                            "EventName": "RunInstances",
                            "EventTime": "2025-01-01T00:00:00Z",
                        }
                    ]
                },
            )

        src = AwsConfigTrailSource({"region": "us-east-1"}, aws_client=_client)
        records = src.query({})
        assert isinstance(records, list)
        assert len(records) >= 1


# ============================================================================
# Other connectors — MQTT, time-series DBs
# ============================================================================


class TestMqttConnector:
    def test_config_requires_broker_host(self):
        from general_ludd.connectors.mqtt import MqttSource

        with pytest.raises(ValueError, match="broker_host"):
            MqttSource({})

    def test_ssrf_rejects_loopback(self):
        from general_ludd.connectors.mqtt import MqttBrokerBlockedError, MqttSource

        with pytest.raises((MqttBrokerBlockedError, ValueError)):
            MqttSource({"broker_host": "127.0.0.1"})

    def test_health_reports_buffer_size(self):
        from general_ludd.connectors.mqtt import MqttSource

        src = MqttSource({"broker_host": "mqtt.example.com"})
        result = src.health()
        assert result["ok"] is True
        assert "size" in result
        assert "capacity" in result

    def test_push_and_query(self):
        from general_ludd.connectors.mqtt import MqttSource

        src = MqttSource({"broker_host": "mqtt.example.com", "maxlen": 10})
        src.push_message("sensors/temp", b"23.5")
        src.push_message("sensors/humidity", b"60")
        records = src.query({})
        assert len(records) == 2

    def test_query_filtered_by_topic(self):
        from general_ludd.connectors.mqtt import MqttSource

        src = MqttSource({"broker_host": "mqtt.example.com", "maxlen": 10})
        src.push_message("sensors/temp", b"23.5")
        src.push_message("sensors/humidity", b"60")
        records = src.query({"topic": "sensors/temp"})
        assert len(records) == 1
        assert records[0]["labels"]["topic"] == "sensors/temp"

    def test_query_filtered_by_since(self):
        from general_ludd.connectors.mqtt import MqttSource

        src = MqttSource({"broker_host": "mqtt.example.com", "maxlen": 10})
        src.push_message("sensors/temp", b"23.5")
        time.sleep(0.01)
        cutoff = time.time()
        src.push_message("sensors/humidity", b"60")
        records = src.query({"since": cutoff})
        assert len(records) == 1

    def test_query_filtered_by_kind(self):
        from general_ludd.connectors.mqtt import MqttSource

        src = MqttSource({"broker_host": "mqtt.example.com", "kind": "logs", "maxlen": 10})
        src.push_message("topic/a", b"data")
        records = src.query({"kind": "logs"})
        assert len(records) == 1
        records_other = src.query({"kind": "metrics"})
        assert len(records_other) == 0

    def test_empty_query_on_no_data(self):
        from general_ludd.connectors.mqtt import MqttSource

        src = MqttSource({"broker_host": "mqtt.example.com"})
        records = src.query({})
        assert records == []

    def test_maxlen_respects_bound(self):
        from general_ludd.connectors.mqtt import MqttSource

        src = MqttSource({"broker_host": "mqtt.example.com", "maxlen": 3})
        for i in range(5):
            src.push_message(f"topic/{i}", str(i).encode())
        records = src.query({})
        assert len(records) == 3

    def test_maxlen_must_be_positive(self):
        from general_ludd.connectors.mqtt import MqttSource

        with pytest.raises(ValueError, match="maxlen"):
            MqttSource({"broker_host": "mqtt.example.com", "maxlen": 0})


class TestVictoriaMetricsConnector:
    def test_health_ok(self):
        from general_ludd.connectors.victoriametrics import VictoriaMetricsSource

        transport = MockHttpTransport(default_status=200, default_body={"data": {"result": []}})
        src = VictoriaMetricsSource({"base_url": "https://vm.example.com/"}, transport=cast(Any, transport))
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.victoriametrics import VictoriaMetricsSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "data": {
                    "result": [
                        {
                            "metric": {"__name__": "cpu_usage"},
                            "values": [[1700000000, "42.5"], [1700000060, "43.1"]],
                        }
                    ]
                }
            },
        )
        src = VictoriaMetricsSource({"base_url": "https://vm.example.com/"}, transport=cast(Any, transport))
        records = src.query({"query": "cpu_usage"})
        assert isinstance(records, list)
        assert len(records) >= 2


class TestOpenTsdbConnector:
    def test_health_ok(self):
        from general_ludd.connectors.opentsdb import OpenTsdbSource

        transport = MockHttpTransport(default_status=200, default_body=[])
        src = OpenTsdbSource({"base_url": "https://opentsdb.example.com/"}, transport=cast(Any, transport))
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.opentsdb import OpenTsdbSource

        transport = MockHttpTransport(
            default_status=200,
            default_body=[
                {
                    "metric": "sys.cpu.user",
                    "tags": {"host": "server1"},
                    "aggregateTags": ["host"],
                    "dps": {"1700000000": 42.0},
                }
            ],
        )
        src = OpenTsdbSource({"base_url": "https://opentsdb.example.com/"}, transport=cast(Any, transport))
        records = src.query({"query": "sys.cpu"})
        assert isinstance(records, list)
        assert len(records) >= 1


class TestInfluxDBConnector:
    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.influxdb import InfluxDBSource

        transport = MockHttpTransport(default_status=200, default_body={"data": []})
        src = InfluxDBSource(
            {"base_url": "https://influx.example.com/", "token_env": "INFLUX_TOKEN"},
            transport=cast(Any, transport),
        )
        monkeypatch.setenv("INFLUX_TOKEN", "test-token")
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.influxdb import InfluxDBSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "data": [
                    {
                        "_time": "2025-01-01T00:00:00Z",
                        "_field": "cpu",
                        "_value": 75.0,
                    }
                ]
            },
        )
        monkeypatch.setenv("INFLUX_TOKEN", "test-token")
        src = InfluxDBSource(
            {"base_url": "https://influx.example.com/", "token_env": "INFLUX_TOKEN"},
            transport=cast(Any, transport),
        )
        records = src.query({"query": "from(bucket: \"monitoring\")"})
        assert isinstance(records, list)
        assert len(records) >= 1


class TestGraphiteConnector:
    def test_health_ok(self):
        from general_ludd.connectors.graphite import GraphiteSource

        transport = MockHttpTransport(default_status=200, default_body=[{"target": "cpu", "datapoints": []}])
        src = GraphiteSource({"base_url": "https://graphite.example.com/"}, transport=cast(Any, transport))
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.graphite import GraphiteSource

        transport = MockHttpTransport(
            default_status=200,
            default_body=[
                {
                    "target": "servers.server1.cpu.user",
                    "datapoints": [[42.5, 1700000000], [43.1, 1700000060]],
                }
            ],
        )
        src = GraphiteSource({"base_url": "https://graphite.example.com/"}, transport=cast(Any, transport))
        records = src.query({"query": "cpu"})
        assert isinstance(records, list)
        assert len(records) >= 2


class TestThanosConnector:
    def test_health_ok(self):
        from general_ludd.connectors.thanos import ThanosSource

        transport = MockHttpTransport(default_status=200, default_body={"data": {"result": []}})
        src = ThanosSource({"base_url": "https://thanos.example.com/"}, transport=cast(Any, transport))
        result = src.health()
        assert isinstance(result, dict)

    def test_query_returns_records(self):
        from general_ludd.connectors.thanos import ThanosSource

        transport = MockHttpTransport(
            default_status=200,
            default_body={
                "data": {
                    "result": [
                        {
                            "metric": {"__name__": "up", "job": "node"},
                            "value": [1700000000, "1"],
                        }
                    ]
                }
            },
        )
        src = ThanosSource({"base_url": "https://thanos.example.com/"}, transport=cast(Any, transport))
        records = src.query({"query": "up"})
        assert isinstance(records, list)
        assert len(records) >= 1
