"""Regression test: stale TODO(integration) block removed from features router.

All items that were deferred (Ansible module, role, molecule scenario, dogfood
self-check, Makefile target) have shipped. The router module docstring should
no longer reference pending integration work.
"""
from __future__ import annotations

from pathlib import Path

ROUTER = Path(__file__).resolve().parents[2] / "src" / "general_ludd" / "routers" / "features.py"


def test_stale_todo_removed() -> None:
    text = ROUTER.read_text()
    assert "TODO(integration)" not in text, "stale TODO(integration) block should be removed"
    assert "Deferred to integration sprint" not in text, "stale deferral note should be removed"


def test_done_note_present() -> None:
    text = ROUTER.read_text()
    assert "Done:" in text, "features router should record that integration items shipped"
