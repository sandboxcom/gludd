"""Unit tests for the Argo Workflows pipeline connector (fully mocked transport)."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from general_ludd.connectors.argo_workflows import ArgoWorkflowsSource, ConnectorConfigError

CANNED_RESPONSE = {
    "items": [
        {
            "metadata": {"name": "build-abc", "namespace": "ci", "uid": "uid-1"},
            "status": {
                "phase": "Succeeded",
                "startedAt": "2026-06-04T08:00:00Z",
                "finishedAt": "2026-06-04T08:10:00Z",
                "progress": "5/5",
            },
        },
        {
            "metadata": {"name": "deploy-xyz", "namespace": "ci", "uid": "uid-2"},
            "status": {
                "phase": "Running",
                "startedAt": "2026-06-04T09:00:00Z",
                "progress": "2/5",
            },
        },
        {
            "metadata": {"name": "test-fail", "namespace": "ci", "uid": "uid-3"},
            "status": {
                "phase": "Failed",
                "startedAt": "2026-06-04T07:00:00Z",
                "finishedAt": "2026-06-04T07:03:00Z",
            },
        },
    ]
}


class _FakeTransport:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def __call__(self, method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), timeout))
        return self.status, self.body


def _config(**overrides: object) -> dict[str, object]:
    base = {
        "base_url": "https://argo.example.com",
        "namespace": "ci",
        "token_env": "ARGO_TEST_TOKEN",
    }
    base.update(overrides)
    return base


def test_query_normalizes_workflows() -> None:
    transport = _FakeTransport(200, json.dumps(CANNED_RESPONSE).encode())
    src = ArgoWorkflowsSource(_config(), transport=transport)

    events = src.query()

    assert len(events) == 3
    succeeded = events[0]
    assert succeeded["source"] == "argo_workflows"
    assert succeeded["kind"] == "pipeline"
    assert succeeded["level_or_status"] == "Succeeded"
    assert succeeded["ts"] == "2026-06-04T08:10:00Z"  # finishedAt preferred
    assert succeeded["message"] == "build-abc"
    assert succeeded["labels"] == {"namespace": "ci", "uid": "uid-1"}
    assert succeeded["raw"]["metadata"]["uid"] == "uid-1"
    assert set(succeeded) == {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}


def test_http_get_compatibility_accepts_decoded_json() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def http_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
        calls.append((url, headers))
        return 200, CANNED_RESPONSE

    src = ArgoWorkflowsSource(_config(), http_get=http_get)

    events = src.query()

    assert len(events) == 3
    assert calls[0][0] == "https://argo.example.com/api/v1/workflows/ci"


def test_transport_and_http_get_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match=r"transport.*http_get"):
        ArgoWorkflowsSource(
            _config(),
            transport=_FakeTransport(200, b"{}"),
            http_get=lambda _url, _headers: (200, {}),
        )


def test_status_mapping_and_ts_fallback() -> None:
    transport = _FakeTransport(200, json.dumps(CANNED_RESPONSE).encode())
    src = ArgoWorkflowsSource(_config(), transport=transport)
    events = src.query()
    running = events[1]
    assert running["level_or_status"] == "Running"
    assert running["ts"] == "2026-06-04T09:00:00Z"  # falls back to startedAt
    failed = events[2]
    assert failed["level_or_status"] == "Failed"


def test_empty_items_null() -> None:
    transport = _FakeTransport(200, json.dumps({"items": None}).encode())
    src = ArgoWorkflowsSource(_config(), transport=transport)
    assert src.query() == []


def test_workflows_url() -> None:
    transport = _FakeTransport(200, b'{"items": []}')
    src = ArgoWorkflowsSource(_config(), transport=transport)
    src.query()
    assert transport.calls[-1][1] == "https://argo.example.com/api/v1/workflows/ci"


def test_auth_header_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGO_TEST_TOKEN", "bearer-xyz")
    transport = _FakeTransport(200, b'{"items": []}')
    src = ArgoWorkflowsSource(_config(), transport=transport)
    src.query()
    _, _, headers, _ = transport.calls[-1]
    assert headers["Authorization"] == "Bearer bearer-xyz"


def test_no_auth_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGO_TEST_TOKEN", raising=False)
    transport = _FakeTransport(200, b'{"items": []}')
    src = ArgoWorkflowsSource(_config(), transport=transport)
    src.query()
    _, _, headers, _ = transport.calls[-1]
    assert "Authorization" not in headers


def test_token_never_hardcoded() -> None:
    src = ArgoWorkflowsSource(_config(token_env="DEFINITELY_UNSET_VAR"))
    assert src._token == ""


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "http://10.96.0.1",  # typical k8s service CIDR
        "http://169.254.169.254",
        "http://[::1]",
        # Loopback / metadata by *name* (not IP literal) must also be rejected.
        "http://localhost",
        "https://ip6-localhost",
        "http://metadata.google.internal",
    ],
)
def test_internal_base_url_rejected_by_default(bad_url: str) -> None:
    with pytest.raises(ConnectorConfigError):
        ArgoWorkflowsSource(_config(base_url=bad_url))


@pytest.mark.parametrize(
    "internal_url",
    [
        "http://127.0.0.1",
        "http://10.96.0.1",
        "https://192.168.1.50:6443",
        # The name-based denylist is also bypassed by the opt-in.
        "http://localhost:2746",
        "http://metadata.google.internal",
    ],
)
def test_internal_base_url_allowed_with_opt_in(internal_url: str) -> None:
    # allow_private=True is the k8s-internal opt-in (mirrors the kubernetes connector).
    src = ArgoWorkflowsSource(_config(base_url=internal_url, allow_private=True))
    assert src.allow_private is True
    assert src.base_url == internal_url.rstrip("/")


def test_public_base_url_accepted_without_opt_in() -> None:
    src = ArgoWorkflowsSource(_config(base_url="https://argo.example.com/"))
    assert src.base_url == "https://argo.example.com"


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost/",
        "http://metadata.google.internal/",
        "http://169.254.169.254/",
        "http://100.100.100.200/",  # Alibaba metadata IP — canonical guard catches it
    ],
)
def test_canonical_metadata_hosts_rejected(bad_url: str) -> None:
    # Regression for task #40 (SSRF-guard consolidation): the private/metadata
    # decision now delegates to security.ssrf.is_url_blocked, which blocks the
    # full canonical set — including the Alibaba metadata IP the bespoke
    # literal guard used to miss.
    with pytest.raises(ConnectorConfigError):
        ArgoWorkflowsSource(_config(base_url=bad_url))


def test_allow_private_permits_localhost() -> None:
    src = ArgoWorkflowsSource(_config(base_url="http://localhost:2746", allow_private=True))
    assert src.base_url == "http://localhost:2746"


def test_health_ok() -> None:
    src = ArgoWorkflowsSource(_config(), transport=_FakeTransport(200, b'{"items": []}'))
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok() -> None:
    src = ArgoWorkflowsSource(_config(), transport=_FakeTransport(403, b""))
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "HTTP 403"


def test_health_never_raises() -> None:
    def boom(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise RuntimeError("api server unreachable")

    src = ArgoWorkflowsSource(_config(), transport=boom)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "health check failed"


def test_query_raises_on_http_error() -> None:
    src = ArgoWorkflowsSource(_config(), transport=_FakeTransport(500, b""))
    with pytest.raises(ConnectorConfigError):
        src.query()


def test_fetch_jobs_alias() -> None:
    transport = _FakeTransport(200, json.dumps(CANNED_RESPONSE).encode())
    src = ArgoWorkflowsSource(_config(), transport=transport)
    assert src.fetch_jobs() == src.query()


def test_kind_and_name() -> None:
    src = ArgoWorkflowsSource(_config(name="argo-prod"))
    assert src.KIND == "pipeline"
    assert src.name == "argo-prod"
