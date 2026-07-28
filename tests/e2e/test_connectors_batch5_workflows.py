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
    def test_config_requires_api_server(self):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource({})

    def test_config_requires_namespace(self):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource({
                "api_server": "https://api.example.com:6443",
                "token_env": "OPENSHIFT_TOKEN",
            })

    def test_rejects_private_host(self):
        from general_ludd.connectors.openshift import OpenShiftSource

        with pytest.raises((ValueError, RuntimeError)):
            OpenShiftSource({"api_server": "http://127.0.0.1:6443", "token_env": "T"})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.openshift import OpenShiftSource

        monkeypatch.setenv("OC_TOK_B5", "sha256~abc")
        try:
            source = OpenShiftSource({
                "api_server": "https://api.openshift.example.com:6443",
                "token_env": "OC_TOK_B5",
                "namespace": "default",
                "allow_private": True,
            })
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
                    "token_env": "OC_TOK_H",
                    "namespace": "default",
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
                    "token_env": "OC_TOK_H2",
                    "namespace": "default",
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
                    "token_env": "OC_TOK_Q",
                    "namespace": "default",
                    "allow_private": True,
                },
                transport=transport,
            )
            records = source.query({"mode": "pods"})
            assert len(records) >= 1
            assert records[0]["kind"] == "logs"
        finally:
            del os.environ["OC_TOK_Q"]


# ============================================================================
# 2. Nomad Connector
# ============================================================================


class TestNomadConnector:
    def test_config_requires_base_url(self):
        from general_ludd.connectors.nomad import NomadSource

        with pytest.raises((ValueError, RuntimeError)):
            NomadSource({})

    def test_constructs_with_valid_config(self, monkeypatch):
        from general_ludd.connectors.nomad import NomadSource

        monkeypatch.setenv("NOMAD_TOK", "tok")
        try:
            source = NomadSource({
                "base_url": "https://nomad.example.com:4646",
                "token_env": "NOMAD_TOK",
                "allow_private": True,
            })
            assert source.KIND == "logs"
        finally:
            del os.environ["NOMAD_TOK"]

    def test_rejects_private_host_by_default(self):
        from general_ludd.connectors.nomad import NomadSource

        with pytest.raises((ValueError, RuntimeError)):
            NomadSource({"base_url": "http://10.0.0.1:4646", "token_env": "T"})

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

        transport = MockHttpTransport(
            status_code=200,
            text="web task ready\n",
        )
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
            assert len(records) == 1
            assert records[0]["kind"] == "logs"
            assert records[0]["message"] == "web task ready"
            assert records[0]["labels"]["alloc_id"] == "alloc-1"
        finally:
            del os.environ["NOM_Q"]


# ============================================================================
# 3. Podman Connector
# ============================================================================


class TestPodmanConnector:
    @dataclass
    class _MockPodmanResponse:
        status: int
        headers: dict[str, str] = field(default_factory=dict)
        body: bytes = b""

    def test_constructs_with_defaults(self):
        from general_ludd.connectors.podman import PodmanSource

        source = PodmanSource()
        assert source.KIND == "logs"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.podman import PodmanSource

        source = PodmanSource({"name": "podman-prod", "base_url": "unix:///run/podman/podman.sock"})
        assert source.name == "podman-prod"

    def test_health_ok_with_injected_transport(self):
        from general_ludd.connectors.podman import PodmanSource

        resp = self._MockPodmanResponse(status=200, body=_json.dumps([{"Id": "c1"}]).encode())
        source = PodmanSource({"transport": lambda *a, **kw: resp})
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_error(self):
        from general_ludd.connectors.podman import PodmanSource

        resp = self._MockPodmanResponse(status=500, body=b"")
        source = PodmanSource({"transport": lambda *a, **kw: resp})
        result = source.health()
        assert result["ok"] is False

    def test_health_not_ok_when_transport_raises(self):
        from general_ludd.connectors.podman import PodmanSource

        def _fail(*_: object, **__: object) -> object:
            raise OSError("socket unavail")

        source = PodmanSource({"transport": _fail})
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_containers(self):
        from general_ludd.connectors.podman import PodmanSource

        resp = self._MockPodmanResponse(
            status=200,
            body=_json.dumps([
                {
                    "Id": "abc123",
                    "Names": ["/web"],
                    "Image": "nginx:latest",
                    "State": "running",
                    "Status": "Up 2 hours",
                    "Created": 1700000000,
                }
            ]).encode(),
        )
        source = PodmanSource({"transport": lambda *a, **kw: resp})
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"

    def test_query_propagates_transport_error(self):
        from general_ludd.connectors.podman import PodmanSource

        def _fail(*_: object, **__: object) -> object:
            raise ConnectionRefusedError("down")

        source = PodmanSource({"transport": _fail})
        with pytest.raises(ConnectionRefusedError, match="down"):
            source.query({})


# ============================================================================
# 4. Containerd Connector
# ============================================================================


class TestContainerdConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.containerd import ContainerdSource

        source = ContainerdSource()
        assert source.KIND == "logs"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.containerd import ContainerdSource

        source = ContainerdSource({"runtime_endpoint": "/var/run/containerd/custom.sock"})
        assert source.name == "containerd"
        assert source.config.runtime_endpoint == "/var/run/containerd/custom.sock"

    def test_health_ok_with_injected_runner(self):
        from general_ludd.connectors.containerd import ContainerdSource

        def _runner(argv: list[str], *, timeout: float) -> str:
            assert argv == [
                "crictl",
                "--runtime-endpoint",
                "unix:///run/containerd/containerd.sock",
                "version",
                "-o",
                "json",
            ]
            assert timeout == 10.0
            return _json.dumps({"items": [{"id": "c1"}]})

        source = ContainerdSource(runner=_runner)
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_runner_fails(self):
        from general_ludd.connectors.containerd import ContainerdSource

        def _runner(argv: list[str], *, timeout: float) -> str:
            raise RuntimeError("no containerd")

        source = ContainerdSource(runner=_runner)
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_containers(self):
        from general_ludd.connectors.containerd import ContainerdSource

        call_count = 0

        def _runner(argv: list[str], *, timeout: float) -> str:
            nonlocal call_count
            call_count += 1
            assert argv[-4:] == ["ps", "-a", "-o", "json"]
            assert timeout == 10.0
            return _json.dumps({
                "containers": [{
                    "id": "abc",
                    "metadata": {"name": "web"},
                    "labels": {
                        "io.kubernetes.pod.name": "web-pod",
                        "io.kubernetes.pod.namespace": "default",
                    },
                    "state": "CONTAINER_RUNNING",
                    "createdAt": 1700000000,
                }]
            })

        source = ContainerdSource(runner=_runner)
        records = source.query({"what": "ps"})
        assert call_count == 1
        assert len(records) == 1
        assert records[0]["kind"] == "logs"
        assert records[0]["message"] == "container web state=CONTAINER_RUNNING"
        assert records[0]["labels"]["pod"] == "web-pod"

    def test_query_empty_without_runner(self):
        from general_ludd.connectors.containerd import ContainerdSource

        source = ContainerdSource()
        records = source.query({})
        assert records == []


# ============================================================================
# 5. dmesg Connector
# ============================================================================


class TestDmesgConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource()
        assert source.KIND == "logs"
        assert source.name == "dmesg"

    def test_constructs_custom_name(self):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource({"name": "kernel-log"})
        assert source.name == "kernel-log"

    def test_health_ok_with_injected_runner(self):
        from general_ludd.connectors.dmesg import DmesgSource

        result = MockRunResult(returncode=0, stdout=_json.dumps([{"msg": "test", "ts": 0}]))
        source = DmesgSource(runner=lambda argv: result)
        health = source.health()
        assert health["ok"] is True

    def test_health_not_ok_when_runner_fails(self):
        from general_ludd.connectors.dmesg import DmesgSource

        result = MockRunResult(returncode=1, stderr="permission denied")
        source = DmesgSource(runner=lambda argv: result)
        health = source.health()
        assert health["ok"] is False

    def test_query_returns_log_entries(self):
        from general_ludd.connectors.dmesg import DmesgSource

        calls: list[list[str]] = []
        result = MockRunResult(
            returncode=0,
            stdout=_json.dumps({
                "dmesg": [
                    {
                        "msg": "Initializing cgroup subsys cpuset",
                        "timestamp": {"usec": 1},
                        "priority": 6,
                        "facility": "kern",
                    },
                    {
                        "msg": "Command line: BOOT_IMAGE=/vmlinuz",
                        "timestamp": {"usec": 2},
                        "priority": 6,
                        "facility": "kern",
                    },
                ]
            }),
        )

        def _runner(argv: list[str]) -> MockRunResult:
            calls.append(argv)
            return result

        source = DmesgSource(runner=_runner)
        records = source.query({})
        assert calls == [["dmesg", "--json"]]
        assert len(records) == 2
        assert all(r["kind"] == "logs" for r in records)
        assert records[0]["message"] == "Initializing cgroup subsys cpuset"
        assert records[0]["level_or_status"] == "info"
        assert records[0]["labels"]["facility"] == "kern"

    def test_query_requires_injected_runner(self):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource()
        with pytest.raises(RuntimeError, match="no runner injected"):
            source.query({})

    def test_rejects_flag_injection(self):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource()
        with pytest.raises(ValueError):
            source._validate_arg("--evil")


# ============================================================================
# 6. proc/sys Connector
# ============================================================================


class TestProcSysConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.proc_sys import ProcSysSource

        source = ProcSysSource()
        assert source.KIND == "metrics"
        assert source.name == "proc_sys"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.proc_sys import ProcSysSource

        source = ProcSysSource({"name": "kernel-tune"})
        assert source.name == "kernel-tune"

    def test_health_ok_with_injected_reader(self):
        from general_ludd.connectors.proc_sys import ProcSysSource

        source = ProcSysSource(reader=lambda path: "0.10 0.20 0.30 1/100 123\n")
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_reader_fails(self):
        from general_ludd.connectors.proc_sys import ProcSysSource

        def _reader(path: str) -> str:
            raise FileNotFoundError(f"no such file: {path}")

        source = ProcSysSource(reader=_reader)
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_sysctl_values(self):
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
        records = [
            *source.query({"path": "/proc/sys/net/core/somaxconn"}),
            *source.query({"path": "/proc/sys/kernel/hostname"}),
        ]
        assert len(records) == 2
        assert records[0]["message"] == "somaxconn"
        assert records[0]["value"] == 4096
        assert records[1]["message"] == "myhost"

    def test_query_propagates_missing_path(self):
        from general_ludd.connectors.proc_sys import ProcSysSource

        def _reader(path: str) -> str:
            raise FileNotFoundError(path)

        source = ProcSysSource(reader=_reader)
        with pytest.raises(FileNotFoundError, match="somaxconn"):
            source.query({"path": "/proc/sys/net/core/somaxconn"})


# ============================================================================
# 7. Linux Namespaces Connector
# ============================================================================


class TestLinuxNamespacesConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.linux_namespaces import LinuxNamespacesSource

        source = LinuxNamespacesSource()
        assert source.KIND == "metrics"
        assert source.name == "linux_namespaces"

    def test_constructs_custom_name(self):
        from general_ludd.connectors.linux_namespaces import LinuxNamespacesSource

        source = LinuxNamespacesSource({"name": "ns-monitor"})
        assert source.name == "ns-monitor"

    def test_health_ok_with_proc_namespace(self, monkeypatch):
        from general_ludd.connectors import linux_namespaces as namespaces_mod
        from general_ludd.connectors.linux_namespaces import LinuxNamespacesSource

        paths: list[str] = []

        def _readlink(path: str) -> str:
            paths.append(path)
            return "pid:[4026531836]"

        monkeypatch.setattr(namespaces_mod.os, "readlink", _readlink)
        source = LinuxNamespacesSource()
        result = source.health()
        assert result == {
            "ok": True,
            "detail": "namespace support confirmed via /proc",
        }
        assert paths == ["/proc/self/ns/pid"]

    def test_query_returns_namespace_info(self, monkeypatch):
        from general_ludd.connectors.linux_namespaces import LinuxNamespacesSource

        listed: list[str] = []
        linked: list[str] = []

        def _list_dir(path: str) -> list[str]:
            listed.append(path)
            return ["net", "pid"]

        def _readlink(path: str) -> str:
            linked.append(path)
            return {
                "/proc/1/ns/net": "net:[4026531992]",
                "/proc/1/ns/pid": "pid:[4026531836]",
            }[path]

        monkeypatch.setattr(
            LinuxNamespacesSource,
            "_list_dir",
            staticmethod(_list_dir),
        )
        monkeypatch.setattr(
            LinuxNamespacesSource,
            "_readlink",
            staticmethod(_readlink),
        )

        source = LinuxNamespacesSource()
        records = source.query({"target": "namespaces", "pid": 1})
        assert listed == ["/proc/1/ns"]
        assert linked == ["/proc/1/ns/net", "/proc/1/ns/pid"]
        assert len(records) == 2
        assert [record["labels"]["ns_type"] for record in records] == [
            "net",
            "pid",
        ]
        assert records[0]["labels"]["target"] == "net:[4026531992]"
        assert all(record["kind"] == "metrics" for record in records)


# ============================================================================
# 8. Redfish Connector (hardware mgmt)
# ============================================================================


class TestRedfishConnector:
    def test_default_config_is_fail_closed(self):
        from general_ludd.connectors.redfish import RedfishSource

        source = RedfishSource()
        assert source.config.base_url == "https://127.0.0.1"
        assert source.health() == {
            "ok": False,
            "detail": "host validation failed",
        }

    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.redfish import RedfishSource

        source = RedfishSource({
            "base_url": "https://idrac.example.com",
            "username_env": "RF_USR",
            "password_env": "RF_PWD",  # pragma: allowlist secret
        })
        assert source.KIND == "metrics"
        assert source.name == "redfish"
        assert source.config.base_url == "https://idrac.example.com"

    def test_health_ok(self):
        from general_ludd.connectors.redfish import RedfishSource, TransportResponse

        calls: list[dict[str, object]] = []

        def _transport(
            url: str,
            *,
            headers: dict[str, str],
            timeout: float,
            verify: bool,
        ) -> TransportResponse:
            calls.append({
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "verify": verify,
            })
            return TransportResponse(200, _json.dumps({"@odata.id": "/redfish/v1/"}))

        source = RedfishSource(
            {
                "base_url": "https://idrac.example.com",
                "username_env": "RF_U",
                "password_env": "RF_P",
            },  # pragma: allowlist secret
            transport=_transport,
            env={"RF_U": "a", "RF_P": "p"},  # pragma: allowlist secret
        )
        result = source.health()
        assert result == {"ok": True, "detail": "service root 200"}
        assert calls[0]["url"] == "https://idrac.example.com/redfish/v1/"
        assert calls[0]["timeout"] == 10.0
        assert calls[0]["verify"] is True
        assert "Authorization" in calls[0]["headers"]

    def test_health_not_ok_on_error(self):
        from general_ludd.connectors.redfish import RedfishSource, TransportResponse

        source = RedfishSource(
            {
                "base_url": "https://idrac.example.com",
                "username_env": "RF_U2",
                "password_env": "RF_P2",
            },  # pragma: allowlist secret
            transport=lambda *args, **kwargs: TransportResponse(500, "{}"),
            env={"RF_U2": "a", "RF_P2": "p"},  # pragma: allowlist secret
        )
        result = source.health()
        assert result == {"ok": False, "detail": "service root HTTP 500"}

    def test_query_returns_thermal_data(self):
        from general_ludd.connectors.redfish import RedfishSource, TransportResponse

        calls: list[str] = []
        body = {
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
            }

        def _transport(url: str, **kwargs: object) -> TransportResponse:
            calls.append(url)
            return TransportResponse(200, _json.dumps(body))

        source = RedfishSource(
            {
                "base_url": "https://idrac.example.com",
                "username_env": "RF_Q",
                "password_env": "RF_PQ",
            },  # pragma: allowlist secret
            transport=_transport,
            env={"RF_Q": "a", "RF_PQ": "p"},  # pragma: allowlist secret
        )
        records = source.query({"what": "thermal"})
        assert calls == ["https://idrac.example.com/redfish/v1/Chassis/1/Thermal"]
        assert len(records) == 3
        assert all(record["kind"] == "metrics" for record in records)
        assert [record["value"] for record in records] == [45.0, 22.0, 4500.0]


# ============================================================================
# 9. SNMP Connector
# ============================================================================


class TestSnmpConnector:
    def test_constructs_with_empty_config(self):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource({})
        assert source.KIND == "metrics"
        assert source.name == "snmp"
        assert source.health() == {
            "ok": False,
            "detail": "snmp mode requires host",
        }

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        monkeypatch.setenv("SNMP_E2E_COMMUNITY", "private-community")
        source = SnmpSource({
            "host": "router.example",
            "community_env": "SNMP_E2E_COMMUNITY",
            "oids": ["1.3.6.1.2.1.1.3.0"],
            "name": "router-uptime",
        })
        assert source.name == "router-uptime"
        assert source.host == "router.example"
        assert source.oids == ["1.3.6.1.2.1.1.3.0"]

    def test_health_ok_with_injected_getter(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        calls: list[tuple[str, int, str, list[str], float]] = []
        monkeypatch.setenv("SNMP_E2E_COMMUNITY", "private-community")

        def _getter(
            host: str,
            port: int,
            community: str,
            oids: list[str],
            timeout: float,
        ) -> list[tuple[str, object]]:
            calls.append((host, port, community, oids, timeout))
            return []

        source = SnmpSource(
            {
                "host": "router.example",
                "community_env": "SNMP_E2E_COMMUNITY",
            },
            getter=_getter,
        )
        result = source.health()
        assert result == {"ok": True, "detail": "snmp getter reachable"}
        assert calls == [
            ("router.example", 161, "private-community", [], 5.0)
        ]

    def test_health_not_ok_when_getter_fails(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        community = "private-community"
        monkeypatch.setenv("SNMP_E2E_COMMUNITY", community)

        def _fail(*_: object, **__: object) -> object:
            raise OSError(f"timeout for {community}")

        source = SnmpSource(
            {
                "host": "router.example",
                "community_env": "SNMP_E2E_COMMUNITY",
            },
            getter=_fail,
        )
        result = source.health()
        assert result["ok"] is False
        assert community not in result["detail"]
        assert "***redacted***" in result["detail"]

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.snmp import COMMUNITY_REDACTED, SnmpSource

        calls: list[tuple[str, int, str, list[str], float]] = []
        community = "private-community"
        oid = "1.3.6.1.2.1.1.3.0"
        monkeypatch.setenv("SNMP_E2E_COMMUNITY", community)

        def _getter(
            host: str,
            port: int,
            secret: str,
            oids: list[str],
            timeout: float,
        ) -> list[tuple[str, object]]:
            calls.append((host, port, secret, oids, timeout))
            return [(oid, "12345678")]

        source = SnmpSource(
            {
                "host": "router.example",
                "community_env": "SNMP_E2E_COMMUNITY",
            },
            getter=_getter,
        )
        records = source.query({"oids": [oid]})
        assert calls == [
            ("router.example", 161, community, [oid], 5.0)
        ]
        assert len(records) == 1
        assert records[0]["kind"] == "metrics"
        assert records[0]["value"] == 12345678.0
        assert records[0]["labels"]["community"] == COMMUNITY_REDACTED
        assert records[0]["raw"]["community"] == COMMUNITY_REDACTED
        assert community not in repr(records)


# ============================================================================
# 10. Windows Defender Connector
# ============================================================================


class TestWindowsDefenderConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        source = WindowsDefenderSource()
        assert source.KIND == "logs"

    def test_constructs_custom_name(self):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        source = WindowsDefenderSource({"name": "defender-audit"})
        assert source.name == "defender-audit"

    def test_health_ok_with_runner(self):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        calls: list[list[str]] = []

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return (
                0,
                _json.dumps([{
                    "AntivirusEnabled": True,
                    "AMServiceEnabled": True,
                    "AntispywareEnabled": True,
                    "RealTimeProtectionEnabled": True,
                }]),
                "",
            )

        source = WindowsDefenderSource(runner=_runner)
        result = source.health()
        assert result == {
            "ok": True,
            "detail": "Get-MpComputerStatus responded",
        }
        assert calls == [[
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-MpComputerStatus | Select-Object AntivirusEnabled,"
            "AMServiceEnabled,AntispywareEnabled,RealTimeProtectionEnabled "
            "| ConvertTo-Json",
        ]]

    def test_query_returns_detections(self):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        calls: list[list[str]] = []

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return (
                0,
                _json.dumps([{
                    "ThreatName": "Trojan:Win32/Test",
                    "SeverityName": "Severe",
                    "StatusName": "Active",
                    "InitialDetectionTime": "2025-01-01T12:00:00Z",
                }]),
                "",
            )

        source = WindowsDefenderSource(runner=_runner)
        records = source.query({"target": "threats"})
        assert calls == [[
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-MpThreatDetection | ConvertTo-Json -Depth 5",
        ]]
        assert len(records) == 1
        assert records[0]["kind"] == "logs"
        assert records[0]["level_or_status"] == "severe"
        assert records[0]["message"] == (
            "Threat: Trojan:Win32/Test | Status: Active | Severity: Severe"
        )
        assert records[0]["raw"]["command"] == "Get-MpThreatDetection"

    def test_query_empty_on_nonzero_exit(self):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        calls: list[list[str]] = []

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return (127, "", "powershell not found")

        source = WindowsDefenderSource(runner=_runner)
        records = source.query({"target": "status"})
        assert records == []
        assert calls == [[
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-MpComputerStatus | ConvertTo-Json -Depth 5",
        ]]


# ============================================================================
# 11. Windows Event Log Connector
# ============================================================================


class TestWindowsEventLogConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource({})
        assert source.KIND == "logs"
        assert source.name == "wineventlog:System"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource({
            "backend": "powershell",
            "channel": "Security",
            "name": "sec-log",
        })
        assert source.name == "sec-log"

    def test_health_ok_with_runner(self):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        calls: list[list[str]] = []

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return (0, "LogName : System", "")

        source = WindowsEventLogSource({}, runner=_runner)
        result = source.health()
        assert result == {
            "ok": True,
            "source": "wineventlog:System",
            "detail": "ok",
            "channel": "System",
        }
        assert calls == [[
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-WinEvent -ListLog 'System'",
        ]]

    def test_health_not_ok_when_runner_unavailable(self):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        calls: list[list[str]] = []

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return (127, "", "powershell not found")

        source = WindowsEventLogSource({}, runner=_runner)
        result = source.health()
        assert result == {
            "ok": False,
            "source": "wineventlog:System",
            "detail": "powershell not found",
            "channel": "System",
        }
        assert calls == [[
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-WinEvent -ListLog 'System'",
        ]]

    def test_query_returns_events(self):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        calls: list[list[str]] = []

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return (
                0,
                _json.dumps([{
                    "Id": 4624,
                    "LevelDisplayName": "Information",
                    "TimeCreated": "2025-01-01T12:00:00+00:00",
                    "Message": "An account was successfully logged on",
                    "ProviderName": "Microsoft-Windows-Security-Auditing",
                    "LogName": "Security",
                    "MachineName": "WIN-HOST-01",
                }]),
                "",
            )

        source = WindowsEventLogSource({"channel": "Security"}, runner=_runner)
        records = source.query({})
        assert calls == [[
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-WinEvent -FilterHashtable @{LogName='Security'} "
            "-MaxEvents 100 | ConvertTo-Json -Depth 5",
        ]]
        assert len(records) == 1
        assert records[0]["kind"] == "logs"
        assert records[0]["message"] == (
            "An account was successfully logged on"
        )
        assert records[0]["value"] == 4624.0
        assert records[0]["labels"] == {
            "provider": "Microsoft-Windows-Security-Auditing",
            "id": "4624",
            "machine": "WIN-HOST-01",
            "channel": "Security",
        }


# ============================================================================
# 12. Windows WMI Connector
# ============================================================================


class TestWindowsWmiConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.windows_wmi import WinWmiSource

        source = WinWmiSource()
        assert source.KIND == "metrics"
        assert source.name == "windows_wmi"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.windows_wmi import WinWmiSource

        source = WinWmiSource({"name": "wmi-cpu"})
        assert source.name == "wmi-cpu"

    def test_health_ok_with_runner(self):
        from general_ludd.connectors.windows_wmi import WinWmiSource

        calls: list[list[str]] = []

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return (
                0,
                _json.dumps([{
                    "Caption": "Microsoft Windows Server 2022 Standard",
                    "Version": "10.0.20348",
                }]),
                "",
            )

        source = WinWmiSource(runner=_runner)
        result = source.health()
        assert result["ok"] is True
        assert "Windows Server 2022 Standard" in result["detail"]
        assert calls == [[
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance -ClassName Win32_OperatingSystem | "
            "Select-Object Caption,Version | ConvertTo-Json",
        ]]

    def test_health_not_ok_when_runner_unavailable(self):
        from general_ludd.connectors.windows_wmi import WinWmiSource

        calls: list[list[str]] = []

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return (127, "", "powershell not found")

        source = WinWmiSource(runner=_runner)
        result = source.health()
        assert result == {
            "ok": False,
            "detail": "powershell not found",
        }
        assert calls == [[
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance -ClassName Win32_OperatingSystem | "
            "Select-Object Caption,Version | ConvertTo-Json",
        ]]

    def test_query_returns_wmi_results(self):
        from general_ludd.connectors.windows_wmi import WinWmiSource

        calls: list[list[str]] = []

        def _runner(argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return (
                0,
                _json.dumps([{
                    "Caption": "Intel64 Family 6",
                    "Name": "Intel Core i7",
                    "DeviceID": "CPU0",
                    "NumberOfCores": 8,
                    "NumberOfLogicalProcessors": 16,
                    "MaxClockSpeed": 3600,
                }]),
                "",
            )

        source = WinWmiSource({"name": "wmi-cpu"}, runner=_runner)
        records = source.query({"target": "cpu"})
        assert calls == [[
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance -ClassName Win32_Processor | "
            "ConvertTo-Json -Depth 3",
        ]]
        assert len(records) == 1
        assert records[0]["kind"] == "metrics"
        assert records[0]["source"] == "wmi-cpu"
        assert records[0]["message"] == (
            "Processor: Intel64 Family 6 Intel Core i7"
        )
        assert records[0]["labels"]["wmi_class"] == "Win32_Processor"
        assert records[0]["labels"]["numberofcores"] == 8
        assert records[0]["labels"]["maxclockspeed"] == 3600


# ============================================================================
# 13. macOS Log Connector
# ============================================================================


class TestMacOSLogConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource()
        assert source.KIND == "logs"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource({
            "predicate": 'process == "opendirectoryd"',
            "name": "od-log",
            "last": "10m",
        })
        assert source.name == "od-log"

    def test_health_ok_with_runner(self):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource(runner=lambda argv: _json.dumps([{"eventMessage": "test"}]))
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_log_entries(self):
        from general_ludd.connectors.macos_log import MacOSLogSource

        def _runner(argv: list[str]) -> str:
            return _json.dumps([
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
            ])

        source = MacOSLogSource({"predicate": 'process == "opendirectoryd"'}, runner=_runner)
        records = source.query({})
        assert len(records) >= 2
        assert all(r["kind"] == "logs" for r in records)

    def test_query_empty_without_runner(self):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource()
        records = source.query({})
        assert records == []


# ============================================================================
# 14. macOS Security Connector
# ============================================================================


class TestMacOSSecurityConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource()
        assert source.KIND == "logs"

    def test_constructs_custom_name(self):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource({"name": "mac-sec-audit"})
        assert source.name == "mac-sec-audit"

    def test_health_ok_with_runner(self):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource(runner=lambda argv: _json.dumps([{"event": "AUTHENTICATION_SUCCEEDED"}]))
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_security_events(self):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        def _runner(argv: list[str]) -> str:
            return _json.dumps([
                {"event": "AUTHENTICATION_SUCCEEDED", "user": "admin", "timestamp": "2025-01-01T12:00:00Z"},
                {"event": "GATEKEEPER_OVERRIDE", "user": "admin", "timestamp": "2025-01-01T12:01:00Z"},
            ])

        source = MacOSSecuritySource(runner=_runner)
        records = source.query({})
        assert len(records) >= 2
        assert all(r["kind"] == "logs" for r in records)

    def test_query_empty_without_runner(self):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource()
        records = source.query({})
        assert records == []


# ============================================================================
# 15. StatsD Parsing Connector
# ============================================================================


class TestStatsdParseConnector:
    def test_constructs_with_defaults(self):
        from general_ludd.connectors.statsd_parse import StatsdParseSource

        source = StatsdParseSource()
        assert source.KIND == "metrics"

    def test_constructs_custom_config(self):
        from general_ludd.connectors.statsd_parse import StatsdParseSource

        source = StatsdParseSource({"name": "statsd-parser", "port": 9125})
        assert source.name == "statsd-parser"

    def test_dispatch_parses_counter(self):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.requests:1|c")
        assert len(records) >= 1
        assert records[0]["message"] == "app.requests"
        assert records[0]["value"] == 1
        assert records[0]["labels"]["metric_type"] == "counter"

    def test_dispatch_parses_gauge(self):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.memory:512.5|g")
        assert len(records) >= 1
        assert records[0]["value"] == 512.5
        assert records[0]["labels"]["metric_type"] == "gauge"

    def test_dispatch_parses_timer(self):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.latency:42.5|ms")
        assert len(records) >= 1
        assert records[0]["value"] == 42.5
        assert records[0]["labels"]["metric_type"] == "timer"

    def test_dispatch_parses_with_tags(self):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.errors:5|c|#host:web1,region:us-east")
        assert len(records) >= 1
        assert records[0]["labels"].get("host") == "web1"
        assert records[0]["labels"].get("region") == "us-east"

    def test_dispatch_parses_sampling_rate(self):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.hits:10|c|@0.1")
        assert len(records) >= 1
        assert records[0]["value"] == 10
        assert records[0]["labels"]["sample_rate"] == "0.1"

    def test_dispatch_returns_empty_for_invalid(self):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("invalid without colon")
        assert records == []

    def test_strip_name_drops_prefix(self):
        from general_ludd.connectors.statsd_parse import _strip_name

        result = _strip_name("stats.gauges.app.requests")
        assert "app" in result

    def test_query_parses_multiple_lines(self):
        from general_ludd.connectors.statsd_parse import StatsdParseSource

        source = StatsdParseSource()
        records = source.query({"lines": ["app.hits:5|c", "app.mem:512|g"]})
        assert len(records) >= 2


# ============================================================================
# 16. Ingest Formats Connector
# ============================================================================


class TestIngestFormatsConnector:
    def test_detect_json_returns_true_for_json(self):
        from general_ludd.connectors.ingest_formats import _detect_format

        fmt = _detect_format('{"key": "value"}')
        assert fmt == "json"

    def test_detect_plain_returns_true_for_plain(self):
        from general_ludd.connectors.ingest_formats import _detect_format

        fmt = _detect_format("just plain text here")
        assert fmt == "plain"

    def test_detect_csv_returns_true_for_csv(self):
        from general_ludd.connectors.ingest_formats import _detect_format

        fmt = _detect_format("col1,col2,col3\nval1,val2,val3")
        assert fmt == "csv"
