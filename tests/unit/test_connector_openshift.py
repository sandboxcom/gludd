"""Unit tests for the OpenShift connector (mocked transport, no real cluster).

Covers, per contract:
* canned builds / pods / events / log payloads -> normalization schema
* internal/private api_server rejected unless allow_private=True
* Bearer header sourced from token_env
* health() ok / not-ok / never-raises
* SSRF literal-host block (no DNS), gated by allow_private
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.openshift import (
    HttpResponse,
    OpenShiftConfigError,
    OpenShiftSource,
)

# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #

PUBLIC_API = "https://api.openshift.example.com:6443"
NS = "ci-builds"
TOKEN_ENV = "OPENSHIFT_TEST_TOKEN"


class FakeResponse:
    """Minimal HttpResponse: a status code plus a JSON body or raw text."""

    def __init__(self, status_code: int = 200, *, body: Any = None, text: str | None = None) -> None:
        self._status = status_code
        self._body = body
        self._text = text if text is not None else (json.dumps(body) if body is not None else "")

    @property
    def status_code(self) -> int:
        return self._status

    @property
    def text(self) -> str:
        return self._text

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json body")
        return self._body


class RecordingTransport:
    """Records GET calls and returns queued responses (by URL substring)."""

    def __init__(self, routes: dict[str, FakeResponse] | None = None, default: FakeResponse | None = None) -> None:
        self.routes = routes or {}
        self.default = default or FakeResponse(200, body={"items": []})
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> HttpResponse:
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        return self.default


class BoomTransport:
    """Transport that raises — used to prove health() never propagates."""

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> HttpResponse:
        raise ConnectionError("network down")


def make_source(
    *,
    transport: Any = None,
    allow_private: bool = False,
    api_server: str = PUBLIC_API,
    extra: dict[str, Any] | None = None,
) -> OpenShiftSource:
    config: dict[str, Any] = {
        "api_server": api_server,
        "namespace": NS,
        "token_env": TOKEN_ENV,
        "allow_private": allow_private,
    }
    if extra:
        config.update(extra)
    return OpenShiftSource(config, transport=transport or RecordingTransport())


# --------------------------------------------------------------------------- #
# Canned payloads
# --------------------------------------------------------------------------- #

BUILDS_PAYLOAD = {
    "items": [
        {
            "metadata": {
                "name": "myapp-7",
                "namespace": NS,
                "labels": {"buildconfig": "myapp"},
            },
            "status": {
                "phase": "Complete",
                "startTimestamp": "2026-06-12T10:00:00Z",
                "completionTimestamp": "2026-06-12T10:05:00Z",
            },
        },
        {
            "metadata": {
                "name": "myapp-8",
                "namespace": NS,
                "labels": {"openshift.io/build-config.name": "myapp"},
            },
            "status": {
                "phase": "Running",
                "startTimestamp": "2026-06-12T11:00:00Z",
            },
        },
    ]
}

PODS_PAYLOAD = {
    "items": [
        {
            "metadata": {"name": "web-abc", "namespace": NS, "creationTimestamp": "2026-06-12T09:00:00Z"},
            "spec": {"nodeName": "node-1"},
            "status": {"phase": "Running"},
        }
    ]
}

EVENTS_PAYLOAD = {
    "items": [
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "lastTimestamp": "2026-06-12T12:00:00Z",
            "involvedObject": {"kind": "Pod", "name": "web-abc"},
        }
    ]
}

LOG_TEXT = "line one\nline two\n\nline three\n"


# --------------------------------------------------------------------------- #
# Contract: class attributes
# --------------------------------------------------------------------------- #


def test_kind_class_attr_is_logs() -> None:
    assert OpenShiftSource.KIND == "logs"
    assert "pipeline" in OpenShiftSource.KINDS


def test_name_attr_defaults_and_overrides() -> None:
    assert make_source().name == "openshift"
    src = make_source(extra={"name": "ocp-prod"})
    assert src.name == "ocp-prod"


# --------------------------------------------------------------------------- #
# Contract: private/SSRF host gating
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "host",
    [
        "https://10.0.0.5:6443",
        "https://192.168.1.10:6443",
        "https://172.16.5.5:6443",
        "https://127.0.0.1:6443",
        "https://localhost:6443",
        "https://openshift:6443",  # single-label, no dot
        "https://api.cluster.local:6443",
        "https://kubernetes.default.svc",
        "https://[::1]:6443",
    ],
)
def test_internal_host_rejected_by_default(host: str) -> None:
    with pytest.raises(OpenShiftConfigError):
        make_source(api_server=host)


@pytest.mark.parametrize(
    "host",
    [
        "https://10.0.0.5:6443",
        "https://localhost:6443",
        "https://openshift:6443",
        "https://[::1]:6443",
    ],
)
def test_internal_host_allowed_with_opt_in(host: str) -> None:
    src = make_source(api_server=host, allow_private=True)
    assert src.allow_private is True


def test_public_host_allowed_without_opt_in() -> None:
    src = make_source()
    assert src.allow_private is False
    assert src.api_server == PUBLIC_API


@pytest.mark.parametrize(
    "host",
    [
        "https://localhost:6443",
        "https://metadata.google.internal",
        "https://169.254.169.254",
        "https://100.100.100.200",  # Alibaba metadata IP — canonical guard catches it
    ],
)
def test_canonical_metadata_hosts_rejected(host: str) -> None:
    # Regression for task #40 (SSRF-guard consolidation): the literal
    # private/metadata decision delegates to security.ssrf.is_url_blocked. The
    # connector-specific internal-name heuristic (single-label / .svc /
    # .cluster.local) is layered on top and still covered above.
    with pytest.raises(OpenShiftConfigError):
        make_source(api_server=host)


def test_no_dns_resolution_for_literal_host() -> None:
    # A public-looking name that *would* resolve to a private IP is still
    # accepted, proving we block on the literal string only (no DNS lookup).
    src = make_source(api_server="https://api.openshift.example.com:6443")
    assert src.api_server.endswith(":6443")


def test_invalid_api_server_rejected() -> None:
    with pytest.raises(OpenShiftConfigError):
        OpenShiftSource({"api_server": "not-a-url", "namespace": NS})
    with pytest.raises(OpenShiftConfigError):
        OpenShiftSource({"namespace": NS})  # missing api_server
    with pytest.raises(OpenShiftConfigError):
        OpenShiftSource({"api_server": PUBLIC_API})  # missing namespace


def test_base_url_alias_accepted() -> None:
    src = OpenShiftSource({"base_url": PUBLIC_API, "namespace": NS}, transport=RecordingTransport())
    assert src.api_server == PUBLIC_API


# --------------------------------------------------------------------------- #
# Contract: Bearer header from token_env
# --------------------------------------------------------------------------- #


def test_bearer_header_present_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "sa-secret-token")
    transport = RecordingTransport()
    src = make_source(transport=transport)
    src.query({"mode": "pods"})
    assert transport.calls, "expected a GET to have been issued"
    auth = transport.calls[-1]["headers"].get("Authorization")
    assert auth == "Bearer sa-secret-token"


def test_no_bearer_header_when_token_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport()
    src = make_source(transport=transport)
    src.query({"mode": "pods"})
    assert "Authorization" not in transport.calls[-1]["headers"]


def test_requests_are_time_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    transport = RecordingTransport()
    src = make_source(transport=transport, extra={"timeout": 7.5})
    src.query({"mode": "pods"})
    assert transport.calls[-1]["timeout"] == 7.5


# --------------------------------------------------------------------------- #
# Contract: builds -> pipeline records
# --------------------------------------------------------------------------- #


def test_query_builds_normalization() -> None:
    transport = RecordingTransport(routes={"/builds": FakeResponse(200, body=BUILDS_PAYLOAD)})
    src = make_source(transport=transport)
    records = src.query({"mode": "builds"})

    assert len(records) == 2
    r0 = records[0]
    assert r0["kind"] == "pipeline"
    assert r0["source"] == "openshift"
    assert r0["ts"] == "2026-06-12T10:05:00Z"  # completionTimestamp preferred
    assert r0["level_or_status"] == "Complete"
    assert r0["message"] == "myapp-7"
    assert r0["labels"]["buildconfig"] == "myapp"
    assert r0["labels"]["namespace"] == NS
    assert set(r0.keys()) == {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}

    # Running build falls back to startTimestamp + reads label alias.
    r1 = records[1]
    assert r1["ts"] == "2026-06-12T11:00:00Z"
    assert r1["level_or_status"] == "Running"
    assert r1["labels"]["buildconfig"] == "myapp"

    # The request hit the build.openshift.io API path under the namespace.
    assert any("/apis/build.openshift.io/v1/namespaces/ci-builds/builds" in c["url"] for c in transport.calls)


# --------------------------------------------------------------------------- #
# Contract: pods -> records
# --------------------------------------------------------------------------- #


def test_query_pods_normalization() -> None:
    transport = RecordingTransport(routes={"/pods": FakeResponse(200, body=PODS_PAYLOAD)})
    src = make_source(transport=transport)
    records = src.query({"mode": "pods"})

    assert len(records) == 1
    r = records[0]
    assert r["kind"] == "logs"
    assert r["level_or_status"] == "Running"
    assert r["message"] == "web-abc"
    assert r["labels"]["node"] == "node-1"
    assert r["labels"]["namespace"] == NS
    assert any("/api/v1/namespaces/ci-builds/pods" in c["url"] for c in transport.calls)


# --------------------------------------------------------------------------- #
# Contract: events -> records
# --------------------------------------------------------------------------- #


def test_query_events_normalization() -> None:
    transport = RecordingTransport(routes={"/events": FakeResponse(200, body=EVENTS_PAYLOAD)})
    src = make_source(transport=transport)
    records = src.query({"mode": "events"})

    assert len(records) == 1
    r = records[0]
    assert r["kind"] == "logs"
    assert r["level_or_status"] == "Warning"
    assert r["message"] == "BackOff Back-off restarting failed container"
    assert r["labels"]["involvedObject.kind"] == "Pod"
    assert r["labels"]["involvedObject.name"] == "web-abc"
    assert r["labels"]["reason"] == "BackOff"
    assert r["ts"] == "2026-06-12T12:00:00Z"


# --------------------------------------------------------------------------- #
# Contract: logs -> per-line records
# --------------------------------------------------------------------------- #


def test_query_logs_per_line() -> None:
    transport = RecordingTransport(routes={"/log": FakeResponse(200, text=LOG_TEXT)})
    src = make_source(transport=transport)
    records = src.query({"mode": "logs", "pod": "web-abc", "container": "app", "tail_lines": 100})

    # Blank line is dropped; 3 content lines remain.
    assert [r["message"] for r in records] == ["line one", "line two", "line three"]
    assert all(r["kind"] == "logs" for r in records)
    assert records[0]["labels"]["pod"] == "web-abc"
    assert records[0]["labels"]["container"] == "app"
    assert records[0]["labels"]["namespace"] == NS

    url = transport.calls[-1]["url"]
    assert "/api/v1/namespaces/ci-builds/pods/web-abc/log" in url
    assert "container=app" in url
    assert "tailLines=100" in url


def test_logs_mode_requires_pod() -> None:
    src = make_source()
    with pytest.raises(OpenShiftConfigError):
        src.query({"mode": "logs"})


def test_unknown_mode_raises() -> None:
    src = make_source()
    with pytest.raises(OpenShiftConfigError):
        src.query({"mode": "nonsense"})


def test_non_200_yields_empty_records() -> None:
    transport = RecordingTransport(default=FakeResponse(403, body={"message": "forbidden"}))
    src = make_source(transport=transport)
    assert src.query({"mode": "pods"}) == []


# --------------------------------------------------------------------------- #
# Contract: health() ok / not-ok / never raises
# --------------------------------------------------------------------------- #


def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    transport = RecordingTransport(default=FakeResponse(200, body={"kind": "Namespace"}))
    src = make_source(transport=transport)
    result = src.health()
    assert result["ok"] is True
    assert isinstance(result["detail"], str)


def test_health_not_ok_on_bad_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    transport = RecordingTransport(default=FakeResponse(401, body={"message": "unauthorized"}))
    src = make_source(transport=transport)
    result = src.health()
    assert result["ok"] is False
    assert "unexpected status 401" in result["detail"]


def test_health_not_ok_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    src = make_source()
    result = src.health()
    assert result["ok"] is False
    assert TOKEN_ENV in result["detail"]


def test_health_never_raises_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    src = make_source(transport=BoomTransport())
    result = src.health()  # must not raise
    assert result["ok"] is False
    assert "request failed" in result["detail"]
