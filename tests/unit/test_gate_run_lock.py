"""Regression tests for full-gate/gate-refresh mutual exclusion."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate_run_lock.py"
MAKEFILE = ROOT / "Makefile"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gate_run_lock_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lock_module = _load_module()


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


def test_assert_inactive_blocks_live_owner_and_reclaims_stale_owner(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.lock"
    assert _run("assert-inactive", missing, os.getpid()).returncode == 0

    live = tmp_path / "live.lock"
    live.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    refused = _run("assert-inactive", live, os.getppid())
    assert refused.returncode != 0
    assert "active gate" in (refused.stdout + refused.stderr)

    stale = tmp_path / "stale.lock"
    stale.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    reclaimed = _run("assert-inactive", stale, os.getpid())
    assert reclaimed.returncode == 0
    assert not stale.exists()


def test_assert_inactive_fails_closed_on_unreadable_owner(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"
    lock.write_text("{not-json", encoding="utf-8")

    refused = _run("assert-inactive", lock, os.getpid())

    assert refused.returncode != 0
    assert "unreadable owner" in (refused.stdout + refused.stderr)


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


def test_history_mutation_targets_depend_on_active_gate_guard() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    guarded_headers = (
        "_commit-lock-acquire:",
        "_merge-strategy-guard:",
        "agent-merge:",
        "gated-merge:",
        "git-checkout:",
        "git-cherry-pick:",
    )

    for target in guarded_headers:
        header = next(line for line in text.splitlines() if line.startswith(target))
        assert "_gate-mutation-guard" in header, target


def test_pid_and_owner_helpers_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert lock_module._pid_alive(0) is False
    assert lock_module._pid_alive(os.getpid()) is True

    def gone(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(lock_module.os, "kill", gone)
    assert lock_module._pid_alive(73) is False

    def inaccessible(_pid: int, _signal: int) -> None:
        raise PermissionError

    monkeypatch.setattr(lock_module.os, "kill", inaccessible)
    assert lock_module._pid_alive(73) is True

    missing = tmp_path / "missing.lock"
    assert lock_module._read_owner(missing) is None
    missing.write_text("{bad-json", encoding="utf-8")
    assert lock_module._read_owner(missing) is None


def test_publish_release_and_acquire_boundaries(tmp_path: Path) -> None:
    lock = tmp_path / "gate.lock"
    assert lock_module._publish_lock(lock, os.getpid()) is True
    assert lock_module._publish_lock(lock, os.getpid()) is False
    assert lock_module.release(lock, os.getppid()) == 1
    assert lock_module.release(lock, os.getpid()) == 0
    assert lock_module.release(lock, os.getpid()) == 1

    malformed = tmp_path / "malformed.lock"
    malformed.write_text("[]", encoding="utf-8")
    assert lock_module.acquire(malformed, os.getpid()) == 1

    live = tmp_path / "live.lock"
    live.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert lock_module.acquire(live, os.getppid()) == 1

    stale = tmp_path / "stale.lock"
    stale.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    assert lock_module.acquire(stale, os.getpid()) == 0


def test_assert_inactive_direct_boundaries(tmp_path: Path) -> None:
    missing = tmp_path / "missing.lock"
    assert lock_module.assert_inactive(missing, os.getpid()) == 0

    malformed = tmp_path / "malformed.lock"
    malformed.write_text("null", encoding="utf-8")
    assert lock_module.assert_inactive(malformed, os.getpid()) == 1

    live = tmp_path / "live.lock"
    live.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert lock_module.assert_inactive(live, os.getppid()) == 1

    stale = tmp_path / "stale.lock"
    stale.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    assert lock_module.assert_inactive(stale, os.getpid()) == 0
    assert not stale.exists()


def test_main_dispatches_actions_and_rejects_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock = tmp_path / "gate.lock"
    assert lock_module.main(["gate_run_lock.py"]) == 2
    assert lock_module.main(["gate_run_lock.py", "unknown", str(lock), "1"]) == 2
    assert (
        lock_module.main(
            ["gate_run_lock.py", "acquire", str(lock), "not-a-pid"]
        )
        == 2
    )

    monkeypatch.setattr(lock_module, "acquire", lambda _path, _pid: 11)
    monkeypatch.setattr(lock_module, "release", lambda _path, _pid: 12)
    monkeypatch.setattr(lock_module, "assert_inactive", lambda _path, _pid: 13)
    assert lock_module.main(["gate_run_lock.py", "acquire", str(lock), "7"]) == 11
    assert lock_module.main(["gate_run_lock.py", "release", str(lock), "7"]) == 12
    assert (
        lock_module.main(
            ["gate_run_lock.py", "assert-inactive", str(lock), "7"]
        )
        == 13
    )
