"""Ephemeral port utility and target-game filtering for daemon/game tests.

Usage:
    from tests.unit.test_ephemeral_port import _find_free_port
    port = _find_free_port()

    # Target a single game for e2e game-building tests:
    E2E_TARGET_GAME=tetris make test TESTFILE=tests/e2e/test_game_building_deepseek.py
"""
from __future__ import annotations

import os
import socket


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def pytest_collection_modifyitems(config, items):
    """Filter game-building tests by E2E_TARGET_GAME env var.

    When E2E_TARGET_GAME is set (e.g. 'tetris'), deselect all game-building
    tests that don't match the target game.  This makes -k filtering O(1)
    instead of O(n) — non-matching tests never reach execution.
    """
    target = os.environ.get("E2E_TARGET_GAME", "").strip().lower()
    if not target:
        return

    deselected = []
    kept = []
    for item in items:
        nodeid = item.nodeid.lower()
        if target in nodeid or "game_building" not in nodeid.lower():
            kept.append(item)
        else:
            deselected.append(item)

    if deselected:
        items[:] = kept
        config.hook.pytest_deselected(items=deselected)
