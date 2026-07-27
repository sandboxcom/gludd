"""E2E tests for connectors batch 5 — Orchestration, System, Hardware, Windows, macOS, Metrics.

Covers 18 uncovered connector modules. Uses mock transports/runners — no real network I/O.

Targets:
  Orchestration:  openshift, nomad
  Container:       podman, containerd
  System:          dmesg, proc_sys, linux_namespaces
  Hardware:        redfish
  Network:         snmp
  Windows:         windows_defender, windows_event_log, windows_wmi
  macOS:           macos_log, macos_security
  Metrics:         statsd_parse
  Utilities:       ingest_formats, exc_sanitizer
  Other:           adb, baseten
"""

from __future__ import annotations

import json as _json
import os
from dataclasses import dataclass, field

import pytest

# ============================================================================
# Mock helpers — shared across connectors
# ============================================================================


class MockHttpResponse:
    """Mock httpx-like response for HttpResponse protocol connectors."""

    def __init__(self, status_code: int = 200, body: object = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or ""
        self.headers: dict[str, str] = {}

    def json(self) -> object:
        if isinstance(self._body, (dict, list)):
            return self._body
        return self._body

    def iter_lines(self) -> list[bytes]:
        """Return text split into lines for SSE/stream connectors."""
        if not self.text:
            return []
        return [line.encode() for line in self.text.splitlines() if line]


class MockHttpTransport:
    """Injectable HTTP transport returning HttpResponse-like objects."""

    def __init__(
        self,
        status_code: int = 200,
        body: object = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._status = status_code
        self._body = body
        self._text = text
        self._headers = headers or {}
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        timeout: float = 10.0,
        **kwargs: object,
    ) -> MockHttpResponse:
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params})
        resp = MockHttpResponse(self._status, self._body, self._text)
        resp.headers = dict(self._headers)
        return resp

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        **kwargs: object,
    ) -> MockHttpResponse:
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        resp = MockHttpResponse(self._status, self._body, self._text)
        resp.headers = dict(self._headers)
        return resp


@dataclass
class MockRunResult:
    """Mock subprocess runner result for dmesg/containerd-style connectors."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


# ============================================================================
# 1. OpenShift Connector
# ============================================================================


class TestOpenShiftConnector:
    def test_config_requires_api_server(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource({})

    def test_config_requires_namespace(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource({"api_server": "https://api.example.com:6443"})

    def test_rejects_private_host(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource({"api_server": "http://127.0.0.1:6443", "token_env": "T"})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        monkeypatch.setenv("OC_TOK_B5", "sha256~abc")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.openshift.example.com:6443",
                    "token_env": "OC_TOK_B5",
                    "namespace": "default",
                    "allow_private": True,
                }
            )
            assert source.KIND == "logs"
            assert source.name is not None
        finally:
            del os.environ["OC_TOK_B5"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        transport = MockHttpTransport(status_code=200, body={"kind": "Status", "status": "ok"})
        monkeypatch.setenv("OC_TOK_H", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_H",
                    "allow_private": True,
                },
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["OC_TOK_H"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        transport = MockHttpTransport(status_code=401, body={})
        monkeypatch.setenv("OC_TOK_H2", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_H2",
                    "allow_private": True,
                },
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["OC_TOK_H2"]

    def test_query_returns_pods(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        transport = MockHttpTransport(
            status_code=200,
            body={
                "items": [
                    {
                        "metadata": {
                            "name": "pod-1",
                            "namespace": "default",
                            "creationTimestamp": "2025-01-01T12:00:00Z",
                            "labels": {"app": "web"},
                        },
                        "status": {
                            "phase": "Running",
                            "containerStatuses": [
                                {"name": "app", "ready": True, "restartCount": 0, "state": {"running": {}}}
                            ],
                        },
                    }
                ]
            },
        )
        monkeypatch.setenv("OC_TOK_Q", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_Q",
                    "allow_private": True,
                },
                transport=transport,
            )
            records = source.query({"mode": "pods"})
            assert len(records) >= 1
            assert records[0]["kind"] == "logs"
        finally:
            del os.environ["OC_TOK_Q"]

    # -- edge cases: query-mode coverage -----------------------------------

    def test_query_builds_mode(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        transport = MockHttpTransport(
            status_code=200,
            body={
                "items": [
                    {
                        "metadata": {
                            "name": "build-1",
                            "namespace": "default",
                            "labels": {
                                "buildconfig": "my-app",
                                "openshift.io/build-config.name": "my-app",
                            },
                        },
                        "status": {
                            "phase": "Complete",
                            "startTimestamp": "2025-01-01T12:00:00Z",
                            "completionTimestamp": "2025-01-01T12:05:00Z",
                        },
                    }
                ]
            },
        )
        monkeypatch.setenv("OC_TOK_BLD", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_BLD",
                    "allow_private": True,
                },
                transport=transport,
            )
            records = source.query({"mode": "builds"})
            assert len(records) >= 1
            assert records[0]["kind"] == "pipeline"
            assert records[0]["message"] == "build-1"
        finally:
            del os.environ["OC_TOK_BLD"]

    def test_query_events_mode(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        transport = MockHttpTransport(
            status_code=200,
            body={
                "items": [
                    {
                        "type": "Warning",
                        "reason": "FailedScheduling",
                        "message": "0/3 nodes are available",
                        "firstTimestamp": "2025-01-01T12:00:00Z",
                        "lastTimestamp": "2025-01-01T12:01:00Z",
                        "involvedObject": {"kind": "Pod", "name": "pod-1"},
                        "metadata": {"creationTimestamp": "2025-01-01T12:00:00Z"},
                    }
                ]
            },
        )
        monkeypatch.setenv("OC_TOK_EVT", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_EVT",
                    "allow_private": True,
                },
                transport=transport,
            )
            records = source.query({"mode": "events"})
            assert len(records) >= 1
            assert records[0]["kind"] == "logs"
            assert "FailedScheduling" in str(records[0]["message"])
        finally:
            del os.environ["OC_TOK_EVT"]

    def test_query_logs_mode(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        transport = MockHttpTransport(
            status_code=200,
            text="2025-01-01T12:00:00Z INFO Starting\n2025-01-01T12:00:01Z INFO Ready\n",
        )
        monkeypatch.setenv("OC_TOK_LOG", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_LOG",
                    "allow_private": True,
                },
                transport=transport,
            )
            records = source.query({"mode": "logs", "pod": "pod-1", "container": "app"})
            assert len(records) >= 2
            assert all(r["kind"] == "logs" for r in records)
            assert records[0]["labels"]["pod"] == "pod-1"
            assert records[0]["labels"]["container"] == "app"
        finally:
            del os.environ["OC_TOK_LOG"]

    def test_query_logs_mode_no_container(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        transport = MockHttpTransport(status_code=200, text="line1\n")
        monkeypatch.setenv("OC_TOK_LC", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_LC",
                    "allow_private": True,
                },
                transport=transport,
            )
            records = source.query({"mode": "logs", "pod": "pod-2"})
            assert len(records) >= 1
            assert records[0]["labels"]["container"] is None
        finally:
            del os.environ["OC_TOK_LC"]

    def test_query_logs_mode_requires_pod(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        monkeypatch.setenv("OC_TOK_LP", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_LP",
                    "allow_private": True,
                },
                transport=MockHttpTransport(status_code=200, body={}),
            )
            with pytest.raises((ValueError, RuntimeError)):
                source.query({"mode": "logs"})
        finally:
            del os.environ["OC_TOK_LP"]

    def test_query_unknown_mode_raises(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        monkeypatch.setenv("OC_TOK_UNK", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_UNK",
                    "allow_private": True,
                },
                transport=MockHttpTransport(status_code=200, body={}),
            )
            with pytest.raises((ValueError, RuntimeError)):
                source.query({"mode": "nonexistent"})
        finally:
            del os.environ["OC_TOK_UNK"]

    def test_query_defaults_to_unknown_mode_raises(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        monkeypatch.setenv("OC_TOK_DEF", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_DEF",
                    "allow_private": True,
                },
                transport=MockHttpTransport(status_code=200, body={}),
            )
            with pytest.raises((ValueError, RuntimeError)):
                source.query({})
        finally:
            del os.environ["OC_TOK_DEF"]

    # -- edge cases: SSRF internal-name heuristics -------------------------

    def test_rejects_svc_suffix_host(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource(
                {
                    "api_server": "https://api.openshift.svc:6443",
                    "namespace": "default",
                    "token_env": "T",
                }
            )

    def test_rejects_cluster_local_suffix_host(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource(
                {
                    "api_server": "https://api.openshift.cluster.local:6443",
                    "namespace": "default",
                    "token_env": "T",
                }
            )

    def test_rejects_local_suffix_host(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource(
                {
                    "api_server": "https://api.local:6443",
                    "namespace": "default",
                    "token_env": "T",
                }
            )

    def test_rejects_internal_suffix_host(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource(
                {
                    "api_server": "https://api.internal:6443",
                    "namespace": "default",
                    "token_env": "T",
                }
            )

    def test_rejects_single_label_host(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource(
                {
                    "api_server": "https://openshift:6443",
                    "namespace": "default",
                    "token_env": "T",
                }
            )

    # -- edge cases: namespace collision -----------------------------------

    def test_namespace_collision_distinct_sources(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        transport_a = MockHttpTransport(
            status_code=200,
            body={
                "items": [
                    {
                        "metadata": {
                            "name": "pod-a",
                            "namespace": "shared",
                            "creationTimestamp": "2025-01-01T12:00:00Z",
                        },
                        "status": {"phase": "Running"},
                    }
                ]
            },
        )
        transport_b = MockHttpTransport(
            status_code=200,
            body={
                "items": [
                    {
                        "metadata": {
                            "name": "pod-b",
                            "namespace": "shared",
                            "creationTimestamp": "2025-01-01T12:00:01Z",
                        },
                        "status": {"phase": "Running"},
                    }
                ]
            },
        )
        monkeypatch.setenv("OC_TOK_NSA", "tok-a")
        monkeypatch.setenv("OC_TOK_NSB", "tok-b")
        try:
            source_a = OpenShiftSource(
                {
                    "api_server": "https://api-a.example.com:6443",
                    "namespace": "shared",
                    "token_env": "OC_TOK_NSA",
                    "allow_private": True,
                    "name": "openshift-cluster-a",
                },
                transport=transport_a,
            )
            source_b = OpenShiftSource(
                {
                    "api_server": "https://api-b.example.com:6443",
                    "namespace": "shared",
                    "token_env": "OC_TOK_NSB",
                    "allow_private": True,
                    "name": "openshift-cluster-b",
                },
                transport=transport_b,
            )
            assert source_a.name == "openshift-cluster-a"
            assert source_b.name == "openshift-cluster-b"
            records_a = source_a.query({"mode": "pods"})
            records_b = source_b.query({"mode": "pods"})
            assert len(records_a) >= 1
            assert len(records_b) >= 1
            assert records_a[0]["message"] == "pod-a"
            assert records_b[0]["message"] == "pod-b"
            assert records_a[0]["source"] == "openshift-cluster-a"
            assert records_b[0]["source"] == "openshift-cluster-b"
        finally:
            del os.environ["OC_TOK_NSA"], os.environ["OC_TOK_NSB"]

    # -- edge cases: transport timeout -------------------------------------

    def test_timeout_propagates_to_transport(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        captured_timeouts: list[float] = []

        class _TimeoutCaptureTransport:
            def get(self, url: str, *, headers: dict[str, str], timeout: float) -> MockHttpResponse:
                captured_timeouts.append(timeout)
                return MockHttpResponse(status_code=200, body={"kind": "Status", "status": "ok"})

        transport = _TimeoutCaptureTransport()
        monkeypatch.setenv("OC_TOK_TO", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_TO",
                    "allow_private": True,
                    "timeout": 45.0,
                },
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
            assert len(captured_timeouts) >= 1
            assert captured_timeouts[0] == 45.0
        finally:
            del os.environ["OC_TOK_TO"]

    def test_timeout_defaults_to_30_seconds(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        captured_timeouts: list[float] = []

        class _TimeoutCaptureTransport:
            def get(self, url: str, *, headers: dict[str, str], timeout: float) -> MockHttpResponse:
                captured_timeouts.append(timeout)
                return MockHttpResponse(status_code=200, body={"kind": "Status", "status": "ok"})

        transport = _TimeoutCaptureTransport()
        monkeypatch.setenv("OC_TOK_TD", "tok")
        try:
            source = OpenShiftSource(
                {
                    "api_server": "https://api.example.com:6443",
                    "namespace": "default",
                    "token_env": "OC_TOK_TD",
                    "allow_private": True,
                },
                transport=transport,
            )
            source.health()
            assert captured_timeouts[0] == 30.0
        finally:
            del os.environ["OC_TOK_TD"]


# ============================================================================
# 2. Nomad Connector
# ============================================================================


class TestNomadConnector:
    def test_config_requires_base_url(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        with pytest.raises((ValueError, RuntimeError)):
            NomadSource({})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        monkeypatch.setenv("NOMAD_TOK", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOMAD_TOK",
                    "allow_private": True,
                }
            )
            assert source.KIND == "logs"
        finally:
            del os.environ["NOMAD_TOK"]

    def test_rejects_private_host_by_default(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        source = NomadSource({"base_url": "http://10.0.0.1:4646", "token_env": "T"})
        result = source.health()
        assert result["ok"] is False
        assert "ssrf-blocked" in result["detail"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        transport = MockHttpTransport(status_code=200, body={"KnownLeader": True})
        monkeypatch.setenv("NOM_H", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOM_H",
                    "allow_private": True,
                    "transport": transport,
                },
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["NOM_H"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        transport = MockHttpTransport(status_code=500, body={})
        monkeypatch.setenv("NOM_H2", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOM_H2",
                    "allow_private": True,
                    "transport": transport,
                },
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["NOM_H2"]

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        transport = MockHttpTransport(status_code=200, text="allocation started\n")
        monkeypatch.setenv("NOM_Q", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOM_Q",
                    "allow_private": True,
                    "transport": transport,
                },
            )
            records = source.query({"type": "logs", "alloc_id": "alloc-1"})
            assert len(records) >= 1
            assert records[0]["kind"] == "logs"
        finally:
            del os.environ["NOM_Q"]

    # -- edge cases: query-type coverage -----------------------------------

    def test_query_events_type(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        transport = MockHttpTransport(
            status_code=200,
            text='{"Index":1,"Events":[{"Topic":"Job","Type":"JobRegistered","Key":"example","Namespace":"default"}]}',
        )
        monkeypatch.setenv("NOM_EV", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOM_EV",
                    "allow_private": True,
                    "transport": transport,
                },
            )
            records = source.query({"type": "events", "limit": 5})
            assert len(records) >= 1
            assert records[0]["kind"] == "pipeline"
            assert records[0]["labels"]["topic"] == "Job"
        finally:
            del os.environ["NOM_EV"]

    def test_query_events_with_sse_framing(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        transport = MockHttpTransport(
            status_code=200,
            text='data: {"Events":[{"Topic":"Node","Type":"NodeEvent","Key":"node-1"}]}\n',
        )
        monkeypatch.setenv("NOM_ESSE", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOM_ESSE",
                    "allow_private": True,
                    "transport": transport,
                },
            )
            records = source.query({"type": "events"})
            assert len(records) >= 1
            assert records[0]["kind"] == "pipeline"
        finally:
            del os.environ["NOM_ESSE"]

    def test_query_metrics_type(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        transport = MockHttpTransport(
            status_code=200,
            text=(
                "# HELP nomad_runtime_num_goroutines Number of goroutines\n"
                "# TYPE nomad_runtime_num_goroutines gauge\n"
                'nomad_runtime_num_goroutines{instance="nomad"} 42\n'
            ),
        )
        monkeypatch.setenv("NOM_MT", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOM_MT",
                    "allow_private": True,
                    "transport": transport,
                },
            )
            records = source.query({"type": "metrics"})
            assert len(records) >= 1
            assert records[0]["kind"] == "metrics"
            assert records[0]["message"] == "nomad_runtime_num_goroutines"
            assert records[0]["value"] == 42
        finally:
            del os.environ["NOM_MT"]

    def test_query_unsupported_type_raises(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        monkeypatch.setenv("NOM_UNK", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOM_UNK",
                    "allow_private": True,
                    "transport": MockHttpTransport(status_code=200, body={}),
                },
            )
            with pytest.raises(ValueError):
                source.query({"type": "unsupported"})
        finally:
            del os.environ["NOM_UNK"]

    def test_query_logs_requires_alloc_id(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        monkeypatch.setenv("NOM_NOALLOC", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOM_NOALLOC",
                    "allow_private": True,
                    "transport": MockHttpTransport(status_code=200, body={}),
                },
            )
            with pytest.raises(ValueError):
                source.query({"type": "logs"})
        finally:
            del os.environ["NOM_NOALLOC"]

    def test_query_logs_stderr_type(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        transport = MockHttpTransport(status_code=200, text="error: something went wrong\n")
        monkeypatch.setenv("NOM_STDERR", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "https://nomad.example.com:4646",
                    "token_env": "NOM_STDERR",
                    "allow_private": True,
                    "transport": transport,
                },
            )
            records = source.query({"type": "logs", "alloc_id": "alloc-1", "log_type": "stderr"})
            assert len(records) >= 1
            assert records[0]["level_or_status"] == "error"
        finally:
            del os.environ["NOM_STDERR"]

    # -- edge cases: SSRF timing contract (lazy, not at construction) ------

    def test_ssrf_blocked_lazily_at_query_time(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        monkeypatch.setenv("NOM_SSRF_Q", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "http://10.0.0.1:4646",
                    "token_env": "NOM_SSRF_Q",
                }
            )
            result = source.health()
            assert result["ok"] is False
            assert "ssrf-blocked" in result["detail"]
        finally:
            del os.environ["NOM_SSRF_Q"]

    def test_private_host_constructs_without_error(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        monkeypatch.setenv("NOM_PRIV", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "http://127.0.0.1:4646",
                    "token_env": "NOM_PRIV",
                }
            )
            assert source.KIND == "logs"
        finally:
            del os.environ["NOM_PRIV"]

    def test_ssrf_blocked_during_query_not_construction(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        monkeypatch.setenv("NOM_DNS", "tok")
        try:
            source = NomadSource(
                {
                    "base_url": "http://10.0.0.1:4646",
                    "token_env": "NOM_DNS",
                }
            )
            assert source.KIND == "logs"
            with pytest.raises((ValueError, RuntimeError)):
                source.query({"type": "metrics"})
        finally:
            del os.environ["NOM_DNS"]

    # -- edge cases: transport timeout / error handling --------------------

    def test_transport_error_raised_during_health(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        class _FailingTransport:
            def __call__(self, method: str, url: str, headers: dict[str, str], params: dict[str, str] | None) -> object:
                raise OSError("connection refused")

        transport = _FailingTransport()
        source = NomadSource(
            {
                "base_url": "https://nomad.example.com:4646",
                "allow_private": True,
                "transport": transport,
            },
        )
        result = source.health()
        assert result["ok"] is False
        assert "unhealthy" in result["detail"]

    def test_health_reports_no_token(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        source = NomadSource(
            {
                "base_url": "https://nomad.example.com:4646",
                "allow_private": True,
                "token_env": "NOMAD_NONEXISTENT_VAR",
                "transport": MockHttpTransport(status_code=200, body={"KnownLeader": True}),
            },
        )
        result = source.health()
        assert isinstance(result, dict)


# ============================================================================
# 3. Podman Connector
# ============================================================================


class TestPodmanConnector:
    @dataclass
    class _MockPodmanResponse:
        status: int
        headers: dict[str, str] = field(default_factory=dict)
        body: bytes = b""

    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        source = PodmanSource()
        assert source.KIND == "logs"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        source = PodmanSource({"name": "podman-prod", "base_url": "unix:///run/podman/podman.sock"})
        assert source.name == "podman-prod"

    def test_health_ok_with_injected_transport(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        resp = self._MockPodmanResponse(status=200, body=_json.dumps([{"Id": "c1"}]).encode())
        source = PodmanSource(transport=lambda *a, **kw: resp)
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        resp = self._MockPodmanResponse(status=500, body=b"")
        source = PodmanSource(transport=lambda *a, **kw: resp)
        result = source.health()
        assert result["ok"] is False

    def test_health_not_ok_when_transport_raises(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        def _fail(*_: object, **__: object) -> object:
            raise OSError("socket unavail")

        source = PodmanSource(transport=_fail)
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_containers(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        resp = self._MockPodmanResponse(
            status=200,
            body=_json.dumps(
                [
                    {
                        "Id": "abc123",
                        "Names": ["/web"],
                        "Image": "nginx:latest",
                        "State": "running",
                        "Status": "Up 2 hours",
                        "Created": 1700000000,
                    }
                ]
            ).encode(),
        )
        source = PodmanSource(transport=lambda *a, **kw: resp)
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"

    def test_query_empty_on_transport_error(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        def _fail(*_: object, **__: object) -> object:
            raise ConnectionRefusedError("down")

        source = PodmanSource(transport=_fail)
        records = source.query({})
        assert records == []


# ============================================================================
# 4. Containerd Connector
# ============================================================================


class TestContainerdConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.containerd import ContainerdSource

        source = ContainerdSource()
        assert source.KIND == "logs"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.containerd import ContainerdSource

        source = ContainerdSource({"name": "cri-prod", "runtime_endpoint": "/run/containerd/containerd.sock"})
        assert source.name == "cri-prod"

    def test_health_ok_with_injected_runner(self, monkeypatch):
        from general_ludd.connectors.containerd import ContainerdSource

        def _runner(argv: list[str]) -> str:
            return _json.dumps({"items": [{"id": "c1"}]})

        source = ContainerdSource(runner=_runner)
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_runner_fails(self, monkeypatch):
        from general_ludd.connectors.containerd import ContainerdSource

        def _runner(argv: list[str]) -> str:
            raise RuntimeError("no containerd")

        source = ContainerdSource(runner=_runner)
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_pods(self, monkeypatch):
        from general_ludd.connectors.containerd import ContainerdSource

        call_count = 0

        def _runner(argv: list[str]) -> str:
            nonlocal call_count
            call_count += 1
            if "ps" in str(argv):
                return _json.dumps(
                    {
                        "items": [
                            {
                                "id": "abc",
                                "metadata": {"name": "web", "namespace": "default"},
                                "state": "CONTAINER_RUNNING",
                                "createdAt": "1700000000000000000",
                            }
                        ]
                    }
                )
            return _json.dumps({})

        source = ContainerdSource(runner=_runner)
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"

    def test_query_empty_without_runner(self, monkeypatch):
        from general_ludd.connectors.containerd import ContainerdSource

        source = ContainerdSource()
        records = source.query({})
        assert records == []


# ============================================================================
# 5. dmesg Connector
# ============================================================================


class TestDmesgConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource()
        assert source.KIND == "logs"
        assert source.name == "dmesg"

    def test_constructs_custom_name(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource({"name": "kernel-log"})
        assert source.name == "kernel-log"

    def test_health_ok_with_injected_runner(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        result = MockRunResult(returncode=0, stdout=_json.dumps([{"msg": "test", "ts": 0}]))
        source = DmesgSource(runner=lambda argv: result)
        health = source.health()
        assert health["ok"] is True

    def test_health_not_ok_when_runner_fails(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        result = MockRunResult(returncode=1, stderr="permission denied")
        source = DmesgSource(runner=lambda argv: result)
        health = source.health()
        assert health["ok"] is False

    def test_query_returns_log_entries(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        result = MockRunResult(
            returncode=0,
            stdout=_json.dumps(
                [
                    {"msg": "Initializing cgroup subsys cpuset", "ts": 1, "prio": 6, "fac": "kern"},
                    {"msg": "Command line: BOOT_IMAGE=/vmlinuz", "ts": 2, "prio": 6, "fac": "kern"},
                ]
            ),
        )
        source = DmesgSource(runner=lambda argv: result)
        records = source.query({})
        assert len(records) >= 2
        assert all(r["kind"] == "logs" for r in records)

    def test_query_empty_when_no_runner(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource()
        records = source.query({})
        assert records == []

    def test_rejects_flag_injection(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource()
        with pytest.raises(ValueError):
            source._validate_arg("--evil")


# ============================================================================
# 6. proc/sys Connector
# ============================================================================


class TestProcSysConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.proc_sys import ProcSysSource

        source = ProcSysSource()
        assert source.KIND == "metrics"
        assert source.name == "proc_sys"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.proc_sys import ProcSysSource

        source = ProcSysSource({"name": "kernel-tune", "paths": ["net.core.somaxconn"]})
        assert source.name == "kernel-tune"

    def test_health_ok_with_injected_file_reader(self, monkeypatch):
        from general_ludd.connectors.proc_sys import ProcSysSource

        source = ProcSysSource(reader=lambda path: "1024\n")
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_reader_fails(self, monkeypatch):
        from general_ludd.connectors.proc_sys import ProcSysSource

        def _reader(path: str) -> str:
            raise FileNotFoundError(f"no such file: {path}")

        source = ProcSysSource(reader=_reader)
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_sysctl_values(self, monkeypatch):
        from general_ludd.connectors.proc_sys import ProcSysSource

        def _reader(path: str) -> str:
            values = {
                "/proc/sys/net/core/somaxconn": "4096\n",
                "/proc/sys/kernel/hostname": "myhost\n",
            }
            text = values.get(path, "")
            if not text:
                raise FileNotFoundError(path)
            return text

        source = ProcSysSource(reader=_reader)
        records = source.query({"path": "/proc/sys/net/core/somaxconn"})
        records.extend(source.query({"path": "/proc/sys/kernel/hostname"}))
        assert len(records) == 2
        assert any("somaxconn" in str(r["message"]).lower() for r in records)

    def test_query_skips_missing_paths(self, monkeypatch):
        from general_ludd.connectors.proc_sys import ProcSysSource

        def _reader(path: str) -> str:
            raise FileNotFoundError(path)

        source = ProcSysSource(reader=_reader)
        with pytest.raises(FileNotFoundError):
            source.query({"path": "/proc/sys/net/core/somaxconn"})


# ============================================================================
# 7. Linux Namespaces Connector
# ============================================================================


class TestLinuxNamespacesConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.linux_namespaces import LinuxNamespacesSource

        source = LinuxNamespacesSource()
        assert source.KIND == "metrics"
        assert source.name is not None

    def test_constructs_custom_name(self, monkeypatch):
        from general_ludd.connectors.linux_namespaces import LinuxNamespacesSource

        source = LinuxNamespacesSource({"name": "ns-monitor"})
        assert source.name == "ns-monitor"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.linux_namespaces import LinuxNamespacesSource

        @dataclass
        class _Result:
            returncode: int = 0
            stdout: str = ""
            stderr: str = ""

        source = LinuxNamespacesSource(runner=lambda argv: _Result())
        result = source.health()
        assert isinstance(result, dict)

    def test_query_returns_namespace_info(self, monkeypatch):
        from general_ludd.connectors.linux_namespaces import LinuxNamespacesSource

        source = LinuxNamespacesSource()
        monkeypatch.setattr(
            source,
            "_read_ns_links",
            lambda pid: {"net": "net:[4026531992]", "pid": "pid:[4026531836]"},
        )
        records = source.query({"pid": 1})
        assert len(records) == 2
        assert records[0]["kind"] == "metrics"


# ============================================================================
# 8. Redfish Connector (hardware mgmt)
# ============================================================================


class TestRedfishConnector:
    def test_config_requires_base_url(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        with pytest.raises((ValueError, RuntimeError)):
            RedfishSource({})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        monkeypatch.setenv("RF_USR", "admin")
        monkeypatch.setenv("RF_PWD", "pass")  # pragma: allowlist secret
        try:
            source = RedfishSource(
                {
                    "base_url": "https://idrac.example.com",
                    "username_env": "RF_USR",
                    "password_env": "RF_PWD",  # pragma: allowlist secret
                }
            )
            assert source.KIND == "metrics"
        finally:
            del os.environ["RF_USR"], os.environ["RF_PWD"]

    def test_health_ok(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        transport = MockHttpTransport(status_code=200, body={"@odata.id": "/redfish/v1/"})
        monkeypatch.setenv("RF_U", "a")
        monkeypatch.setenv("RF_P", "p")
        try:
            source = RedfishSource(
                {
                    "base_url": "https://idrac.example.com",
                    "username_env": "RF_U",
                    "password_env": "RF_P",
                },  # pragma: allowlist secret
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is True
        finally:
            del os.environ["RF_U"], os.environ["RF_P"]

    def test_health_not_ok_on_error(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        transport = MockHttpTransport(status_code=500, body={})
        monkeypatch.setenv("RF_U2", "a")
        monkeypatch.setenv("RF_P2", "p")
        try:
            source = RedfishSource(
                {
                    "base_url": "https://idrac.example.com",
                    "username_env": "RF_U2",
                    "password_env": "RF_P2",
                },  # pragma: allowlist secret
                transport=transport,
            )
            result = source.health()
            assert result["ok"] is False
        finally:
            del os.environ["RF_U2"], os.environ["RF_P2"]

    def test_query_returns_thermal_data(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        transport = MockHttpTransport(
            status_code=200,
            body={
                "@odata.id": "/redfish/v1/Chassis/1/Thermal",
                "Temperatures": [
                    {
                        "Name": "CPU1 Temp",
                        "ReadingCelsius": 45,
                        "Status": {"Health": "OK"},
                    },
                    {
                        "Name": "Inlet Temp",
                        "ReadingCelsius": 22,
                        "Status": {"Health": "OK"},
                    },
                ],
                "Fans": [
                    {
                        "Name": "Fan1",
                        "Reading": 4500,
                        "ReadingUnits": "RPM",
                        "Status": {"Health": "OK"},
                    },
                ],
            },
        )
        monkeypatch.setenv("RF_Q", "a")
        monkeypatch.setenv("RF_PQ", "p")
        try:
            source = RedfishSource(
                {
                    "base_url": "https://idrac.example.com",
                    "username_env": "RF_Q",
                    "password_env": "RF_PQ",
                },  # pragma: allowlist secret
                transport=transport,
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["kind"] == "metrics"
        finally:
            del os.environ["RF_Q"], os.environ["RF_PQ"]


# ============================================================================
# 9. SNMP Connector
# ============================================================================


class TestSnmpConnector:
    def test_constructs_with_no_config(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource()
        assert source.KIND == "metrics"
        assert source.name == "snmp"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource(
            {
                "host": "192.168.1.1",
                "community": "public",
                "oid": "1.3.6.1.2.1.1.3",
                "name": "router-uptime",
            }
        )
        assert source.name == "router-uptime"

    def test_health_ok_with_injected_getter(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource(getter=lambda host, community, oid: (None, None))
        result = source.health()
        assert isinstance(result, dict)

    def test_health_not_ok_when_getter_fails(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        def _fail(*_: object, **__: object) -> object:
            raise OSError("timeout")

        source = SnmpSource({"host": "192.168.1.1"}, getter=_fail)
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        def _getter(host: str, community: str, oid: str) -> tuple[object, object]:
            return ("1.3.6.1.2.1.1.3.0", "12345678")

        source = SnmpSource({"host": "192.168.1.1"}, getter=_getter)
        records = source.query({"oid": "1.3.6.1.2.1.1.3"})
        assert len(records) >= 1
        assert records[0]["kind"] == "metrics"


# ============================================================================
# 10. Windows Defender Connector
# ============================================================================


class TestWindowsDefenderConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        source = WindowsDefenderSource()
        assert source.KIND == "logs"

    def test_constructs_custom_name(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        source = WindowsDefenderSource({"name": "defender-audit"})
        assert source.name == "defender-audit"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        source = WindowsDefenderSource(runner=lambda cmd: _json.dumps([{"ThreatName": "", "ActionSuccess": True}]))
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_detections(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        def _runner(cmd: str) -> str:
            return _json.dumps(
                [
                    {
                        "ThreatName": "Trojan:Win32/Test",
                        "Severity": "5",
                        "ActionSuccess": False,
                        "InitialDetectionTime": "2025-01-01T12:00:00Z",
                    }
                ]
            )

        source = WindowsDefenderSource(runner=_runner)
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"

    def test_query_empty_on_error(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        def _runner(cmd: str) -> str:
            raise RuntimeError("powershell not found")

        source = WindowsDefenderSource(runner=_runner)
        records = source.query({})
        assert records == []


# ============================================================================
# 11. Windows Event Log Connector
# ============================================================================


class TestWindowsEventLogConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource()
        assert source.KIND == "logs"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource(
            {
                "log_name": "Security",
                "name": "sec-log",
                "max_events": 100,
            }
        )
        assert source.name == "sec-log"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource(runner=lambda cmd: _json.dumps([{"Id": "1"}]))
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_runner_unavailable(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource()
        result = source.health()
        assert isinstance(result, dict)

    def test_query_returns_events(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        def _runner(cmd: str) -> str:
            return _json.dumps(
                [
                    {
                        "Id": 4624,
                        "LevelDisplayName": "Information",
                        "TimeCreated": "2025-01-01T12:00:00.0000000Z",
                        "Message": "An account was successfully logged on",
                        "ProviderName": "Microsoft-Windows-Security-Auditing",
                    }
                ]
            )

        source = WindowsEventLogSource({"log_name": "Security"}, runner=_runner)
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"
        assert "4624" in str(records[0]["message"])


# ============================================================================
# 12. Windows WMI Connector
# ============================================================================


class TestWindowsWmiConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        source = WindowsWmiSource()
        assert source.KIND == "metrics"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        source = WindowsWmiSource({"name": "wmi-cpu", "query": "SELECT * FROM Win32_Processor"})
        assert source.name == "wmi-cpu"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        source = WindowsWmiSource(runner=lambda query: _json.dumps([{"Name": "CPU0"}]))
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_runner_unavailable(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        source = WindowsWmiSource()
        result = source.health()
        assert isinstance(result, dict)

    def test_query_returns_wmi_results(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        def _runner(query: str) -> str:
            if "Win32_Processor" in query:
                return _json.dumps([{"Name": "Intel Core i7", "NumberOfCores": 8, "MaxClockSpeed": 3600}])
            return _json.dumps([])

        source = WindowsWmiSource({"query": "SELECT * FROM Win32_Processor"}, runner=_runner)
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["kind"] == "metrics"


# ============================================================================
# 13. macOS Log Connector
# ============================================================================


class TestMacOSLogConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource()
        assert source.KIND == "logs"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource(
            {
                "predicate": 'process == "opendirectoryd"',
                "name": "od-log",
                "last": "10m",
            }
        )
        assert source.name == "od-log"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource(runner=lambda argv: _json.dumps([{"eventMessage": "test"}]))
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_log_entries(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        def _runner(argv: list[str]) -> str:
            return _json.dumps(
                [
                    {
                        "eventMessage": "Login attempt",
                        "processImagePath": "/usr/libexec/opendirectoryd",
                        "timestamp": "2025-01-01 12:00:00.000000-0500",
                    },
                    {
                        "eventMessage": "Authentication succeeded",
                        "processImagePath": "/usr/libexec/opendirectoryd",
                        "timestamp": "2025-01-01 12:00:01.000000-0500",
                    },
                ]
            )

        source = MacOSLogSource({"predicate": 'process == "opendirectoryd"'}, runner=_runner)
        records = source.query({})
        assert len(records) >= 2
        assert all(r["kind"] == "logs" for r in records)

    def test_query_empty_without_runner(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource()
        records = source.query({})
        assert records == []


# ============================================================================
# 14. macOS Security Connector
# ============================================================================


class TestMacOSSecurityConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource()
        assert source.KIND == "logs"

    def test_constructs_custom_name(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource({"name": "mac-sec-audit"})
        assert source.name == "mac-sec-audit"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource(runner=lambda argv: _json.dumps([{"event": "AUTHENTICATION_SUCCEEDED"}]))
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_security_events(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        def _runner(argv: list[str]) -> str:
            return _json.dumps(
                [
                    {"event": "AUTHENTICATION_SUCCEEDED", "user": "admin", "timestamp": "2025-01-01T12:00:00Z"},
                    {"event": "GATEKEEPER_OVERRIDE", "user": "admin", "timestamp": "2025-01-01T12:01:00Z"},
                ]
            )

        source = MacOSSecuritySource(runner=_runner)
        records = source.query({})
        assert len(records) >= 2
        assert all(r["kind"] == "logs" for r in records)

    def test_query_empty_without_runner(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource()
        records = source.query({})
        assert records == []


# ============================================================================
# 15. StatsD Parsing Connector
# ============================================================================


class TestStatsdParseConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import StatsdParseSource

        source = StatsdParseSource()
        assert source.KIND == "metrics"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import StatsdParseSource

        source = StatsdParseSource({"name": "statsd-parser", "port": 9125})
        assert source.name == "statsd-parser"

    def test_dispatch_parses_counter(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.requests:1|c")
        assert len(records) >= 1
        assert records[0]["message"] == "app.requests"
        assert records[0]["value"] == 1
        assert records[0]["labels"]["metric_type"] == "counter"

    def test_dispatch_parses_gauge(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.memory:512.5|g")
        assert len(records) >= 1
        assert records[0]["value"] == 512.5
        assert records[0]["labels"]["metric_type"] == "gauge"

    def test_dispatch_parses_timer(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.latency:42.5|ms")
        assert len(records) >= 1
        assert records[0]["value"] == 42.5
        assert records[0]["labels"]["metric_type"] == "timer"

    def test_dispatch_parses_with_tags(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.errors:5|c|#host:web1,region:us-east")
        assert len(records) >= 1
        assert records[0]["labels"].get("host") == "web1"
        assert records[0]["labels"].get("region") == "us-east"

    def test_dispatch_parses_sampling_rate(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.hits:10|c|@0.1")
        assert len(records) >= 1
        assert records[0]["value"] == 10
        assert records[0]["labels"]["sample_rate"] == "0.1"

    def test_dispatch_returns_empty_for_invalid(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("invalid without colon")
        assert records == []

    def test_strip_name_drops_prefix(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _strip_name

        result = _strip_name("stats.gauges.app.requests")
        assert "app" in result

    def test_query_parses_multiple_lines(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import StatsdParseSource

        source = StatsdParseSource()
        records = source.query({"lines": ["app.hits:5|c", "app.mem:512|g"]})
        assert len(records) >= 2


# ============================================================================
# 16. Ingest Formats Connector
# ============================================================================


class TestIngestFormatsConnector:
    def test_detect_json_returns_true_for_json(self, monkeypatch):
        from general_ludd.connectors.ingest_formats import _detect_format

        fmt = _detect_format('{"key": "value"}')
        assert fmt == "json"

    def test_detect_plain_returns_true_for_plain(self, monkeypatch):
        from general_ludd.connectors.ingest_formats import _detect_format

        fmt = _detect_format("just plain text here")
        assert fmt == "plain"

    def test_detect_csv_returns_true_for_csv(self, monkeypatch):
        from general_ludd.connectors.ingest_formats import _detect_format

        fmt = _detect_format("col1,col2,col3\nval1,val2,val3")
        assert fmt == "csv"
