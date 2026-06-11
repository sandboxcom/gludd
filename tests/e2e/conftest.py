"""Ephemeral port utility for daemon tests.

Usage:
    from tests.unit.test_ephemeral_port import _find_free_port
    port = _find_free_port()

Replaces hardcoded port 8000 in daemon e2e tests.
"""
from __future__ import annotations

import socket


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]
