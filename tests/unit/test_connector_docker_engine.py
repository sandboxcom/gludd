"""Unit tests for the Docker Engine log/event connector.

All HTTP is mocked via an injected fake transport — no real daemon, no socket,
no network. Covers: container list (ps), a timestamped + multiplexed log
stream, an events stream, internal-TCP SSRF reject, and health ok/not-ok.
"""

from __future__ import annotations

import json
from typing import Any

from general_ludd.connectors.docker_engine import (
    DockerEngineSource,
    Response,
)


# --------------------------------------------------------------------------- #
# Fake transport
# --------------------------------------------------------------------------- #
class FakeTransport:
    """Routes (method, path) -> canned Response; records calls for assertions."""

    def __init__(self, routes: dict[tuple[str, str], Response]) -> None:
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None,
        base_url: str,
        timeout: float,
    ) -> Response:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "query": query,
                "base_url": base_url,
                "timeout": timeout,
            }
        )
        key = (method, path)
        if key not in self.routes:
            return Response(status=404, headers={}, body=b"")
        return self.routes[key]


def _json_resp(payload: Any) -> Response:
    return Response(status=200, headers={"content-type": "application/json"}, body=json.dumps(payload).encode())


def _frame(stream: int, text: str) -> bytes:
    """Build one Docker multiplexed stream frame: 8-byte header + payload."""
    data = text.encode("utf-8")
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


# --------------------------------------------------------------------------- #
# Canned payloads
# --------------------------------------------------------------------------- #
CONTAINER_LIST = [
    {
        "Id": "abc123def456",
        "Names": ["/web-1"],
        "Image": "nginx:latest",
        "State": "running",
        "Status": "Up 3 hours",
    },
    {
        "Id": "ffff0000",
        "Names": ["/db-1"],
        "Image": "postgres:16",
        "State": "exited",
        "Status": "Exited (0) 2 minutes ago",
    },
]

# Multiplexed, timestamped log stream: stdout line then stderr line.
LOG_STREAM = (
    _frame(1, "2024-01-02T03:04:05.123456789Z hello from stdout\n")
    + _frame(2, "2024-01-02T03:04:06.000000000Z oops on stderr\n")
)

EVENTS_STREAM = (
    json.dumps(
        {
            "Type": "container",
            "Action": "start",
            "id": "abc123def456",
            "from": "nginx:latest",
            "time": 1704164645,
            "timeNano": 1704164645123456789,
            "Actor": {"ID": "abc123def456", "Attributes": {"image": "nginx:latest"}},
        }
    )
    + "\n"
    + json.dumps(
        {
            "Type": "image",
            "Action": "pull",
            "id": "postgres:16",
            "from": "postgres:16",
            "time": 1704164700,
        }
    )
    + "\n"
).encode()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
class TestContract:
    def test_kind_class_attr(self) -> None:
        assert DockerEngineSource.KIND == "logs"

    def test_name_and_default_socket(self) -> None:
        src = DockerEngineSource({})
        assert src.name == "docker-engine"
        assert src.base_url == "unix:///var/run/docker.sock"

    def test_name_overridable(self) -> None:
        src = DockerEngineSource({"name": "prod-engine"})
        assert src.name == "prod-engine"


class TestPs:
    def test_ps_normalization(self) -> None:
        t = FakeTransport({("GET", "/containers/json"): _json_resp(CONTAINER_LIST)})
        src = DockerEngineSource({"transport": t})
        records = src.query({"mode": "ps"})
        assert len(records) == 2
        first = records[0]
        assert first["kind"] == "logs"
        assert first["source"] == "docker-engine"
        assert first["level_or_status"] == "running"
        assert first["labels"]["container_id"] == "abc123def456"
        assert first["labels"]["container_name"] == "web-1"
        assert first["labels"]["image"] == "nginx:latest"
        assert "web-1" in first["message"]
        assert first["raw"]["Id"] == "abc123def456"
        assert records[1]["level_or_status"] == "exited"


class TestLogs:
    def test_logs_request_params(self) -> None:
        t = FakeTransport(
            {("GET", "/containers/abc123def456/logs"): Response(200, {}, LOG_STREAM)}
        )
        src = DockerEngineSource({"transport": t})
        src.query({"mode": "logs", "container_id": "abc123def456"})
        q = t.calls[0]["query"]
        assert q["stdout"] == "1"
        assert q["stderr"] == "1"
        assert q["timestamps"] == "1"
        assert "tail" in q

    def test_logs_multiplexed_ts_parse_and_labels(self) -> None:
        t = FakeTransport(
            {("GET", "/containers/abc123def456/logs"): Response(200, {}, LOG_STREAM)}
        )
        src = DockerEngineSource({"transport": t})
        records = src.query(
            {"mode": "logs", "container_id": "abc123def456", "container_name": "web-1"}
        )
        assert len(records) == 2
        out, err = records
        assert out["ts"] == "2024-01-02T03:04:05.123456789Z"
        assert out["message"] == "hello from stdout"
        assert out["labels"]["stream"] == "stdout"
        assert out["labels"]["container_id"] == "abc123def456"
        assert out["labels"]["container_name"] == "web-1"
        assert out["level_or_status"] == "stdout"
        assert out["kind"] == "logs"
        assert err["ts"] == "2024-01-02T03:04:06.000000000Z"
        assert err["message"] == "oops on stderr"
        assert err["labels"]["stream"] == "stderr"

    def test_logs_plain_tty_stream(self) -> None:
        body = b"2024-01-02T03:04:05.000000000Z plain tty line\n"
        t = FakeTransport({("GET", "/containers/cid/logs"): Response(200, {}, body)})
        src = DockerEngineSource({"transport": t})
        records = src.query({"mode": "logs", "container_id": "cid"})
        assert len(records) == 1
        assert records[0]["message"] == "plain tty line"
        assert records[0]["ts"] == "2024-01-02T03:04:05.000000000Z"

    def test_logs_requires_container_id(self) -> None:
        src = DockerEngineSource({"transport": FakeTransport({})})
        try:
            src.query({"mode": "logs"})
        except ValueError as exc:
            assert "container_id" in str(exc)
        else:
            raise AssertionError("expected ValueError")


class TestEvents:
    def test_events_normalization(self) -> None:
        t = FakeTransport({("GET", "/events"): Response(200, {}, EVENTS_STREAM)})
        src = DockerEngineSource({"transport": t})
        records = src.query({"mode": "events", "since": "1704164600"})
        assert len(records) == 2
        start = records[0]
        assert start["level_or_status"] == "start"
        assert start["message"] == "container start"
        assert start["labels"]["id"] == "abc123def456"
        assert start["labels"]["from"] == "nginx:latest"
        assert start["ts"] is not None and start["ts"].startswith("2024-01-02")
        assert records[1]["message"] == "image pull"

    def test_events_since_passed_through(self) -> None:
        t = FakeTransport({("GET", "/events"): Response(200, {}, EVENTS_STREAM)})
        src = DockerEngineSource({"transport": t})
        src.query({"mode": "events", "since": "999"})
        assert t.calls[0]["query"]["since"] == "999"


class TestSSRF:
    def test_internal_tcp_host_rejected_in_query(self) -> None:
        src = DockerEngineSource({"base_url": "http://127.0.0.1:2375"})
        try:
            src.query({"mode": "ps"})
        except RuntimeError as exc:
            assert "internal" in str(exc).lower() or "refused" in str(exc).lower()
        else:
            raise AssertionError("expected RuntimeError for internal TCP host")

    def test_internal_tcp_host_rejected_in_health(self) -> None:
        src = DockerEngineSource({"base_url": "http://10.0.0.5:2375"})
        result = src.health()
        assert result["ok"] is False
        assert "refused" in result["detail"].lower() or "internal" in result["detail"].lower()

    def test_localhost_name_rejected(self) -> None:
        src = DockerEngineSource({"base_url": "http://localhost:2375"})
        assert src.health()["ok"] is False

    def test_non_ip_hostname_rejected_no_dns(self) -> None:
        # We refuse to resolve DNS; a bare public-looking name is still blocked.
        src = DockerEngineSource({"base_url": "http://example.com:2375"})
        assert src.health()["ok"] is False

    def test_public_ip_tcp_allowed(self) -> None:
        # A public literal IP is permitted; transport is mocked so no real call.
        t = FakeTransport({("GET", "/_ping"): Response(200, {}, b"OK")})
        src = DockerEngineSource({"base_url": "http://93.184.216.34:2375", "transport": t})
        assert src.health()["ok"] is True


class TestHealth:
    def test_health_ok(self) -> None:
        t = FakeTransport({("GET", "/_ping"): Response(200, {}, b"OK")})
        src = DockerEngineSource({"transport": t})
        result = src.health()
        assert result["ok"] is True
        assert result["detail"] == "ok"

    def test_health_not_ok_status(self) -> None:
        t = FakeTransport({("GET", "/_ping"): Response(500, {}, b"boom")})
        src = DockerEngineSource({"transport": t})
        result = src.health()
        assert result["ok"] is False
        assert "500" in result["detail"]

    def test_health_never_raises_on_transport_error(self) -> None:
        def boom(*args: Any, **kwargs: Any) -> Response:
            raise ConnectionError("socket gone")

        src = DockerEngineSource({"transport": boom})
        result = src.health()
        assert result["ok"] is False
        assert "ConnectionError" in result["detail"]


class TestDispatch:
    def test_unknown_mode_raises(self) -> None:
        src = DockerEngineSource({"transport": FakeTransport({})})
        try:
            src.query({"mode": "bogus"})
        except ValueError as exc:
            assert "bogus" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    def test_default_mode_is_ps(self) -> None:
        t = FakeTransport({("GET", "/containers/json"): _json_resp(CONTAINER_LIST)})
        src = DockerEngineSource({"transport": t})
        records = src.query()
        assert len(records) == 2
