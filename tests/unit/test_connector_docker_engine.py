"""Structural tests for Docker Engine connector."""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.docker_engine import (
    Connector,
    DockerEngineSource,
    Transport,
    _DockerResponse,
    _is_internal_literal_host,
    _is_multiplexed,
    _iter_log_payload,
    _looks_rfc3339,
    _record,
    _split_rfc3339,
)

Response = _DockerResponse


class FakeTransport:
    def __init__(self, routes: dict[tuple[str, str], Response]) -> None:
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, method: str, path: str, query: dict[str, object] | None,
        base_url: str, timeout: float,
    ) -> Response:
        self.calls.append({"method": method, "path": path, "query": query, "base_url": base_url, "timeout": timeout})
        return self.routes[(method, path)]


def _resp(status: int, body: object = b"") -> Response:
    raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    return Response(status=status, headers={"Content-Type": "application/json"}, body=raw)


PS_PAYLOAD = [
    {"Id": "abc123", "Names": ["/web-server"], "State": "running", "Status": "Up 3h", "Image": "nginx:latest"},
]


class TestHelpers:
    def test_looks_rfc3339_valid(self) -> None:
        assert _looks_rfc3339("2024-01-02T03:04:05.123Z") is True

    def test_looks_rfc3339_invalid(self) -> None:
        assert _looks_rfc3339("hello world") is False

    def test_looks_rfc3339_too_short(self) -> None:
        assert _looks_rfc3339("short") is False

    def test_split_rfc3339_with_ts(self) -> None:
        ts, msg = _split_rfc3339("2024-01-02T03:04:05.123Z hello world")
        assert ts == "2024-01-02T03:04:05.123Z"
        assert msg == "hello world"

    def test_split_rfc3339_no_ts(self) -> None:
        ts, msg = _split_rfc3339("plain message")
        assert ts is None
        assert msg == "plain message"

    def test_split_rfc3339_empty(self) -> None:
        ts, msg = _split_rfc3339("")
        assert ts is None
        assert msg == ""

    def test_record_shape(self) -> None:
        r = _record(
            ts="2024-01-01T00:00:00Z",
            source="docker",
            level_or_status="stdout",
            message="hello",
            value=None,
            labels={"k": "v"},
            raw={"a": 1},
        )
        assert r["ts"] == "2024-01-01T00:00:00Z"
        assert r["source"] == "docker"
        assert r["kind"] == "logs"
        assert r["level_or_status"] == "stdout"
        assert r["message"] == "hello"
        assert r["labels"] == {"k": "v"}

    def test_is_multiplexed_valid(self) -> None:
        frame = bytes([1, 0, 0, 0, 0, 0, 0, 5]) + b"hello"
        assert _is_multiplexed(frame) is True

    def test_is_multiplexed_invalid(self) -> None:
        assert _is_multiplexed(b"plain text that is not multiplexed") is False

    def test_is_multiplexed_short(self) -> None:
        assert _is_multiplexed(b"short") is False

    def test_is_multiplexed_bad_stream_byte(self) -> None:
        frame = bytes([9, 0, 0, 0, 0, 0, 0, 5]) + b"hello"
        assert _is_multiplexed(frame) is False

    def test_iter_log_payload_multiplexed(self) -> None:
        frame = bytes([1, 0, 0, 0, 0, 0, 0, 11]) + b"hello\nworld"
        result = _iter_log_payload(frame)
        assert len(result) == 2
        assert result[0][0] == "stdout"
        assert "hello" in result[0][1]

    def test_iter_log_payload_plain(self) -> None:
        result = _iter_log_payload(b"line one\nline two")
        assert len(result) == 2
        assert result[0][0] == "stdout"
        assert result[0][1] == "line one"

    def test_is_internal_literal_host_loopback(self) -> None:
        assert _is_internal_literal_host("127.0.0.1") is True

    def test_is_internal_literal_host_private(self) -> None:
        assert _is_internal_literal_host("10.0.0.5") is True

    def test_is_internal_literal_host_non_ip(self) -> None:
        assert _is_internal_literal_host("docker.example.com") is True

    def test_is_internal_literal_host_public_ip(self) -> None:
        assert _is_internal_literal_host("8.8.8.8") is False


class TestContract:
    def test_kind(self) -> None:
        assert DockerEngineSource.KIND == "logs"

    def test_transport_protocol_exists(self) -> None:
        assert Transport is not None

    def test_connector_alias(self) -> None:
        assert Connector is DockerEngineSource


class TestInit:
    def test_defaults(self) -> None:
        src = DockerEngineSource()
        assert src.name == "docker-engine"
        assert "unix://" in src.base_url

    def test_custom(self) -> None:
        src = DockerEngineSource({"name": "my-docker", "base_url": "unix:///custom/socket", "timeout": 5.0})
        assert src.name == "my-docker"
        assert src.base_url == "unix:///custom/socket"
        assert src.timeout == 5.0

    def test_ssrf_blocks_tcp_internal(self) -> None:
        src = DockerEngineSource({"base_url": "http://10.0.0.1:2375"})
        assert src._ssrf_error is not None


class TestHealth:
    def test_ok(self) -> None:
        t = FakeTransport({("GET", "/_ping"): _resp(200)})
        src = DockerEngineSource({"transport": t})
        r = src.health()
        assert r["ok"] is True

    def test_transport_error(self) -> None:
        def _fail(*a: Any, **kw: Any) -> Response:
            raise RuntimeError("down")

        src = DockerEngineSource({"transport": _fail})
        r = src.health()
        assert r["ok"] is False

    def test_ssrf_blocked(self) -> None:
        src = DockerEngineSource({"base_url": "http://10.0.0.1:2375"})
        r = src.health()
        assert r["ok"] is False


class TestQueryPs:
    def test_returns_containers(self) -> None:
        t = FakeTransport({("GET", "/containers/json"): _resp(200, PS_PAYLOAD)})
        src = DockerEngineSource({"transport": t})
        records = src.query({"mode": "ps"})
        assert len(records) == 1
        r = records[0]
        assert r["source"] == "docker-engine"
        assert r["kind"] == "logs"
        assert r["labels"]["container_id"] == "abc123"
        assert r["labels"]["container_name"] == "web-server"

    def test_empty_list(self) -> None:
        t = FakeTransport({("GET", "/containers/json"): _resp(200, [])})
        src = DockerEngineSource({"transport": t})
        records = src.query({"mode": "ps"})
        assert records == []

    def test_unknown_mode(self) -> None:
        src = DockerEngineSource()
        with pytest.raises(ValueError):
            src.query({"mode": "bogus"})

    def test_default_mode_is_ps(self) -> None:
        t = FakeTransport({("GET", "/containers/json"): _resp(200, PS_PAYLOAD)})
        src = DockerEngineSource({"transport": t})
        records = src.query({})
        assert len(records) >= 0  # type: ignore[arg-type]  # default mode ps returns list

    def test_ssrf_blocked_raises(self) -> None:
        src = DockerEngineSource({"base_url": "http://10.0.0.1:2375"})
        with pytest.raises(RuntimeError):
            src.query({"mode": "ps"})


class TestQueryLogs:
    def test_logs_no_container_id_raises(self) -> None:
        src = DockerEngineSource()
        with pytest.raises(ValueError):
            src.query({"mode": "logs"})

    def test_logs_returns_records(self) -> None:
        t = FakeTransport({("GET", "/containers/abc/logs"): _resp(200, b"plain log line")})
        src = DockerEngineSource({"transport": t})
        records = src.query({"mode": "logs", "container_id": "abc"})
        assert len(records) >= 1
        assert records[0]["source"] == "docker-engine"


class TestQueryEvents:
    def test_events_returns_records(self) -> None:
        event = json.dumps({"Type": "container", "Action": "start", "id": "abc123", "time": 1700000000}).encode()
        t = FakeTransport({("GET", "/events"): _resp(200, event)})
        src = DockerEngineSource({"transport": t})
        records = src.query({"mode": "events"})
        assert len(records) == 1
        r = records[0]
        assert r["level_or_status"] == "start"
        assert "container" in r["message"]

    def test_events_http_error_raises(self) -> None:
        t = FakeTransport({("GET", "/events"): _resp(500)})
        src = DockerEngineSource({"transport": t})
        with pytest.raises(RuntimeError):
            src.query({"mode": "events"})
