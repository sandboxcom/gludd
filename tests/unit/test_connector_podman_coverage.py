"""Transport and failure-branch coverage for the Podman connector."""

from __future__ import annotations

import socket

import pytest

from general_ludd.connectors import podman


class _FakeSocket:
    """One-response socket with observable request and close state."""

    def __init__(self, response: bytes) -> None:
        self._responses = [response, b""]
        self.connected_to: str | tuple[str, int] | None = None
        self.sent = b""
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        del timeout

    def connect(self, address: str | tuple[str, int]) -> None:
        self.connected_to = address

    def sendall(self, payload: bytes) -> None:
        self.sent += payload

    def recv(self, size: int) -> bytes:
        del size
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed = True


def _install_socket(monkeypatch: pytest.MonkeyPatch, response: bytes) -> _FakeSocket:
    fake = _FakeSocket(response)

    def socket_factory(*args: object, **kwargs: object) -> _FakeSocket:
        del args, kwargs
        return fake

    monkeypatch.setattr(socket, "socket", socket_factory)
    return fake


def test_default_transport_owns_unix_socket_and_parses_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unix transport forwards query data, parses headers, and always closes."""
    fake = _install_socket(
        monkeypatch,
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}",
    )

    response = podman._default_transport(
        "GET",
        "/containers/json",
        {"all": "1"},
        "unix:///tmp/podman.sock",
        2.0,
    )

    assert response.status == 200
    assert response.headers == {"content-type": "application/json"}
    assert fake.connected_to == "/tmp/podman.sock"
    assert b"GET /containers/json?all=1 HTTP/1.1" in fake.sent
    assert fake.closed is True


def test_default_transport_uses_tcp_default_and_tolerates_malformed_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TCP transport uses the default port while malformed status stays unknown."""
    fake = _install_socket(monkeypatch, b"invalid\r\nBrokenHeader\r\n\r\nbody")

    response = podman._default_transport(
        "GET",
        "/_ping",
        None,
        "http://8.8.8.8",
        1.0,
    )

    assert fake.connected_to == ("8.8.8.8", 8080)
    assert response.status == 0
    assert response.headers == {}
    assert response.body == b"body"
    assert fake.closed is True


def test_get_json_ps_and_logs_handle_empty_error_and_all_query() -> None:
    """Wrong HTTP state and empty payloads cannot become container records."""
    observed_queries: list[dict[str, object] | None] = []
    responses = iter(
        [
            podman._PodmanResponse(500, {}, b"error"),
            podman._PodmanResponse(204, {}, b""),
            podman._PodmanResponse(200, {}, b"[]"),
            podman._PodmanResponse(404, {}, b"missing"),
        ]
    )

    def transport(
        method: str,
        path: str,
        query: dict[str, object] | None,
        base_url: str,
        timeout: float,
    ) -> podman._PodmanResponse:
        del method, path, base_url, timeout
        observed_queries.append(query)
        return next(responses)

    source = podman.PodmanSource(transport=transport)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        source._get_json("/failure")
    assert source._get_json("/empty") == []
    assert source.query({"mode": "ps", "all": True}) == []
    assert observed_queries[-1] == {"all": "1"}
    with pytest.raises(RuntimeError, match="HTTP 404"):
        source.query({"mode": "logs", "container_id": "abc"})


def test_public_tcp_events_forward_bounds_and_timestamp_fallbacks() -> None:
    """Public TCP events normalize nano, second, and absent timestamps."""
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
    ) -> podman._PodmanResponse:
        nonlocal observed_query
        del method, path, base_url, timeout
        observed_query = query
        return podman._PodmanResponse(200, {}, body)

    source = podman.PodmanSource(
        {"base_url": "http://8.8.8.8:8080"},
        transport=transport,
    )
    records = source.query({"mode": "events", "since": 1, "until": 3})

    assert observed_query == {"since": "1", "until": "3"}
    assert [record["ts"] for record in records] == [
        "1970-01-01T00:00:01+00:00",
        "1970-01-01T00:00:02+00:00",
        None,
    ]
    assert podman._is_multiplexed(b"\x01\x01\x00\x00\x00\x00\x00\x01x") is False
