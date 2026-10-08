"""Regression tests for namespaced pytest collection serialization."""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
from pathlib import Path

from scripts.collection_lock import collection_lock
from scripts.makefile_layout import compose_makefile

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


def test_stale_owner_record_is_replaced_without_unlinking_lock_inode(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "collection.lock"
    lock_path.write_text("pid=999999\nacquired_unix_ns=1\n", encoding="utf-8")
    inode = lock_path.stat().st_ino

    with collection_lock(lock_path, timeout=0):
        record = lock_path.read_text(encoding="utf-8")
        assert f"pid={os.getpid()}" in record
        assert "acquired_unix_ns=" in record

    assert lock_path.stat().st_ino == inode


def test_collect_check_uses_project_collection_lock() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    target = makefile.split("collect-check:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/collection_lock.py --run" in target


def test_test_count_uses_repository_collection_lock() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    target = makefile.split("test-count:", 1)[1].split("\n\n", 1)[0]

    assert "scripts/collection_lock.py --run" in target


def test_collect_check_confines_ansible_temp_to_owned_observed_root() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    target = makefile.split("collect-check:", 1)[1].split("\n\n", 1)[0]

    assert 'ANSIBLE_TMP="$(OBSERVED_ROOT)/ansible-local-' in target
    assert 'ANSIBLE_LOCAL_TEMP="$$ANSIBLE_TMP"' in target
    assert "trap 'rm -rf -- \"$$ANSIBLE_TMP\"'" in target


def test_gate_refresh_uses_singleton_project_resource_lock() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    target = makefile.split("gate-refresh:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/collection_lock.py --resource gate-refresh --run" in target


def test_cross_worktree_collection_lease_contract_is_documented() -> None:
    document = (
        ROOT / "docs" / "features" / "CROSS_WORKTREE_COLLECTION_LEASE.md"
    ).read_text(encoding="utf-8")

    assert "git rev-parse --git-common-dir" in document
    assert "github.com/pytest-dev/pytest/issues/5456" in document
    assert "active-work-status" in document
    assert "waiter_overflow_count" in document
    assert "zero-downtime" in document.lower()
    assert "Rollback" in document
