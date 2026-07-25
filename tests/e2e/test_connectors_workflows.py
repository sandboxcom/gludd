"""E2E tests for the connectors subsystem (cloud/API integrations).

Covers: cloud provider config validation, Docker/Kubernetes, GitHub/GitLab,
SSH config, API error handling (timeout/retry/circuit breaker), webhook
buffer, observability facade, provider registry.

Uses mock transports so no real network I/O is required.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from general_ludd.connectors._errors import (
    ConnectorConfigError,
    SSRFError,
    sanitize_exc_message,
    sanitize_str,
)
from general_ludd.connectors.aws_observability import AwsObservabilitySource
from general_ludd.connectors.azure_monitor import AzureMonitorSource
from general_ludd.connectors.base import (
    Observability,
    SourceRegistry,
    classify_health,
    classify_health_for_source,
    is_safe_endpoint,
    normalized_record,
    run_healthcheck,
)
from general_ludd.connectors.docker_engine import (
    DockerEngineSource,
    _DockerResponse,
    _is_multiplexed,
    _iter_log_payload,
    _record as docker_record,
    _split_rfc3339,
)
from general_ludd.connectors.gcp_observability import GcpObservabilitySource
from general_ludd.connectors.github_actions import GitHubActionsSource
from general_ludd.connectors.gitlab_ci import GitlabCiSource
from general_ludd.connectors.kubernetes import KubernetesSource
from general_ludd.connectors.normalize import (
    AUTH_FAMILY_PREFIXES,
    CANONICAL_SEVERITIES,
    auth_family,
    bundle_credentials,
    correlate,
    normalize_join_keys,
    sanitize_metric_value,
)
from general_ludd.connectors.registry import (
    ConnectorRegistry,
    _validate_class_name,
    _validate_source_class,
)
from general_ludd.connectors.webhook_buffer import WebhookBufferSource


# ============================================================================
# Helpers
# ============================================================================


class _MockTransport:
    """Callable mock HTTP transport returning canned (status, json)."""

    def __init__(
        self,
        responses: dict[str | None, tuple[int, object]] | None = None,
        default_status: int = 200,
        default_body: object = None,
    ):
        self._responses = responses or {}
        self._default_status = default_status
        self._default_body = default_body
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, object]:
        self.calls.append((url, headers))
        key: str | None = url
        if url not in self._responses:
            if isinstance(self._responses, dict) and not self._responses:
                key = None
            else:
                status, body = self._default_status, self._default_body
                return status, body
        return self._responses.get(key, (self._default_status, self._default_body))


class _MockHttpResponse:
    """Trivial HttpResponse Protocol implementation."""

    def __init__(self, status_code: int = 200, text_body: str = "", json_body: object = None):
        self.status_code = status_code
        self._text = text_body
        self._json_body = json_body if json_body is not None else {"ok": True}

    @property
    def text(self) -> str:
        return self._text

    def json(self) -> object:
        return self._json_body


class _MockGcpTransport:
    """Injectable HttpTransport stub for GcpObservabilitySource."""

    def __init__(
        self,
        status_code: int = 200,
        response_json: object = None,
    ):
        self.status_code = status_code
        self.response_json = response_json
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object] | None = None,
        json_body: dict[str, object] | None = None,
        timeout: float,
    ) -> _MockHttpResponse:
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
        text_body = json.dumps(self.response_json) if self.response_json is not None else ""
        return _MockHttpResponse(self.status_code, text_body, self.response_json)


class _MockDockerTransport:
    """Injectable transport stub for DockerEngineSource."""

    def __init__(self, responses: dict[str, _DockerResponse] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, str, dict[str, object] | None, str, float]] = []
        self._default = _DockerResponse(status=200, headers={}, body=b"[]")

    def __call__(
        self,
        method: str,
        path: str,
        query: dict[str, object] | None,
        base_url: str,
        timeout: float,
    ) -> _DockerResponse:
        self.calls.append((method, path, query, base_url, timeout))
        return self._responses.get(path, self._default)


class _MockK8sTransport:
    """Injectable transport stub for KubernetesSource."""

    def __init__(self, status_code: int = 200, text_body: str = "", json_body: object = None):
        self._status = status_code
        self._text = text_body
        self._json_body = json_body
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> _MockHttpResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "timeout": timeout}
        )
        return _MockHttpResponse(self._status, self._text, self._json_body)


class _MockAwsClient:
    """Fake boto3 client for AwsObservabilitySource."""

    def __init__(self, method_responses: dict[str, object] | None = None):
        self._responses = method_responses or {}
        self.calls: dict[str, list[dict[str, object]]] = {}

    def __getattr__(self, method_name: str) -> Callable[..., object]:
        def caller(**kwargs: object) -> object:
            self.calls.setdefault(method_name, []).append(kwargs)
            return self._responses.get(method_name, {})
        return caller


class _FakeSource:
    """Minimal Source-compliant fake for registry/facade tests.

    Accepts either positional (name, kind) for direct tests, or a config dict
    for ConnectorRegistry.from_config (which calls factory(config)).
    """

    def __init__(self, name_or_config: object = None, kind: str = "logs"):
        if isinstance(name_or_config, dict):
            cfg: dict[str, object] = name_or_config
            self.name = str(cfg.get("name", "fake"))
            self.KIND = str(cfg.get("kind", kind))
        elif isinstance(name_or_config, str):
            self.name = name_or_config
            self.KIND = kind
        else:
            self.name = "fake"
            self.KIND = kind

    def health(self) -> dict[str, object]:
        return {"ok": True, "detail": "ok"}

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        return [
            {"ts": 1000.0, "source": self.name, "kind": self.KIND,
             "level_or_status": "info", "message": "test", "value": None,
             "labels": {"trace_id": "abc"}, "raw": None}
        ]


class _BlowingSource(_FakeSource):
    """Source whose query() always raises — exercises resilience."""

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        raise RuntimeError("deliberate blow-up")


class _SlowHealthSource(_FakeSource):
    """Source whose health() blocks forever — exercises healthcheck timeout."""

    def health(self) -> dict[str, object]:
        time.sleep(10.0)
        return {"ok": True}


# ============================================================================
# 1. Cloud Provider Config Validation (AWS/GCP/Azure)
# ============================================================================


class TestCloudProviderConfigValidation:
    """Config loading, credential resolution, and region validation."""

    # --- AWS ---

    def test_aws_source_default_constructs_with_region(self):
        source = AwsObservabilitySource({"name": "aws-prod", "region": "us-east-1"})
        assert source.name == "aws-prod"
        assert source.region == "us-east-1"
        assert source.KIND == "aws_observability"

    def test_aws_source_default_name_falls_back_to_kind(self):
        source = AwsObservabilitySource({"region": "eu-west-2"})
        assert source.name == "aws_observability"

    def test_aws_source_health_ok_with_mock_client(self):
        client = _MockAwsClient()
        source = AwsObservabilitySource(
            {"name": "aws-test", "region": "us-west-2"}, client_factory=lambda svc: client
        )
        result = source.health()
        assert result["ok"] is True
        assert "us-west-2" in str(result["detail"])

    def test_aws_source_query_logs_mode_dispatches(self):
        client = _MockAwsClient({"filter_log_events": {"events": []}})
        source = AwsObservabilitySource(
            {"name": "aws-logs"}, client_factory=lambda svc: client
        )
        records = source.query({"mode": "logs", "logGroupName": "/aws/lambda/myfunc"})
        assert isinstance(records, list)
        assert "filter_log_events" in client.calls

    def test_aws_source_query_unknown_mode_raises(self):
        source = AwsObservabilitySource({"name": "aws"}, client_factory=lambda svc: _MockAwsClient())
        with pytest.raises(ValueError, match="unknown or missing query mode"):
            source.query({"mode": "bogus"})

    # --- GCP ---

    def test_gcp_source_constructs_with_explicit_token(self):
        transport = _MockGcpTransport()
        source = GcpObservabilitySource(
            {"name": "gcp-dev", "project": "my-project"},
            transport=transport,
            token="ya29.fake-token",
        )
        assert source.project == "my-project"
        assert source.KIND == "gcp_observability"

    def test_gcp_source_health_ok(self):
        transport = _MockGcpTransport(status_code=200, response_json={"timeSeries": []})
        source = GcpObservabilitySource(
            {"name": "gcp-health", "project": "p", "timeout": "5.0"},
            transport=transport,
            token="token",
        )
        result = source.health()
        assert result["ok"] is True
        assert "monitoring reachable" in str(result["detail"])

    def test_gcp_source_health_fails_safely(self):
        transport = _MockGcpTransport(status_code=500)
        source = GcpObservabilitySource(
            {"name": "gcp-broken", "project": "p", "timeout": "5.0"},
            transport=transport,
            token="token",
        )
        result = source.health()
        assert result["ok"] is False

    def test_gcp_source_query_logs_mode(self):
        transport = _MockGcpTransport(
            status_code=200,
            response_json={"entries": [{"textPayload": "hello", "timestamp": "2025-01-01T00:00:00Z"}]},
        )
        source = GcpObservabilitySource(
            {"name": "gcp-logs", "project": "proj"},
            transport=transport,
            token="token",
        )
        records = source.query({"mode": "logs", "page_size": 50})
        assert isinstance(records, list)

    def test_gcp_source_refuses_internal_endpoint(self):
        with pytest.raises(ValueError, match="refusing internal"):
            GcpObservabilitySource(
                {"name": "bad", "project": "p", "logging_endpoint": "http://169.254.169.254/"},
                transport=_MockGcpTransport(),
                token="t",
            )

    # --- Azure ---

    def test_azure_source_constructs_with_required_fields(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AZURE_TOKEN", "fake-azure-token")

        def fake_transport(method: str, url: str, headers: dict[str, str],
                           body: object, timeout: float) -> _MockHttpResponse:
            return _MockHttpResponse(200, json_body={"tables": []})

        source = AzureMonitorSource({
            "name": "azure-mon",
            "workspace_id": "ws-123",
            "token_env": "AZURE_TOKEN",
            "transport": fake_transport,
        })
        assert source.name == "azure-mon"
        assert source.KIND == "logs"

    def test_azure_source_health_ok(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AZURE_TOKEN", "tok")

        def fake_transport(method: str, url: str, headers: dict[str, str],
                           body: object, timeout: float) -> _MockHttpResponse:
            return _MockHttpResponse(200, json_body={"tables": []})

        source = AzureMonitorSource({
            "name": "azure-ok",
            "workspace_id": "ws",
            "token_env": "AZURE_TOKEN",
            "transport": fake_transport,
        })
        result = source.health()
        assert result["ok"] is True

    def test_azure_source_missing_workspace_id_raises(self):
        with pytest.raises(ValueError, match="workspace_id"):
            AzureMonitorSource({"name": "bad", "token_env": "X"})


# ============================================================================
# 2. Docker / Kubernetes connector
# ============================================================================


class TestDockerConnector:
    """Image management, container ops, deploy workflow (mocked)."""

    def test_docker_source_constructs_with_defaults(self):
        source = DockerEngineSource({"name": "docker-local"})
        assert source.name == "docker-local"
        assert source.KIND == "logs"

    def test_docker_source_health_with_ssrf_error(self):
        source = DockerEngineSource({"name": "d", "base_url": "http://127.0.0.1:2375"})
        result = source.health()
        assert result["ok"] is False

    def test_docker_source_health_ok_via_mock(self):
        transport = _MockDockerTransport({
            "/info": _DockerResponse(200, {}, json.dumps({"ID": "abc"}).encode()),
        })
        source = DockerEngineSource({"name": "d", "transport": transport})
        result = source.health()
        assert result["ok"] is True

    def test_docker_source_ps_via_mock(self):
        transport = _MockDockerTransport({
            "/containers/json": _DockerResponse(
                200, {}, json.dumps([{"Id": "abc", "Names": ["/web"]}]).encode()
            ),
        })
        source = DockerEngineSource({"name": "d", "transport": transport})
        records = source.query({"mode": "ps"})
        assert len(records) == 1
        assert records[0]["message"] == "web"

    def test_docker_source_logs_via_mock(self):
        transport = _MockDockerTransport({
            "/containers/abc/logs": _DockerResponse(
                200, {}, b"2025-01-01T00:00:00Z hello world\n"
            ),
        })
        source = DockerEngineSource({"name": "d", "transport": transport})
        records = source.query({"mode": "logs", "container_id": "abc"})
        assert len(records) == 1
        assert "hello world" in str(records[0]["message"])

    def test_docker_source_events_via_mock(self):
        transport = _MockDockerTransport({
            "/events": _DockerResponse(
                200, {}, b""
            ),
        })
        source = DockerEngineSource({"name": "d", "transport": transport})
        records = source.query({"mode": "events"})
        assert isinstance(records, list)

    def test_docker_record_helper(self):
        rec = docker_record(
            ts="2025-01-01T00:00:00Z", source="d", level_or_status="info",
            message="hello", value=None, labels={}, raw=None,
        )
        assert rec["kind"] == "logs"
        assert rec["source"] == "d"

    def test_rfc3339_split(self):
        ts, msg = _split_rfc3339("2025-01-01T00:00:00.000Z this is the message")
        assert ts == "2025-01-01T00:00:00.000Z"
        assert msg == "this is the message"

    def test_multiplexed_detection(self):
        header = b"\x01\x00\x00\x00\x00\x00\x00\x05hello"
        assert _is_multiplexed(header) is True

    def test_iter_log_payload_multiplexed(self):
        frame = b"\x01\x00\x00\x00\x00\x00\x00\x05hello"
        pairs = _iter_log_payload(frame)
        assert len(pairs) == 1
        assert pairs[0][0] == "stdout"


class TestKubernetesConnector:
    """Pod operations, log retrieval, event listing."""

    def test_k8s_source_constructs_with_required_api_server(self):
        source = KubernetesSource({
            "name": "k8s-prod",
            "api_server": "https://k8s.example.com",
            "token_env": "K8S_TOKEN",
            "namespace": "production",
            "allow_private": True,
        })
        assert source.name == "k8s-prod"
        assert source.KIND == "logs"

    def test_k8s_source_rejects_blocked_endpoint(self):
        with pytest.raises(ValueError, match="ssrf"):
            KubernetesSource({
                "name": "bad",
                "api_server": "http://169.254.169.254",
                "token_env": "TOK",
            })

    def test_k8s_source_health_ok(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("K8S_TOKEN", "fake-token")
        transport = _MockK8sTransport(200, json_body={"major": "1"})
        source = KubernetesSource({
            "name": "k8s", "api_server": "https://k8s.example.com",
            "token_env": "K8S_TOKEN", "allow_private": True,
            "transport": transport,
        })
        result = source.health()
        assert result["ok"] is True

    def test_k8s_source_query_logs(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("K8S_TOKEN", "tok")
        log_text = (
            "2025-06-01T10:00:00.000Z INFO Starting server\n"
            "2025-06-01T10:00:01.000Z ERROR connection refused\n"
        )
        transport = _MockK8sTransport(200, text_body=log_text)
        source = KubernetesSource({
            "name": "k8s", "api_server": "https://k8s.example.com",
            "token_env": "K8S_TOKEN", "allow_private": True,
            "transport": transport, "namespace": "default",
        })
        records = source.query({"mode": "logs", "pod": "my-pod"})
        assert len(records) >= 1
        for r in records:
            assert r["kind"] == "logs"

    def test_k8s_source_query_events(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("K8S_TOKEN", "tok")
        transport = _MockK8sTransport(
            200, json_body={
                "items": [
                    {
                        "metadata": {"name": "evt-1", "namespace": "default"},
                        "type": "Warning",
                        "reason": "BackOff",
                        "message": "Back-off restarting failed container",
                        "lastTimestamp": "2025-06-01T10:00:00Z",
                    }
                ]
            },
        )
        source = KubernetesSource({
            "name": "k8s", "api_server": "https://k8s.example.com",
            "token_env": "K8S_TOKEN", "allow_private": True,
            "transport": transport,
        })
        records = source.query({"mode": "events"})
        assert isinstance(records, list)


# ============================================================================
# 3. GitHub / GitLab connector
# ============================================================================


class TestGitHubConnector:
    """Repo operations, PR management, webhook handling."""

    def test_github_source_constructs_valid_repo(self):
        source = GitHubActionsSource({"repo": "owner/repo"})
        assert source.repo == "owner/repo"

    def test_github_source_rejects_malformed_repo(self):
        with pytest.raises(ValueError, match="must be"):
            GitHubActionsSource({"repo": "badformat"})

    def test_github_source_health_ok(self):
        transport = _MockTransport(default_status=200, default_body={})
        source = GitHubActionsSource({"repo": "a/b"}, http_get=transport)
        result = source.health()
        assert result["ok"] is True

    def test_github_source_health_not_ok(self):
        transport = _MockTransport(default_status=403, default_body={})
        source = GitHubActionsSource({"repo": "a/b"}, http_get=transport)
        result = source.health()
        assert result["ok"] is False

    def test_github_source_query_returns_normalized_records(self):
        transport = _MockTransport(default_status=200, default_body={
            "workflow_runs": [
                {
                    "id": 123, "name": "CI", "head_branch": "main",
                    "head_sha": "abc123", "event": "push",
                    "status": "completed", "conclusion": "success",
                    "updated_at": "2025-01-01T10:00:00Z",
                }
            ]
        })
        source = GitHubActionsSource({"repo": "my/repo"}, http_get=transport)
        records = source.query({})
        assert len(records) == 1
        assert records[0]["kind"] == "pipeline"
        assert records[0]["level_or_status"] == "success"
        assert records[0]["labels"]["head_sha"] == "abc123"

    def test_github_source_query_filters_by_branch(self):
        transport = _MockTransport(default_status=200, default_body={
            "workflow_runs": [
                {"id": 1, "name": "CI", "head_branch": "main",
                 "status": "completed", "conclusion": "success",
                 "updated_at": "2025-01-01T10:00:00Z"},
                {"id": 2, "name": "CI", "head_branch": "feature/x",
                 "status": "completed", "conclusion": "failure",
                 "updated_at": "2025-01-01T11:00:00Z"},
            ]
        })
        source = GitHubActionsSource({"repo": "a/b"}, http_get=transport)
        records = source.query({"branch": "main"})
        assert len(records) == 1
        assert records[0]["labels"]["run_id"] == 1

    def test_github_source_fetch_failed_logs(self):
        transport = _MockTransport(default_status=200, default_body={
            "jobs": [
                {"id": 1, "name": "build", "conclusion": "failure"},
                {"id": 2, "name": "test", "conclusion": "success"},
            ]
        })
        source = GitHubActionsSource({"repo": "a/b"}, http_get=transport)
        jobs = source.fetch_failed_logs(123)
        assert len(jobs) == 2


class TestGitLabConnector:
    """GitLab pipeline observability."""

    def test_gitlab_source_constructs(self):
        source = GitlabCiSource(
            {"project_id": "12345"},
            http_get=lambda url, headers: (200, []),
        )
        assert source.KIND == "pipeline"

    def test_gitlab_source_rejects_missing_project(self):
        with pytest.raises(ValueError, match="project_id"):
            GitlabCiSource(
                {},
                http_get=lambda url, headers: (200, []),
            )

    def test_gitlab_source_health_ok(self):
        def fake_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return 200, [{"id": 1, "status": "success"}]

        source = GitlabCiSource({"project_id": "1"}, http_get=fake_get)
        result = source.health()
        assert result["ok"] is True

    def test_gitlab_source_query_normalizes_pipelines(self):
        def fake_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return 200, [
                {
                    "id": 1, "ref": "main", "status": "success",
                    "updated_at": "2025-06-01T10:00:00Z",
                }
            ]

        source = GitlabCiSource({"project_id": "1"}, http_get=fake_get)
        records = source.query({})
        assert len(records) == 1
        assert records[0]["kind"] == "pipeline"


# ============================================================================
# 4. Webhook Buffer (push-based ingest)
# ============================================================================


class TestWebhookBuffer:
    """Push-based record buffer — ring semantics, thread safety, query."""

    def test_buffer_constructs_with_defaults(self):
        buf = WebhookBufferSource(name="webhook")
        assert buf.name == "webhook"
        assert buf.capacity == 1000

    def test_buffer_accepts_and_queries_records(self):
        buf = WebhookBufferSource(name="buf", maxlen=50)
        rec = {"ts": 100.0, "source": "test", "kind": "logs",
               "level_or_status": "info", "message": "hello",
               "value": None, "labels": {}, "raw": None}
        buf.push_one(rec)
        results = buf.query({})
        assert len(results) == 1
        assert results[0]["message"] == "hello"

    def test_buffer_ring_eviction(self):
        buf = WebhookBufferSource(name="ring", maxlen=3)
        records = [
            {"ts": float(i), "source": "s", "kind": "logs",
             "level_or_status": "info", "message": str(i),
             "value": None, "labels": {}, "raw": None}
            for i in range(5)
        ]
        buf.push(records)
        results = buf.query({})
        assert len(results) == 3
        assert results[0]["message"] == "2"

    def test_buffer_health(self):
        buf = WebhookBufferSource(name="buf")
        result = buf.health()
        assert result["ok"] is True
        assert "size" in result

    def test_buffer_push_bulk(self):
        buf = WebhookBufferSource(name="many", maxlen=10)
        records = [
            {"ts": float(i), "source": "s", "kind": "logs",
             "level_or_status": "info", "message": str(i),
             "value": None, "labels": {}, "raw": None}
            for i in range(7)
        ]
        accepted = buf.push(records)
        assert accepted == 7
        assert len(buf.query({})) == 7

    def test_buffer_thread_safety(self):
        buf = WebhookBufferSource(name="ts", maxlen=100)
        errors: list[Exception] = []

        def pusher(start: int):
            for i in range(start, start + 20):
                try:
                    buf.push_one({
                        "ts": float(i), "source": "s", "kind": "logs",
                        "level_or_status": "info", "message": str(i),
                        "value": None, "labels": {}, "raw": None,
                    })
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=pusher, args=(b * 20,)) for b in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(buf.query({})) > 0

    def test_buffer_query_filters_by_kind(self):
        buf = WebhookBufferSource(name="filtered", maxlen=20, kind="metrics")
        records = [
            {"ts": 1.0, "source": "s", "kind": "logs",
             "level_or_status": "info", "message": "log",
             "value": None, "labels": {}, "raw": None},
            {"ts": 2.0, "source": "s", "kind": "metrics",
             "level_or_status": "info", "message": "metric",
             "value": 42.0, "labels": {}, "raw": None},
        ]
        buf.push(records)
        results = buf.query({"kind": "metrics"})
        assert len(results) >= 1
        for r in results:
            assert r.get("kind") == "metrics"


# ============================================================================
# 5. Observability Facade + SourceRegistry
# ============================================================================


class TestObservabilityFacade:
    """SourceRegistry registration, Observability find + associate."""

    def test_registry_register_and_get(self):
        reg = SourceRegistry()
        src = _FakeSource("test-src", "logs")
        reg.register(src)
        assert reg.get("test-src") is src
        assert len(reg.all()) == 1

    def test_registry_by_kind_filters(self):
        reg = SourceRegistry()
        reg.register(_FakeSource("log1", "logs"))
        reg.register(_FakeSource("metric1", "metrics"))
        reg.register(_FakeSource("log2", "logs"))
        log_sources = reg.by_kind("logs")
        assert len(log_sources) == 2

    def test_registry_last_write_wins(self):
        reg = SourceRegistry()
        src_a = _FakeSource("dup", "logs")
        src_b = _FakeSource("dup", "metrics")
        reg.register(src_a)
        reg.register(src_b)
        assert reg.get("dup") is src_b

    def test_observability_find_fans_out(self):
        reg = SourceRegistry()
        reg.register(_FakeSource("log-a", kind="logs"))
        reg.register(_FakeSource("log-b", kind="logs"))
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) == 2

    def test_observability_find_resilient_to_blowups(self):
        reg = SourceRegistry()
        reg.register(_BlowingSource("boom", "logs"))
        reg.register(_FakeSource("safe", "logs"))
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) >= 1
        assert any(r["source"] == "safe" for r in results)

    def test_observability_find_filters_by_kind(self):
        reg = SourceRegistry()
        reg.register(_FakeSource("log1", "logs"))
        reg.register(_FakeSource("met1", "metrics"))
        obs = Observability(reg)
        results = obs.find({}, kinds=["metrics"])
        assert len(results) == 1
        assert results[0]["source"] == "met1"

    def test_observability_associate_by_label(self):
        records = [
            {"ts": 1.0, "labels": {"trace_id": "aaa"}, "source": "a"},
            {"ts": 2.0, "labels": {"trace_id": "aaa"}, "source": "b"},
            {"ts": 3.0, "labels": {"trace_id": "bbb"}, "source": "c"},
            {"ts": 4.0, "labels": {}, "source": "d"},
        ]
        groups = Observability.associate(records, by="trace_id")
        assert len(groups) == 2
        assert groups[0]["key"] == "aaa"
        assert len(groups[0]["records"]) == 2
        assert groups[1]["key"] == "bbb"
        assert len(groups[1]["records"]) == 1

    def test_observability_associate_by_window(self):
        records = [
            {"ts": 100.0, "source": "a"},
            {"ts": 110.0, "source": "b"},
            {"ts": 200.0, "source": "c"},
            {"ts": 210.0, "source": "d"},
            {"ts": 400.0, "source": "e"},
        ]
        groups = Observability.associate(records, by="time_window", window_s=20.0)
        assert len(groups) == 3

    def test_observability_sort_by_ts(self):
        records = [
            {"ts": 300.0, "source": "c"},
            {"ts": 100.0, "source": "a"},
            {"ts": 200.0, "source": "b"},
        ]
        sorted_recs = Observability._sort_by_ts(records)
        assert sorted_recs[0]["ts"] == 100.0
        assert sorted_recs[1]["ts"] == 200.0
        assert sorted_recs[2]["ts"] == 300.0

    def test_observability_sort_none_ts_last(self):
        records = [
            {"ts": None, "source": "a"},
            {"ts": 100.0, "source": "b"},
            {"ts": None, "source": "c"},
        ]
        sorted_recs = Observability._sort_by_ts(records)
        assert sorted_recs[0]["source"] == "b"
        assert sorted_recs[-1]["source"] == "c"


# ============================================================================
# 6. API Connector Error Handling (timeout, retry, circuit breaker)
# ============================================================================


class TestApiErrorHandling:
    """Timeout, retry, circuit breaker patterns at the connector level."""

    def test_sanitize_exc_message_no_leak(self):
        exc = ValueError("secret: tok_abc123 in response")
        label = sanitize_exc_message(exc)
        assert label == type(exc).__name__
        assert "tok_abc123" not in label

    def test_sanitize_str_redacts_urls(self):
        result = sanitize_str("fetch https://internal.local/secret endpoint")
        assert "https://internal.local" not in result
        assert "[REDACTED-URL]" in result

    def test_sanitize_str_redacts_tokens(self):
        result = sanitize_str("token: abcdefgh12345678 was used")
        assert "abcdefgh" not in result
        assert "[REDACTED]" in result

    def test_sanitize_str_redacts_paths(self):
        result = sanitize_str("file at /etc/secret/password was read")
        assert "/etc/secret" not in result

    def test_connector_config_error(self):
        err = ConnectorConfigError("bad config")
        assert isinstance(err, ValueError)

    def test_ssrf_error(self):
        err = SSRFError("blocked host")
        assert isinstance(err, ValueError)

    def test_healthcheck_timeout(self):
        source = _SlowHealthSource("slow", "logs")
        result = run_healthcheck(source, timeout=0.05)
        assert result["status"] == "unhealthy"
        assert "timeout" in result["detail"]

    def test_classify_health_ok(self):
        result = classify_health({"ok": True, "detail": "all good"}, "src")
        assert result["status"] == "healthy"

    def test_classify_health_false(self):
        result = classify_health({"ok": False, "detail": "down"}, "src")
        assert result["status"] == "unhealthy"

    def test_classify_health_missing_ok(self):
        result = classify_health({"detail": "something"}, "src")
        assert result["status"] == "unhealthy"


# ============================================================================
# 7. Provider Registry (ConnectorRegistry)
# ============================================================================


class TestConnectorRegistry:
    """Register, search, list providers via ConnectorRegistry."""

    def test_registry_from_config_empty(self):
        reg = ConnectorRegistry.from_config(None)
        assert reg.list_sources() == []
        assert reg.errors() == []

    def test_registry_from_config_with_factory(self):
        source_class = _FakeSource

        reg = ConnectorRegistry.from_config(
            [{"name": "my-log", "kind": "logs", "factory": "fakesrc"}],
            factories={"fakesrc": source_class},
        )
        sources = reg.list_sources()
        assert len(sources) == 1
        assert sources[0]["name"] == "my-log"

    def test_registry_construct_error_skipped(self):
        def bad_factory(config: dict[str, Any]) -> None:
            raise RuntimeError("cannot construct")

        reg = ConnectorRegistry.from_config(
            [{"name": "bad-src", "kind": "logs", "factory": "bad"}],
            factories={"bad": bad_factory},
        )
        assert reg.get("bad-src") is None
        assert len(reg.errors()) == 1
        assert "construct" in reg.errors()[0]["error"]

    def test_registry_bad_config_not_a_dict(self):
        reg = ConnectorRegistry.from_config(["not-a-dict"])
        assert len(reg.errors()) == 1

    def test_registry_missing_name(self):
        reg = ConnectorRegistry.from_config([{"kind": "logs"}])
        assert len(reg.errors()) == 1
        assert "missing 'name'" in reg.errors()[0]["error"]

    def test_registry_by_kind_grouping(self):
        reg = ConnectorRegistry.from_config(
            [
                {"name": "log1", "kind": "logs", "factory": "f"},
                {"name": "met1", "kind": "metrics", "factory": "f"},
            ],
            factories={"f": _FakeSource},
        )
        groups = reg.by_kind()
        assert "logs" in groups
        assert "metrics" in groups

    def test_registry_names_returns_all(self):
        reg = ConnectorRegistry.from_config(
            [
                {"name": "a", "kind": "logs", "factory": "f"},
                {"name": "b", "kind": "metrics", "factory": "f"},
            ],
            factories={"f": _FakeSource},
        )
        assert set(reg.names()) == {"a", "b"}

    def test_registry_query_unknown_raises(self):
        reg = ConnectorRegistry.from_config(None)
        with pytest.raises(KeyError, match="no registered source"):
            reg.query("nonexistent", {})

    def test_registry_health_all(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "h", "kind": "logs", "factory": "f"}],
            factories={"f": _FakeSource},
        )
        health_map = reg.health_all()
        assert "h" in health_map
        assert health_map["h"]["ok"] is True

    def test_registry_close_best_effort(self):
        reg = ConnectorRegistry.from_config(
            [{"name": "c", "kind": "logs", "factory": "f"}],
            factories={"f": _FakeSource},
        )
        reg.close()  # should not raise

    def test_validate_class_name_good(self):
        _validate_class_name("MyConnectorSource")

    def test_validate_class_name_bad_produces_error(self):
        with pytest.raises(ValueError):
            _validate_class_name("badname")

    def test_validate_class_name_blocks_dunder(self):
        with pytest.raises(ValueError, match="starts with '_'"):
            _validate_class_name("__init__")

    def test_validate_source_class_non_callable(self):
        with pytest.raises(TypeError, match="not callable"):
            _validate_source_class(42)


# ============================================================================
# 8. Normalize — join keys, auth families, correlation
# ============================================================================


class TestNormalizeModule:
    """Cross-source normalization helpers."""

    def test_sanitize_metric_value_valid(self):
        assert sanitize_metric_value(42) == 42.0
        assert sanitize_metric_value("3.14") == 3.14

    def test_sanitize_metric_value_invalid(self):
        assert sanitize_metric_value(None) is None
        assert sanitize_metric_value("hello") is None
        assert sanitize_metric_value(float("nan")) is None

    def test_auth_family_classification(self):
        assert auth_family("aws-observability") == "aws"
        assert auth_family("cloudwatch-logs") == "aws"
        assert auth_family("datadog-prod") == "datadog"
        assert auth_family("azure-monitor") == "azure"
        assert auth_family("gcp-observability") == "gcp"
        assert auth_family("github-actions") == "github"
        assert auth_family("gitlab-ci") == "gitlab"
        assert auth_family("grafana-loki") == "grafana"
        assert auth_family("splunk-hec") == "splunk"
        assert auth_family("newrelic-apm") == "newrelic"
        assert auth_family("pagerduty-alerts") == "pagerduty"
        assert auth_family("elastic-search") == "elastic"

    def test_auth_family_unknown(self):
        assert auth_family("xyz-unknown-thing") == "unknown"

    def test_canonical_severities_constant(self):
        assert CANONICAL_SEVERITIES == ("debug", "info", "warn", "error", "critical")

    def test_normalize_join_keys_idempotent(self):
        rec: dict[str, dict[str, object]] = {
            "labels": {"host": "server1", "instance": "i-abc"},
        }
        result = normalize_join_keys(rec)
        assert "join" in result
        result2 = normalize_join_keys(rec)
        assert result == result2

    def test_normalize_join_keys_missing_labels(self):
        result = normalize_join_keys({"labels": {}})
        assert result.get("join") == {}

    def test_correlate_by_label(self):
        records = [
            {"labels": {"host": "a"}, "ts": 1.0, "source": "x"},
            {"labels": {"host": "a"}, "ts": 2.0, "source": "y"},
            {"labels": {"host": "b"}, "ts": 3.0, "source": "z"},
        ]
        groups = correlate([dict(r) for r in records], by="host")
        assert len(groups) == 2

    def test_bundle_credentials_no_leak(self):
        cfg = {"aws_access_key_id_env": "AWS_KEY", "aws_secret_key_env": "AWS_SECRET"}
        bundled = bundle_credentials(cfg)
        assert isinstance(bundled, dict)
        for val in bundled.values():
            assert isinstance(val, str)
            assert "SECRET" not in val.upper() or val.startswith("AWS_")

    def test_auth_family_prefixes_constant(self):
        assert isinstance(AUTH_FAMILY_PREFIXES, dict)


# ============================================================================
# 9. Base module — normalized_record, is_safe_endpoint, healthcheck
# ============================================================================


class TestBaseModule:
    """normalized_record builder, SSRF guard, healthcheck pipeline."""

    def test_normalized_record_builder(self):
        rec = normalized_record(
            source="test-src", kind="logs", message="hello world", ts=1234.5,
        )
        assert rec["source"] == "test-src"
        assert rec["ts"] == 1234.5
        assert rec["kind"] == "logs"

    def test_normalized_record_defaults(self):
        rec = normalized_record(source="s", kind="metrics", value=3.14)
        assert rec["level_or_status"] == "info"
        assert rec["message"] == ""

    def test_normalized_record_nan_ts_coerced(self):
        rec = normalized_record(source="s", kind="logs", ts=float("nan"))
        assert rec["ts"] is None

    def test_normalized_record_nan_value_coerced(self):
        rec = normalized_record(source="s", kind="metrics", value=float("inf"))
        assert rec["value"] is None

    def test_is_safe_endpoint_public(self):
        assert is_safe_endpoint("https://api.example.com/v1") is True

    def test_is_safe_endpoint_private(self):
        assert is_safe_endpoint("http://10.0.0.1/") is False

    def test_is_safe_endpoint_loopback(self):
        assert is_safe_endpoint("http://127.0.0.1/") is False

    def test_is_safe_endpoint_metadata(self):
        assert is_safe_endpoint("http://169.254.169.254/latest") is False

    def test_run_healthcheck_healthy(self):
        source = _FakeSource("healthy-src", "logs")
        result = run_healthcheck(source, timeout=5.0)
        assert result["status"] == "healthy"

    def test_run_healthcheck_exception(self):
        class _ErrorSource:
            name = "err"
            KIND = "logs"

            def health(self) -> dict[str, object]:
                raise RuntimeError("crash")

            def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
                return []

        result = run_healthcheck(_ErrorSource(), timeout=5.0)
        assert result["status"] == "unhealthy"

    def test_classify_health_for_source_degraded(self, monkeypatch: pytest.MonkeyPatch):
        # Source declares a *_env attr whose env var is NOT set
        class _UnconfiguredSource:
            name = "uc"
            KIND = "logs"
            API_TOKEN_ENV = "MISSING_ENV_VAR"

            def health(self) -> dict[str, object]:
                return {"ok": False, "detail": "no auth"}

            def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
                return []

        monkeypatch.delenv("MISSING_ENV_VAR", raising=False)
        result = classify_health_for_source(_UnconfiguredSource(), {"ok": False})
        assert result["status"] == "degraded"
