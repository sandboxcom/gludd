"""Transport and failure-branch coverage for the Docker Engine connector."""

from __future__ import annotations

import socket

import pytest

from general_ludd.connectors import docker_engine


class _FakeSocket:
    """One-response socket with observable ownership and request data."""

    def __init__(self, response: bytes) -> None:
        self._responses = [response, b""]
        self.connected_to: str | tuple[str, int] | None = None
        self.timeout: float | None = None
        self.sent = b""
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, address: str | tuple[str, int]) -> None:
        self.connected_to = address

    def sendall(self, payload: bytes) -> None:
        self.sent += payload

    def recv(self, size: int) -> bytes:
        del size
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed = True


def _install_socket(
    monkeypatch: pytest.MonkeyPatch,
    response: bytes,
) -> _FakeSocket:
    fake = _FakeSocket(response)

    def socket_factory(*args: object, **kwargs: object) -> _FakeSocket:
        del args, kwargs
        return fake

    monkeypatch.setattr(socket, "socket", socket_factory)
    return fake


def test_default_transport_owns_unix_socket_and_parses_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unix transport sends encoded query data, parses headers, and closes."""
    fake = _install_socket(
        monkeypatch,
        b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\n\r\n{}",
    )

    response = docker_engine._default_transport(
        "GET",
        "/containers/json",
        {"all": "1"},
        "unix:///tmp/docker.sock",
        2.0,
    )

    assert response.status == 201
    assert response.headers == {"content-type": "application/json"}
    assert response.body == b"{}"
    assert fake.connected_to == "/tmp/docker.sock"
    assert b"GET /containers/json?all=1 HTTP/1.1" in fake.sent
    assert fake.timeout == 2.0
    assert fake.closed is True


def test_default_transport_uses_tcp_default_port_and_tolerates_malformed_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TCP transport uses the scheme port while malformed status remains unknown."""
    fake = _install_socket(monkeypatch, b"invalid-status\r\nBrokenHeader\r\n\r\nbody")

    response = docker_engine._default_transport(
        "GET",
        "/_ping",
        None,
        "https://8.8.8.8",
        1.0,
    )

    assert fake.connected_to == ("8.8.8.8", 443)
    assert response.status == 0
    assert response.headers == {}
    assert response.body == b"body"
    assert fake.closed is True


def test_get_json_ps_and_logs_fail_closed_on_invalid_responses() -> None:
    """HTTP errors and wrong payload shapes cannot become normalized records."""
    responses = iter(
        [
            docker_engine._DockerResponse(500, {}, b"error"),
            docker_engine._DockerResponse(204, {}, b""),
            docker_engine._DockerResponse(200, {}, b"{}"),
            docker_engine._DockerResponse(404, {}, b"missing"),
        ]
    )

    def transport(
        method: str,
        path: str,
        query: dict[str, object] | None,
        base_url: str,
        timeout: float,
    ) -> docker_engine._DockerResponse:
        del method, path, query, base_url, timeout
        return next(responses)

    source = docker_engine.DockerEngineSource({"transport": transport})
    with pytest.raises(RuntimeError, match="HTTP 500"):
        source._get_json("/failure")
    assert source._get_json("/empty") == []
    assert source.query({"mode": "ps", "all": True}) == []
    with pytest.raises(RuntimeError, match="HTTP 404"):
        source.query({"mode": "logs", "container_id": "abc"})


def test_public_tcp_events_forward_bounds_and_cover_timestamp_fallbacks() -> None:
    """Public TCP events forward bounds and normalize nano, seconds, and missing time."""
    observed_query: dict[str, object] | None = None
    body = (
        b'{"Type":"container","Action":"start","timeNano":1000000000}\n'
        b'{"Type":"image","Action":"pull","time":2}\n'
        b'{"Type":"network","Action":"connect"}\n\n'
    )

    def transport(
        method: str,
        path: str,
        query: dict[str, object] | None,
        base_url: str,
        timeout: float,
    ) -> docker_engine._DockerResponse:
        nonlocal observed_query
        del method, path, base_url, timeout
        observed_query = query
        return docker_engine._DockerResponse(200, {}, body)

    source = docker_engine.DockerEngineSource(
        {"base_url": "http://8.8.8.8:2375", "transport": transport}
    )
    records = source.query({"mode": "events", "since": 1, "until": 3})

    assert observed_query == {"since": "1", "until": "3"}
    assert [record["ts"] for record in records] == [
        "1970-01-01T00:00:01+00:00",
        "1970-01-01T00:00:02+00:00",
        None,
    ]
    assert docker_engine._is_multiplexed(b"\x01\x01\x00\x00\x00\x00\x00\x01x") is False
