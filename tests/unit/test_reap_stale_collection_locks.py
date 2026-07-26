"""Fail-closed tests for stale project-owned collection-lock reaping."""

from __future__ import annotations

import os
from pathlib import Path

from scripts.collection_lock import lock_timeout
from scripts.process_cleanup import ProcessInfo
from scripts.reap_stale_collection_locks import (
    assess_lock,
    reap_stale_gate_refresh_roots,
    reap_stale_locks,
    stale_gate_refresh_roots,
)


def _lock(path: Path, content: str, *, age: float = 1000.0, now: float = 2000.0) -> None:
    path.write_text(content, encoding="utf-8")
    os.utime(path, (now - age, now - age))


def _project_lock_root(tmp_path: Path) -> Path:
    root = tmp_path / "project-a"
    root.mkdir()
    return root


def test_dead_old_project_lock_is_stale_and_reaped(tmp_path: Path) -> None:
    root = _project_lock_root(tmp_path)
    lock = root / "gate-refresh.lock"
    _lock(lock, "pid=99999\n")

    decision = assess_lock(
        lock,
        namespace="project-a",
        project_root=tmp_path,
        process_table={},
        now=2000.0,
        stale_after=300.0,
    )
    assert decision.stale is True
    assert reap_stale_locks(
        root,
        namespace="project-a",
        project_root=tmp_path,
        process_table={},
        now=2000.0,
        stale_after=300.0,
        apply=True,
    ) == [lock]
    assert not lock.exists()


def test_live_project_owner_is_preserved_even_when_old(tmp_path: Path) -> None:
    lock = tmp_path / "gate-refresh.lock"
    _lock(lock, "pid=123\n")
    table = {
        123: ProcessInfo(
            pid=123,
            ppid=1,
            elapsed_secs=5000,
            command="uv run python /work/project-a/scripts/collection_lock.py --resource gate-refresh",
        )
    }

    decision = assess_lock(
        lock,
        namespace="project-a",
        project_root=Path("/work/project-a"),
        process_table=table,
        now=2000.0,
        stale_after=300.0,
    )
    assert decision.stale is False
    assert decision.reason == "live-project-owner"


def test_external_worktree_and_live_gate_are_never_reaped(tmp_path: Path) -> None:
    root = _project_lock_root(tmp_path)
    lock = root / "gate-refresh.lock"
    _lock(lock, "pid=123\n")
    table = {
        123: ProcessInfo(
            pid=123,
            ppid=1,
            elapsed_secs=5000,
            command="uv run python /work/other-project/scripts/collection_lock.py --resource gate-refresh",
        )
    }

    decisions = reap_stale_locks(
        root,
        namespace="project-a",
        project_root=Path("/work/project-a"),
        process_table=table,
        now=2000.0,
        stale_after=300.0,
        apply=True,
    )
    assert decisions == []
    assert lock.exists()


def test_fresh_or_unknown_lock_is_preserved(tmp_path: Path) -> None:
    lock = tmp_path / "collection.lock"
    _lock(lock, "not-a-pid\n", age=2.0)

    decision = assess_lock(
        lock,
        namespace="project-a",
        project_root=tmp_path,
        process_table={},
        now=2000.0,
        stale_after=300.0,
    )
    assert decision.stale is False
    assert decision.reason == "fresh-or-unknown-owner"


def test_unrecognized_lock_names_are_ignored(tmp_path: Path) -> None:
    root = _project_lock_root(tmp_path)
    lock = root / "external-worktree.lock"
    _lock(lock, "pid=99999\n")

    assert reap_stale_locks(
        root,
        namespace="project-a",
        project_root=tmp_path,
        process_table={},
        now=2000.0,
        stale_after=0.0,
        apply=True,
    ) == []
    assert lock.exists()


def test_gate_refresh_wait_is_bounded_without_changing_direct_collection_default() -> None:
    assert lock_timeout("gate-refresh") == 120.0
    assert lock_timeout("collection") == 900.0


def test_orphaned_project_gate_refresh_root_is_reaped() -> None:
    table = {
        321: ProcessInfo(
            pid=321,
            ppid=1,
            elapsed_secs=1000,
            command="/work/project-a/scripts/collection_lock.py --resource gate-refresh --run /work/project-a/make",
        )
    }
    roots = stale_gate_refresh_roots(
        table,
        namespace="project-a",
        project_root=Path("/work/project-a"),
        stale_after=300.0,
    )
    assert [root.pid for root in roots] == [321]


def test_live_or_external_gate_refresh_roots_are_preserved() -> None:
    table = {
        321: ProcessInfo(
            pid=321,
            ppid=42,
            elapsed_secs=1000,
            command="/work/project-a/scripts/collection_lock.py --resource gate-refresh",
        ),
        654: ProcessInfo(
            pid=654,
            ppid=1,
            elapsed_secs=1000,
            command="/work/other-project/scripts/collection_lock.py --resource gate-refresh",
        ),
    }
    assert (
        reap_stale_gate_refresh_roots(
            table,
            namespace="project-a",
            project_root=Path("/work/project-a"),
            stale_after=300.0,
            apply=True,
        )
        == []
    )
