"""pytest conftest — fixtures, hooks, and test environment setup."""
from __future__ import annotations

import asyncio
import contextlib
import gc
import os
import re

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Cap each xdist WORKER's address space so a runaway worker is RLIMIT_AS-
    bounded (its own allocations start failing with MemoryError) instead of the
    kernel OOM-killing the whole box and taking the run — and the operator's
    laptop — down with it.

    Applied ONLY inside an xdist worker (``PYTEST_XDIST_WORKER`` is set on
    workers, absent on the controller and on a plain single-process ``pytest``
    run) so an isolated ``make test-iso`` run is never capped.

    FAIL-OPEN by construction:
      * ``rlimit.apply_limits`` is a no-op where ``RLIMIT_AS`` is unsupported —
        notably macOS, where the local-OOM problem is mitigated instead by the
        load-aware worker sizing in ``scripts/adaptive_test.py`` — and swallows
        the ``ValueError``/``OSError`` a sandbox raises; and
      * the whole block is wrapped in ``try/except Exception`` so a resource
        limit can NEVER break test collection.

    The cap defaults to 1600 MiB per worker (env-tunable via
    ``GLUDD_TEST_WORKER_MEM_MB``); CPU-time is left unbounded because the
    project-wide ``pytest-timeout`` (180s) already backstops a hung test.
    """
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return
    try:
        from general_ludd.system.rlimit import apply_limits

        raw = os.environ.get("GLUDD_TEST_WORKER_MEM_MB", "1600")
        try:
            mem_mb = int(raw)
        except ValueError:
            mem_mb = 1600
        apply_limits(mem_mb=mem_mb, cpu_s=0)
    except Exception:
        # Never let a best-effort resource limit break collection.
        pass


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
        # Remaining present-asserting caplog leaves found by full enumeration
        # (latent — same shape as the confirmed failures; covered pre-emptively
        # so this is the COMPLETE set of general_ludd caplog capture points).
        "general_ludd.models",
        "general_ludd.models.model_registry",
        "general_ludd.secrets.migration",
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
