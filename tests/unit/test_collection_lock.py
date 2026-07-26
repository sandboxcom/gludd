"""Regression tests for namespaced pytest collection serialization."""

from __future__ import annotations

import multiprocessing
import threading
import time
from pathlib import Path

from scripts.collection_lock import collection_lock

ROOT = Path(__file__).parents[2]


def _hold_lock(path: str, ready: multiprocessing.synchronize.Event, release: multiprocessing.synchronize.Event) -> None:
    with collection_lock(Path(path), timeout=2):
        ready.set()
        release.wait(5)


def test_same_project_collection_lock_has_single_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "collection.lock"
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    process = multiprocessing.Process(target=_hold_lock, args=(str(lock_path), ready, release))
    process.start()
    try:
        assert ready.wait(2), "first collection owner did not acquire the lock"
        threading.Timer(0.15, release.set).start()
        started = time.monotonic()
        with collection_lock(lock_path, timeout=2):
            waited = time.monotonic() - started
        assert waited >= 0.05, "second same-project collection did not wait for the owner"
    finally:
        release.set()
        process.join(5)
    assert process.exitcode == 0


def test_distinct_project_collection_locks_do_not_contend(tmp_path: Path) -> None:
    first = tmp_path / "project-a" / "collection.lock"
    second = tmp_path / "project-b" / "collection.lock"
    first.parent.mkdir()
    second.parent.mkdir()
    with collection_lock(first, timeout=0), collection_lock(second, timeout=0):
        assert first != second


def test_collection_lock_rejects_invalid_timeout(tmp_path: Path) -> None:
    try:
        with collection_lock(tmp_path / "collection.lock", timeout=-1):
            pass
    except ValueError:
        pass
    else:
        raise AssertionError("negative collection lock timeout must be rejected")


def test_collect_check_uses_project_collection_lock() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("collect-check:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/collection_lock.py --run" in target
