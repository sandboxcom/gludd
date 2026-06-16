"""Unit tests for the Docker Engine API connector (mocked transport).

Covers: canned Engine-API JSON normalization (containers / logs / events),
socket-path + TCP-host confinement, and ``health()`` ok/not-ok/never-raises.
"""

from __future__ import annotations

import json

import pytest

from general_ludd.connectors.docker_api import KIND_EVENTS, KIND_LOGS, DockerApiSource


class RecordingTransport:
    """A fake HTTP transport returning a path->body map; records its calls."""

    def __init__(self, routes: dict[str, str] | None = None, default: str = "") -> None:
        self.routes = routes or {}
        self.default = default
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, path: str, params: dict | None = None) -> str:
        self.calls.append((path, params))
        for prefix, body in self.routes.items():
            if path.startswith(prefix):
                return body
        return self.default


CONTAINERS_JSON = json.dumps(
    [
        {
            "Id": "abc123",
            "Names": ["/web"],
            "Image": "nginx:latest",
            "State": "running",
            "Created": 1700000000,
        },
        {
            "Id": "def456",
            "Names": ["/db"],
            "Image": "postgres:16",
            "State": "exited",
            "Created": 1700000100,
        },
    ]
)

EVENTS_NDJSON = "\n".join(
    [
        json.dumps(
            {
                "Type": "container",
                "status": "die",
                "id": "abc123",
                "time": 1700000200,
                "Actor": {"ID": "abc123", "Attributes": {"name": "web", "image": "nginx:latest"}},
            }
        ),
        json.dumps(
            {
                "Type": "container",
                "status": "start",
                "id": "def456",
                "time": 1700000201,
                "Actor": {"ID": "def456", "Attributes": {"name": "db", "image": "postgres:16"}},
            }
        ),
    ]
)


class TestDockerNormalization:
    def test_kind_constants(self) -> None:
        assert KIND_LOGS == "logs"
        assert KIND_EVENTS == "events"

    def test_containers_normalized(self) -> None:
        tr = RecordingTransport({"/containers/json": CONTAINERS_JSON})
        src = DockerApiSource({"name": "dk"}, transport=tr)
        recs = src.query({"kind": "logs"})  # no container id -> list states
        assert len(recs) == 2
        r0 = recs[0]
        assert set(r0) == {
            "ts",
            "source",
            "kind",
            "level_or_status",
            "message",
            "value",
            "labels",
            "raw",
        }
        assert r0["source"] == "dk"
        assert r0["kind"] == "logs"
        assert r0["level_or_status"] == "running"
        assert r0["ts"] == pytest.approx(1700000000.0)
        assert r0["labels"] == {"container": "abc123", "image": "nginx:latest", "name": "web"}
        assert recs[1]["level_or_status"] == "exited"
        assert tr.calls[0][0] == "/containers/json"

    def test_container_logs_normalized(self) -> None:
        logs = "first line\nsecond line\n"
        tr = RecordingTransport({"/containers/abc123/logs": logs})
        src = DockerApiSource(transport=tr)
        recs = src.query({"container": "abc123", "tail": 10})
        assert [r["message"] for r in recs] == ["first line", "second line"]
        assert all(r["labels"]["container"] == "abc123" for r in recs)
        # tail was time/size-bound into params.
        path, params = tr.calls[0]
        assert path == "/containers/abc123/logs"
        assert params is not None and params["tail"] == "10"

    def test_events_normalized(self) -> None:
        tr = RecordingTransport({"/events": EVENTS_NDJSON})
        src = DockerApiSource(transport=tr)
        recs = src.query({"kind": "events", "since": 1700000000})
        assert len(recs) == 2
        die = recs[0]
        assert die["kind"] == "events"
        assert die["level_or_status"] == "error"  # die -> error
        assert "die" in die["message"]
        assert die["labels"] == {"container": "abc123", "image": "nginx:latest", "name": "web"}
        assert die["ts"] == pytest.approx(1700000200.0)
        assert recs[1]["level_or_status"] == "info"  # start


class TestDockerConfinement:
    def test_default_socket_accepted(self) -> None:
        src = DockerApiSource(transport=RecordingTransport())
        assert src._socket_path == "/var/run/docker.sock"

    def test_socket_under_run_accepted(self) -> None:
        src = DockerApiSource({"socket_path": "/run/docker.sock"}, transport=RecordingTransport())
        assert src._socket_path == "/run/docker.sock"

    @pytest.mark.parametrize(
        "bad_socket",
        ["/etc/passwd", "/var/run/../../etc/shadow", "relative/docker.sock", "/home/x/docker.sock"],
    )
    def test_socket_escape_rejected(self, bad_socket: str) -> None:
        with pytest.raises(ValueError):
            DockerApiSource({"socket_path": bad_socket}, transport=RecordingTransport())

    @pytest.mark.parametrize(
        "bad_host",
        ["http://127.0.0.1:2375", "http://localhost:2375", "http://169.254.169.254", "http://10.0.0.5:2375"],
    )
    def test_tcp_local_host_rejected_by_default(self, bad_host: str) -> None:
        with pytest.raises(ValueError):
            DockerApiSource({"tcp_host": bad_host}, transport=RecordingTransport())

    def test_tcp_local_host_allowed_with_optin(self) -> None:
        src = DockerApiSource(
            {"tcp_host": "http://127.0.0.1:2375", "allow_local": True},
            transport=RecordingTransport(),
        )
        assert "127.0.0.1" in src._target

    def test_tcp_public_host_accepted(self) -> None:
        src = DockerApiSource({"tcp_host": "http://docker.example.com:2375"}, transport=RecordingTransport())
        assert "docker.example.com" in src._target

    def test_unsafe_container_id_rejected(self) -> None:
        src = DockerApiSource(transport=RecordingTransport({"/containers/x/logs": "x"}))
        with pytest.raises(ValueError):
            src.query({"container": "abc/../../etc"})


class TestDockerHealth:
    def test_health_ok(self) -> None:
        tr = RecordingTransport({"/containers/json": "[]"})
        h = DockerApiSource(transport=tr).health()
        assert h["ok"] is True
        assert "reachable" in h["detail"]

    def test_health_not_ok(self) -> None:
        class Boom:
            def get(self, path: str, params: dict | None = None) -> str:
                raise ConnectionError("socket refused")

        h = DockerApiSource(transport=Boom()).health()
        assert h["ok"] is False
        assert "unavailable" in h["detail"]

    def test_health_never_raises(self) -> None:
        class Boom:
            def get(self, path: str, params: dict | None = None) -> str:
                raise RuntimeError("kaboom")

        h = DockerApiSource(transport=Boom()).health()
        assert isinstance(h, dict) and h["ok"] is False
