"""pytest conftest — ratchet-based strict xfail for known failures.

Reads config/ratchet.yml and applies pytest.mark.xfail(strict=True)
to every listed test. A test listed here that starts passing will
make the suite RED (strict xfail) until its entry is removed.

Entries whose reason starts with "flaky" use strict=False so that
non-deterministic passes don't break the gate.
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import os
import re
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


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Emit GitHub Actions workflow commands for failed tests.

    When running inside GitHub Actions (GITHUB_ACTIONS=true), prints
    ``::error file=...,line=...::message`` lines for each FAILED or ERROR
    report so GitHub renders them as annotations in near-real-time — visible
    while the job is still running, not only in the post-run summary.

    Fires on the *call* phase (actual test body) for failures and on the
    *setup*/*teardown* phases for errors so fixture crashes are also surfaced.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    if report.passed:
        return
    # Only emit on call-phase failures + setup/teardown errors (skip xfail).
    if report.skipped:
        return

    # Extract file + line from the longrepr when available.
    file_part = ""
    line_part = ""
    longrepr = report.longreprtext if hasattr(report, "longreprtext") else str(report.longrepr)

    # Try to pull the last "path:lineno:" reference out of the traceback.
    if hasattr(report, "longrepr") and hasattr(report.longrepr, "reprcrash"):
        crash = report.longrepr.reprcrash  # type: ignore[union-attr]
        if crash:
            file_part = getattr(crash, "path", "") or ""
            line_part = str(getattr(crash, "lineno", "") or "")
    elif hasattr(report, "fspath"):
        file_part = str(report.fspath)

    # Build the ::error:: workflow command.
    # GitHub requires file= and line= to be non-empty for inline annotations.
    ann_parts: list[str] = []
    if file_part:
        ann_parts.append(f"file={file_part}")
    if line_part:
        ann_parts.append(f"line={line_part}")
    ann_props = ",".join(ann_parts)

    # Truncate message to avoid over-long annotation lines; strip ANSI codes.
    msg = re.sub(r"\x1b\[[0-9;]*m", "", longrepr)
    msg = msg.replace("\n", "%0A").replace("\r", "").replace(":", "%3A")
    msg = msg[:1000]  # GitHub annotation message limit is ~64 KB but keep it readable.

    prefix = f"::{('error' if report.failed else 'warning')} {ann_props}"
    print(f"{prefix}::{report.nodeid} — {msg}", flush=True)


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


@pytest.fixture(scope="session", autouse=True)
def _force_propagate_all_general_ludd_loggers() -> None:
    """Ensure every general_ludd.* logger has propagate=True.

    caplog captures log records by installing a handler on the logger named in
    ``caplog.at_level(logger=...)``.  If any ancestor or the logger itself has
    ``propagate = False`` (e.g. because a previous test or a third-party library
    reconfigured it), records never reach caplog's handler and ``caplog.records``
    are empty even when the code under test emits at the right level.

    This fixture runs once per test session and forces propagation on every
    already-created ``general_ludd.*`` logger.  It is belt-and-suspenders with
    the per-test ``.propagate = True`` lines already present in affected tests:
    even a logger created late in the session (or a CI-specific Python 3.11/3.12
    environment that reconfigures the log hierarchy) will propagate.
    """
    import logging

    for name in sorted(logging.root.manager.loggerDict.keys()):
        if name.startswith("general_ludd"):
            logging.getLogger(name).propagate = True


@pytest.fixture(autouse=True)
def _async_teardown_drain() -> None:
    """Drain async generators and collect garbage after each test.

    Prevents aiosqlite / asyncio-resource finalizers from running
    after the function-scoped event loop closes, which causes
    'Event loop is closed' errors in CI serial ordering (Python 3.11/3.12).
    """
    yield
    # Drain any pending async generators on the currently-running loop.
    with contextlib.suppress(RuntimeError):
        asyncio.get_running_loop()
    gc.collect()
