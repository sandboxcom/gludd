"""Regression tests for full-gate/gate-refresh mutual exclusion."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate_run_lock.py"
MAKEFILE = ROOT / "Makefile"


def _run(action: str, lock: Path, pid: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), action, str(lock), str(pid)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_live_owner_blocks_competing_gate(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"

    acquired = _run("acquire", lock, os.getpid())
    assert acquired.returncode == 0, acquired.stderr

    competing = _run("acquire", lock, os.getppid())
    assert competing.returncode != 0
    assert "already running" in (competing.stdout + competing.stderr)


def test_stale_owner_is_reclaimed(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"
    lock.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")

    result = _run("acquire", lock, os.getpid())

    assert result.returncode == 0, result.stderr
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_only_owner_can_release_lock(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"
    assert _run("acquire", lock, os.getpid()).returncode == 0

    refused = _run("release", lock, os.getppid())
    assert refused.returncode != 0
    assert lock.exists()

    released = _run("release", lock, os.getpid())
    assert released.returncode == 0
    assert not lock.exists()


def test_gate_and_refresh_acquire_before_running_phases() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    gate_header = next(line for line in text.splitlines() if line.startswith("gate:"))
    refresh_header = next(
        line for line in text.splitlines() if line.startswith("gate-refresh:")
    )

    assert gate_header.split(":", 1)[1].strip().split()[0] == "_gate-run-lock-acquire"
    assert (
        refresh_header.split(":", 1)[1].strip().split()[0]
        == "_gate-run-lock-acquire"
    )
    assert text.count('gate_run_lock.py release "$(GATE_RUN_LOCK)" "$$PPID"') >= 2
