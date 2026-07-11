"""Unit tests for the Podman log/event connector (Docker-compatible API).

All HTTP is mocked via an injected fake transport — no real podman service, no
socket, no network. Covers: container list (ps), a timestamped + multiplexed
log stream, an events stream, internal-TCP SSRF reject, and health ok/not-ok.
"""

from __future__ import annotations

import json
from typing import Any

from general_ludd.connectors.podman import PodmanSource
from general_ludd.connectors.podman import _PodmanResponse as Response


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
    """Build one Docker-compatible multiplexed stream frame."""
    data = text.encode("utf-8")
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


# --------------------------------------------------------------------------- #
# Canned payloads (Docker-compatible shapes that Podman emits)
# --------------------------------------------------------------------------- #
CONTAINER_LIST = [
    {
        "Id": "podabc123",
        "Names": ["/api-1"],
        "Image": "docker.io/library/redis:7",
        "State": "running",
        "Status": "Up 10 minutes",
    },
    {
        "Id": "poddead00",
        "Names": ["/worker-1"],
        "Image": "docker.io/library/python:3.12",
        "State": "stopped",
        "Status": "Exited (137) 1 minute ago",
    },
]

LOG_STREAM = (
    _frame(1, "2025-03-04T10:20:30.500000000Z podman stdout line\n")
    + _frame(2, "2025-03-04T10:20:31.000000000Z podman stderr line\n")
)

EVENTS_STREAM = (
    json.dumps(
        {
            "Type": "container",
            "Action": "died",
            "id": "podabc123",
            "from": "docker.io/library/redis:7",
            "time": 1741083630,
            "timeNano": 1741083630500000000,
            "Actor": {"ID": "podabc123", "Attributes": {"image": "redis:7"}},
        }
    )
    + "\n"
    + json.dumps(
        {
            "Type": "image",
            "Action": "remove",
            "id": "python:3.12",
            "from": "python:3.12",
            "time": 1741083700,
        }
    )
    + "\n"
).encode()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
class TestContract:
    def test_kind_class_attr(self) -> None:
        assert PodmanSource.KIND == "logs"

    def test_name_and_default_socket(self) -> None:
        src = PodmanSource({})
        assert src.name == "podman"
        assert src.base_url == "unix:///run/podman/podman.sock"

    def test_name_overridable(self) -> None:
        src = PodmanSource({"name": "rootless-podman"})
        assert src.name == "rootless-podman"


class TestPs:
    def test_ps_normalization(self) -> None:
        t = FakeTransport({("GET", "/containers/json"): _json_resp(CONTAINER_LIST)})
        src = PodmanSource({"transport": t})
        records = src.query({"mode": "ps"})
        assert len(records) == 2
        first = records[0]
        assert first["kind"] == "logs"
        assert first["source"] == "podman"
        assert first["level_or_status"] == "running"
        assert first["labels"]["container_id"] == "podabc123"
        assert first["labels"]["container_name"] == "api-1"
        assert first["labels"]["image"] == "docker.io/library/redis:7"
        assert records[1]["level_or_status"] == "stopped"


class TestLogs:
    def test_logs_request_params(self) -> None:
        t = FakeTransport({("GET", "/containers/podabc123/logs"): Response(200, {}, LOG_STREAM)})
        src = PodmanSource({"transport": t})
        src.query({"mode": "logs", "container_id": "podabc123"})
        q = t.calls[0]["query"]
        assert q["stdout"] == "1"
        assert q["stderr"] == "1"
        assert q["timestamps"] == "1"
        assert "tail" in q

    def test_logs_multiplexed_ts_parse_and_labels(self) -> None:
        t = FakeTransport({("GET", "/containers/podabc123/logs"): Response(200, {}, LOG_STREAM)})
        src = PodmanSource({"transport": t})
        records = src.query(
            {"mode": "logs", "container_id": "podabc123", "container_name": "api-1"}
        )
        assert len(records) == 2
        out, err = records
        assert out["ts"] == "2025-03-04T10:20:30.500000000Z"
        assert out["message"] == "podman stdout line"
        assert out["labels"]["stream"] == "stdout"
        assert out["labels"]["container_id"] == "podabc123"
        assert out["labels"]["container_name"] == "api-1"
        assert err["ts"] == "2025-03-04T10:20:31.000000000Z"
        assert err["labels"]["stream"] == "stderr"
        assert err["message"] == "podman stderr line"

    def test_logs_plain_tty_stream(self) -> None:
        body = b"2025-03-04T10:20:30.000000000Z tty line here\n"
        t = FakeTransport({("GET", "/containers/cid/logs"): Response(200, {}, body)})
        src = PodmanSource({"transport": t})
        records = src.query({"mode": "logs", "container_id": "cid"})
        assert len(records) == 1
        assert records[0]["message"] == "tty line here"
        assert records[0]["ts"] == "2025-03-04T10:20:30.000000000Z"

    def test_logs_requires_container_id(self) -> None:
        src = PodmanSource({"transport": FakeTransport({})})
        try:
            src.query({"mode": "logs"})
        except ValueError as exc:
            assert "container_id" in str(exc)
        else:
            raise AssertionError("expected ValueError")


class TestEvents:
    def test_events_normalization(self) -> None:
        t = FakeTransport({("GET", "/events"): Response(200, {}, EVENTS_STREAM)})
        src = PodmanSource({"transport": t})
        records = src.query({"mode": "events", "since": "1741083600"})
        assert len(records) == 2
        died = records[0]
        assert died["level_or_status"] == "died"
        assert died["message"] == "container died"
        assert died["labels"]["id"] == "podabc123"
        assert died["labels"]["from"] == "docker.io/library/redis:7"
        assert died["ts"] is not None and died["ts"].startswith("2025-03-04")
        assert records[1]["message"] == "image remove"

    def test_events_since_passed_through(self) -> None:
        t = FakeTransport({("GET", "/events"): Response(200, {}, EVENTS_STREAM)})
        src = PodmanSource({"transport": t})
        src.query({"mode": "events", "since": "123"})
        assert t.calls[0]["query"]["since"] == "123"


class TestSSRF:
    def test_internal_tcp_host_rejected_in_query(self) -> None:
        src = PodmanSource({"base_url": "http://127.0.0.1:8080"})
        try:
            src.query({"mode": "ps"})
        except RuntimeError as exc:
            assert "internal" in str(exc).lower() or "refused" in str(exc).lower()
        else:
            raise AssertionError("expected RuntimeError for internal TCP host")

    def test_internal_tcp_host_rejected_in_health(self) -> None:
        src = PodmanSource({"base_url": "http://192.168.1.10:8080"})
        result = src.health()
        assert result["ok"] is False
        assert "refused" in result["detail"].lower() or "internal" in result["detail"].lower()

    def test_localhost_name_rejected(self) -> None:
        src = PodmanSource({"base_url": "http://localhost:8080"})
        assert src.health()["ok"] is False

    def test_non_ip_hostname_rejected_no_dns(self) -> None:
        src = PodmanSource({"base_url": "http://example.org:8080"})
        assert src.health()["ok"] is False

    def test_public_ip_tcp_allowed(self) -> None:
        t = FakeTransport({("GET", "/_ping"): Response(200, {}, b"OK")})
        src = PodmanSource({"base_url": "http://93.184.216.34:8080", "transport": t})
        assert src.health()["ok"] is True

    def test_alibaba_metadata_ip_rejected(self) -> None:
        # 100.100.100.200 sits in the CGNAT 100.64.0.0/10 range, which
        # Python's ipaddress module does NOT classify as private/reserved --
        # this was NOT reliably blocked by the old local
        # _is_internal_literal_host (it relied on is_private/is_reserved).
        src = PodmanSource({"base_url": "http://100.100.100.200:8080"})
        assert src.health()["ok"] is False

    def test_metadata_google_internal_name_rejected(self) -> None:
        # Regression pin now that this routes through the canonical
        # host_is_blocked name check (previously blocked only incidentally
        # via the "any non-IP host is refused" rule).
        src = PodmanSource({"base_url": "http://metadata.google.internal:8080"})
        assert src.health()["ok"] is False


class TestHealth:
    def test_health_ok(self) -> None:
        t = FakeTransport({("GET", "/_ping"): Response(200, {}, b"OK")})
        src = PodmanSource({"transport": t})
        result = src.health()
        assert result["ok"] is True
        assert result["detail"] == "ok"

    def test_health_not_ok_status(self) -> None:
        t = FakeTransport({("GET", "/_ping"): Response(503, {}, b"down")})
        src = PodmanSource({"transport": t})
        result = src.health()
        assert result["ok"] is False
        assert "unexpected status 503" in result["detail"]

    def test_health_never_raises_on_transport_error(self) -> None:
        def boom(*args: Any, **kwargs: Any) -> Response:
            raise TimeoutError("no socket")

        src = PodmanSource({"transport": boom})
        result = src.health()
        assert result["ok"] is False
        assert "TimeoutError" in result["detail"]


class TestDispatch:
    def test_unknown_mode_raises(self) -> None:
        src = PodmanSource({"transport": FakeTransport({})})
        try:
            src.query({"mode": "nope"})
        except ValueError as exc:
            assert "nope" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    def test_default_mode_is_ps(self) -> None:
        t = FakeTransport({("GET", "/containers/json"): _json_resp(CONTAINER_LIST)})
        src = PodmanSource({"transport": t})
        records = src.query()
        assert len(records) == 2
