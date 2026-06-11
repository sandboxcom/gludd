from __future__ import annotations

import socket

import pytest


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class TestEphemeralPortForDaemonE2E:
    def test_find_free_port_returns_valid_port(self):
        port = _find_free_port()
        assert 1024 < port < 65536

    def test_free_port_is_actually_free(self):
        port = _find_free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
            assert s.getsockname()[1] == port

    def test_ephemeral_port_differs_between_calls(self):
        port1 = _find_free_port()
        port2 = _find_free_port()
        assert port1 != port2

    def test_can_bind_to_occupied_port_after_release(self):
        port = _find_free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
        port2 = _find_free_port()
        assert 1024 < port2 < 65536
