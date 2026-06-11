"""pytest conftest — ratchet-based strict xfail for known failures.

Reads config/ratchet.yml and applies pytest.mark.xfail(strict=True)
to every listed test. A test listed here that starts passing will
make the suite RED (strict xfail) until its entry is removed.

This replaces the numeric tolerance (≤116 failures = PASS) with an
explicit, pytest-native, self-enforcing ledger.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_RATCHET: dict[str, str] = {}


def _load_ratchet() -> dict[str, str]:
    global _RATCHET
    if _RATCHET:
        return _RATCHET
    ratchet_path = Path(__file__).resolve().parent.parent / "config" / "ratchet.yml"
    if ratchet_path.is_file():
        raw = ratchet_path.read_text(encoding="utf-8")
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ": " in line:
                node_id, reason = line.split(": ", 1)
                node_id = node_id.strip()
                reason = reason.strip().strip('"')
                if node_id:
                    _RATCHET[node_id] = reason
    return _RATCHET


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    ratchet = _load_ratchet()
    if not ratchet:
        return
    for item in items:
        if item.nodeid in ratchet:
            item.add_marker(
                pytest.mark.xfail(strict=True, reason=ratchet[item.nodeid])
            )
