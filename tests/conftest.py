"""pytest conftest — ratchet-based strict xfail for known failures.

Reads config/ratchet.yml and applies pytest.mark.xfail(strict=True)
to every listed test. A test listed here that starts passing will
make the suite RED (strict xfail) until its entry is removed.

Entries whose reason starts with "flaky" use strict=False so that
non-deterministic passes don't break the gate.
"""
from __future__ import annotations

import asyncio
import gc
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
            reason = ratchet[item.nodeid]
            strict = not reason.startswith("flaky")
            item.add_marker(
                pytest.mark.xfail(strict=strict, reason=reason)
            )


@pytest.fixture(autouse=True)
def _no_auth_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set GLUDD_ALLOW_NO_AUTH=1 for the entire test suite.

    The daemon now defaults to fail-closed (503) when no PSK is configured.
    Most tests intentionally run without a PSK and expect open admin access —
    this fixture opts them all out of the fail-closed default so they continue
    to exercise daemon logic rather than middleware rejection.

    Tests that specifically need to verify the fail-closed behaviour (e.g.
    test_daemon_auth_redteam.py) must explicitly undo this via:
        monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
    before creating the daemon app under test.
    """
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")


@pytest.fixture(autouse=True)
def _async_teardown_drain() -> None:
    """Drain async generators and collect garbage after each test.

    Prevents aiosqlite / asyncio-resource finalizers from running
    after the function-scoped event loop closes, which causes
    'Event loop is closed' errors in CI serial ordering (Python 3.11/3.12).
    """
    yield
    # Drain any pending async generators on the currently-running loop.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Inside an async test — pytest-asyncio handles teardown; skip.
            pass
        elif not loop.is_closed():
            loop.run_until_complete(loop.shutdown_asyncgens())
    except RuntimeError:
        pass
    gc.collect()
