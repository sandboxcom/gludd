from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT_PATH = ROOT / "scripts" / "agent_watchdog.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_watchdog_singleton", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("agent_watchdog_singleton", module)
    spec.loader.exec_module(module)
    return module


aw = _load_module()


def test_same_namespace_owner_refuses_duplicate(tmp_path: Path):
    lock_path = tmp_path / "agent-watchdog.lock"
    first = aw.acquire_watchdog_lock(lock_path=lock_path, version="1.0")
    assert first is not None

    second = aw.acquire_watchdog_lock(lock_path=lock_path, version="1.0")
    assert second is None
    aw.release_watchdog_lock(first)


def test_dead_owner_lock_is_recovered(tmp_path: Path, monkeypatch):
    lock_path = tmp_path / "agent-watchdog.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 991991,
                "pid_start_time": "dead-owner",
                "started_at": 1.0,
                "version": "1.0",
                "token": "old-token",
            }
        )
    )

    def dead_kill(pid: int, signal: int) -> None:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(os, "kill", dead_kill)
    lease = aw.acquire_watchdog_lock(lock_path=lock_path, version="1.0")
    assert lease is not None
    assert json.loads(lock_path.read_text())["pid"] == os.getpid()
    aw.release_watchdog_lock(lease)


def test_newer_version_replaces_live_older_owner(tmp_path: Path, monkeypatch):
    lock_path = tmp_path / "agent-watchdog.lock"
    old = aw.acquire_watchdog_lock(lock_path=lock_path, version="1.0")
    assert old is not None
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: killed.append((pid, sig)) if sig else None,
    )

    newer = aw.acquire_watchdog_lock(lock_path=lock_path, version="2.0")
    assert newer is not None
    assert killed == [(os.getpid(), aw.signal.SIGTERM)]
    assert json.loads(lock_path.read_text())["version"] == "2.0"

    # The superseded owner must not remove the replacement's lock.
    aw.release_watchdog_lock(old)
    assert lock_path.exists()
    aw.release_watchdog_lock(newer)


def test_same_or_older_version_cannot_replace_live_owner(tmp_path: Path):
    lock_path = tmp_path / "agent-watchdog.lock"
    owner = aw.acquire_watchdog_lock(lock_path=lock_path, version="2.0")
    assert owner is not None
    assert aw.acquire_watchdog_lock(lock_path=lock_path, version="2.0") is None
    assert aw.acquire_watchdog_lock(lock_path=lock_path, version="1.9") is None
    aw.release_watchdog_lock(owner)


def test_stop_only_terminates_namespaced_owner(tmp_path: Path, monkeypatch):
    lock_path = tmp_path / "agent-watchdog.lock"
    owner = aw.acquire_watchdog_lock(lock_path=lock_path, version="1.0")
    assert owner is not None
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: signals.append((pid, sig)) if sig else None,
    )

    assert aw.stop_watchdog(lock_path=lock_path) is True
    assert signals == [(os.getpid(), aw.signal.SIGTERM)]
    # The owner is expected to release on shutdown; stop must not unlink an
    # active record belonging to a different process.
    assert lock_path.exists()
    aw.release_watchdog_lock(owner)
