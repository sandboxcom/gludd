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


@pytest.fixture(autouse=True)
def _force_propagate_all_general_ludd_loggers() -> None:
    """Restore caplog capture for the general_ludd.* logger subtree per test.

    caplog captures records by installing a handler that receives records
    *propagated* up the logger hierarchy.  Capture silently breaks if any
    ``general_ludd`` **ancestor** logger (not just the leaf named in
    ``caplog.at_level(logger=...)``) has ``propagate = False`` or
    ``disabled = True``, or if a global ``logging.disable()`` is in effect.
    A prior test — or a src import side-effect — that leaves the log hierarchy
    in that state poisons every later caplog test on the SAME xdist worker
    (the exact failure mode behind the connectors / events-bus / daemon-auth
    caplog tests failing only on gw0 in CI while passing in isolation).

    Running FUNCTION-scoped (previously session-scoped, which could not undo
    pollution introduced mid-session), it resets only the global disable and the
    HIERARCHY-ANCESTOR loggers that gate whole subtrees.  It deliberately does
    NOT rewrite leaf loggers' ``propagate``/``disabled``: forcing propagation on
    a leaf a test intentionally silenced would MANUFACTURE records that test
    asserts are absent.  Per CPython, ``callHandlers`` walks leaf -> ... -> root
    and stops only on an ancestor's ``propagate=False`` (it never consults a
    leaf's or ancestor's level, nor an ancestor's ``disabled``); global
    suppression is ``logging.disable``.  So resetting the global disable + the
    ancestor links is both necessary and sufficient to repair the empty-
    ``caplog.records`` failures, without the leaf-blanket's regression surface.
    """
    import logging
    import pkgutil

    import general_ludd

    # 1. Undo any leftover global disable() from a prior test on this worker.
    logging.disable(logging.NOTSET)
    # 2. Re-open propagation on the root package + EVERY immediate
    #    general_ludd.<subpackage> ancestor (intermediate nodes that gate whole
    #    subtrees).  pkgutil.iter_modules scans the filesystem WITHOUT importing
    #    and selects only package dirs (ispkg) — never leaf .py loggers — so it
    #    cannot manufacture records a test silenced at a leaf, while covering
    #    every subtree (secrets/worker/reload/event_loop/code_intelligence/...),
    #    not just the original 4.  Light (~60 getLogger calls, no loggerDict
    #    walk), so it does not perturb xdist ordering the way a full sweep did.
    _ancestors = ["general_ludd", "general_ludd.daemon"]
    _ancestors += [
        f"general_ludd.{info.name}"
        for info in pkgutil.iter_modules(general_ludd.__path__)
        if info.ispkg
    ]
    # A few LEAF loggers are themselves left propagate=False by sibling tests
    # (their package ancestors are already reset above, so the block is AT the
    # leaf).  Reset exactly these known-polluted leaves — NOT a blanket leaf
    # sweep (which manufactured records / perturbed ordering before).
    _ancestors += [
        "general_ludd.connectors.base",
        "general_ludd.events.bus",
        "general_ludd.events.hooks",
        # Explicit packages + leaves for the caplog tests the pkgutil scan did
        # not cover in CI (run 28557097700 showed these 5 subtrees still empty-
        # caplog): resetting the LEAF is what empirically clears them, and the
        # package is a belt-and-suspenders fallback in case iter_modules under-
        # reports on the CI-installed general_ludd package.
        "general_ludd.secrets",
        "general_ludd.secrets.manager",
        "general_ludd.worker",
        "general_ludd.worker.app",
        "general_ludd.reload",
        "general_ludd.reload.worker_broadcast",
        "general_ludd.event_loop",
        "general_ludd.event_loop.loop",
        "general_ludd.code_intelligence",
        "general_ludd.code_intelligence.rg_search",
    ]
    for name in _ancestors:
        lg = logging.getLogger(name)
        lg.propagate = True
        lg.disabled = False


@pytest.fixture(autouse=True)
def _reset_process_registry():
    """Reset the process-wide ProcessRegistry singleton around each test.

    general_ludd.process.registry._DEFAULT_REGISTRY is a lazily-created global
    with no reset hook; PIDs registered by test_processes_router's managed_child
    fixture or by ansible/core_runner leak into later tests on the same xdist
    worker, making the suite order-dependent. Clearing the global forces a fresh
    empty registry next call. Runs at setup/teardown only, so the singleton-
    identity test is unaffected.
    """
    from general_ludd.process import registry as _proc_registry
    _proc_registry._DEFAULT_REGISTRY = None
    yield
    _proc_registry._DEFAULT_REGISTRY = None


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
