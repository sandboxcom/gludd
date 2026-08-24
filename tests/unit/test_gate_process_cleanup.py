"""Tests for gate process cleanup: stale gate timeout, cleanup target, watchdog detection.

Covers:
- gate-cleanup kills stale process (SIGTERM → 10s wait → SIGKILL)
- gate-background sets timeout (GATE_TIMEOUT env var, ABORTED marker)
- watchdog _check_gate_background detects stale gate
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"
GITIGNORE = ROOT / ".gitignore"


def _makefile_content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return MAKEFILE.read_text()


# --- Makefile structural tests ---


def test_gate_cleanup_target_exists():
    content = _makefile_content()
    assert "gate-cleanup:" in content, "Makefile missing 'gate-cleanup:' target"


def test_gate_cleanup_calls_gate_kill():
    content = _makefile_content()
    idx = content.find("gate-cleanup:")
    assert idx != -1
    recipe_block = content[idx : idx + 600]
    assert "gate-kill" in recipe_block, (
        "gate-cleanup must invoke gate-kill to terminate running gate"
    )


def test_gate_cleanup_removes_old_logs():
    content = _makefile_content()
    idx = content.find("gate-cleanup:")
    assert idx != -1
    recipe_block = content[idx : idx + 600]
    assert ".gate-logs" in recipe_block, (
        "gate-cleanup must clean old gate log files"
    )
    assert "gate-*.log" in recipe_block, (
        "gate-cleanup must target gate-*.log files"
    )


def test_gate_cleanup_removes_atomic_publication_scratch() -> None:
    content = _makefile_content()
    idx = content.find("gate-cleanup:")
    recipe_block = content[idx : idx + 800]

    assert ".gate-status.next" in recipe_block
    assert ".gate-status.running" in recipe_block


def test_gate_atomic_publication_scratch_is_ignored() -> None:
    ignored = set(GITIGNORE.read_text(encoding="utf-8").splitlines())

    assert ".gate-status.next" in ignored
    assert ".gate-status.running" in ignored


def test_gate_kill_waits_10s_before_sigkill():
    content = _makefile_content()
    idx = content.find("gate-kill:")
    assert idx != -1
    recipe_block = content[idx : idx + 800]
    assert "-lt 10" in recipe_block, (
        "gate-kill must wait 10 seconds before SIGKILL"
    )
    assert "kill -TERM" in recipe_block, (
        "gate-kill must send SIGTERM first"
    )
    assert "kill -KILL" in recipe_block, (
        "gate-kill must send SIGKILL after wait"
    )


def test_gate_background_has_timeout_watcher():
    content = _makefile_content()
    idx = content.find("gate-background:")
    assert idx != -1
    recipe_block = content[idx : idx + 3000]
    assert "GATE_TIMEOUT" in recipe_block, (
        "gate-background must reference GATE_TIMEOUT env var"
    )
    assert "sleep $$GATE_TIMEOUT_VAL" in recipe_block, (
        "gate-background must spawn timeout watcher with sleep"
    )
    assert "GATE: ABORTED" in recipe_block, (
        "gate-background timeout must write ABORTED marker"
    )


def test_gate_background_timeout_default_3600():
    content = _makefile_content()
    idx = content.find("gate-background:")
    recipe_block = content[idx : idx + 3000]
    assert ":-3600" in recipe_block, (
        "gate-background must default GATE_TIMEOUT to 3600s (1 hour)"
    )


# --- Watchdog _check_gate_background tests ---


def test_watchdog_gate_max_runtime_is_one_hour():
    import scripts.agent_watchdog as aw
    importlib = __import__("importlib")
    importlib.reload(aw)
    assert aw.GATE_MAX_RUNTIME_SECS == 3600, (
        f"GATE_MAX_RUNTIME_SECS must be 3600 (1 hour), got {aw.GATE_MAX_RUNTIME_SECS}"
    )


def test_watchdog_check_gate_background_kills_stale(tmp_path, monkeypatch):
    import importlib

    import scripts.agent_watchdog as aw

    importlib.reload(aw)
    # Redirect the watchdog's gate-state globals into this test's own tmp dir.
    # They default to CWD-relative repo-root files; under pytest-xdist multiple
    # workers share that CWD, so this test and test_watchdog_detects_stale_gate_status
    # would clobber each other's .gate-status/.gate-background.pid preconditions
    # (one needs the pid file present + status absent, the other the inverse) —
    # a shared-/tmp-style race that flaked CI run 29113728377 (unit-2, py3.12).
    pid_file = tmp_path / ".gate-background.pid"
    status_file = tmp_path / ".gate-status"
    monkeypatch.setattr(aw, "GATE_PID_FILE", pid_file)
    monkeypatch.setattr(aw, "_GATE_STATUS", status_file)

    status_file.unlink(missing_ok=True)
    pid_file.write_text("99999")
    mtime_in_past = time.time() - 4000
    os.utime(str(pid_file), (mtime_in_past, mtime_in_past))

    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))

    with patch.object(os, "kill", side_effect=fake_kill):
        aw._check_gate_background()

    assert len(calls) >= 1, (
        "_check_gate_background must attempt to kill stale gate process, "
        f"got {len(calls)} calls: {calls}"
    )


def test_watchdog_detects_stale_gate_status(tmp_path, monkeypatch):
    import scripts.agent_watchdog as aw
    # Per-test tmp isolation of the watchdog gate-state globals (see the sibling
    # test above): shared CWD-relative repo-root files race across xdist workers.
    status_file = tmp_path / ".gate-status"
    pid_file = tmp_path / ".gate-background.pid"
    monkeypatch.setattr(aw, "GATE_PID_FILE", pid_file)
    monkeypatch.setattr(aw, "_GATE_STATUS", status_file)

    pid_file.unlink(missing_ok=True)
    status_file.write_text("=== GATE: PASSED ===")
    os.utime(str(status_file), (time.time(), time.time() - 4000))

    logs = []
    with patch.object(aw, "_log", side_effect=lambda msg: logs.append(msg)):
        aw._check_gate_background()

    stale_logs = [entry for entry in logs if "STALE" in entry or "stale" in entry.lower()]
    assert len(stale_logs) >= 1, (
        "Watchdog must log when .gate-status is older than 1h with no gate running"
    )
