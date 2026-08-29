"""Fail-closed tests for stale project-owned collection-lock reaping."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import scripts.collection_lock as collection_lock_module
import scripts.reap_stale_collection_locks as reaper
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


def test_process_cwd_permission_error_is_an_unavailable_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protected procfs cwd must not abort read-only stale-owner discovery."""

    observed: list[Path] = []

    def protected_procfs(path: Path) -> bool:
        observed.append(path)
        raise PermissionError("protected procfs")

    monkeypatch.setattr(
        reaper,
        "_path_exists",
        protected_procfs,
        raising=False,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    assert reaper._process_cwd(os.getpid()) is None
    assert observed == [Path(f"/proc/{os.getpid()}/cwd")]


def test_owner_pid_rejects_missing_malformed_and_nonpositive_records(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "collection.lock"
    assert reaper._owner_pid(lock) is None

    for payload in ("owner=4\n", "pid=not-an-int\n", "pid=0\n"):
        lock.write_text(payload, encoding="utf-8")
        assert reaper._owner_pid(lock) is None


def test_process_cwd_uses_bounded_lsof_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reaper, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=f"p123\nn{tmp_path}\n",
        ),
    )
    assert reaper._process_cwd(123) == tmp_path.resolve()

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing lsof")),
    )
    assert reaper._process_cwd(123) is None


def test_assess_lock_handles_missing_and_current_process_owner(tmp_path: Path) -> None:
    lock = tmp_path / "collection.lock"
    missing = assess_lock(
        lock,
        namespace="project-a",
        project_root=tmp_path,
        process_table={},
    )
    assert missing.reason == "missing-lock"

    _lock(lock, f"pid={os.getpid()}\n")
    current = assess_lock(
        lock,
        namespace="project-a",
        project_root=tmp_path,
        process_table={
            os.getpid(): ProcessInfo(
                pid=os.getpid(),
                ppid=1,
                elapsed_secs=1000,
                command="untrusted command text",
            )
        },
        now=2000.0,
    )
    assert current.reason == "current-process-owner"


def test_reap_locks_rejects_namespace_mismatch_and_supports_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project_lock_root(tmp_path)
    lock = root / "collection.lock"
    _lock(lock, "pid=99999\n")
    assert reap_stale_locks(
        root,
        namespace="other-project",
        project_root=tmp_path,
        process_table={},
        now=2000.0,
        stale_after=0.0,
    ) == []

    monkeypatch.setattr(reaper, "snapshot_processes", lambda: {})
    assert reap_stale_locks(
        root,
        namespace="project-a",
        project_root=tmp_path,
        now=2000.0,
        stale_after=0.0,
        apply=False,
    ) == []
    assert lock.exists()


def test_stale_gate_refresh_filters_and_apply_signal_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/work/project-a")
    candidate = ProcessInfo(
        pid=321,
        ppid=1,
        elapsed_secs=1000,
        command="/work/project-a/scripts/collection_lock.py --resource gate-refresh",
    )
    table = {
        321: candidate,
        os.getpid(): ProcessInfo(os.getpid(), 1, 1000, "gate-refresh"),
        322: ProcessInfo(322, 2, 1000, "gate-refresh /work/project-a"),
        323: ProcessInfo(323, 1, 1, "gate-refresh /work/project-a"),
        324: ProcessInfo(324, 1, 1000, "/work/project-a/no-gate"),
    }
    assert [item.pid for item in stale_gate_refresh_roots(
        table,
        namespace="project-a",
        project_root=root,
        stale_after=300.0,
    )] == [321]

    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: signalled.append((pid, sig)),
    )
    assert reap_stale_gate_refresh_roots(
        table,
        namespace="project-a",
        project_root=root,
        stale_after=300.0,
        apply=True,
    ) == [321]
    assert signalled == [(321, signal.SIGTERM)]


def test_main_forwards_namespaced_roots_and_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "checkout"
    lock_root = tmp_path / "project-a"
    lock_root.mkdir()
    calls: list[tuple[str, bool, float]] = []

    monkeypatch.setattr(reaper, "project_root", lambda: root)
    monkeypatch.setattr(reaper, "project_namespace", lambda _root: "project-a")
    monkeypatch.setattr(reaper, "resource_root", lambda _root: lock_root)
    monkeypatch.setattr(reaper, "snapshot_processes", lambda: {})
    monkeypatch.setattr(
        reaper,
        "reap_stale_locks",
        lambda _root, **kwargs: calls.append(
            ("locks", bool(kwargs["apply"]), float(kwargs["stale_after"]))
        ),
    )
    monkeypatch.setattr(
        reaper,
        "reap_stale_gate_refresh_roots",
        lambda _table, **kwargs: calls.append(
            ("roots", bool(kwargs["apply"]), float(kwargs["stale_after"]))
        ),
    )

    assert reaper.main(["--apply", "--stale-after", "12.5"]) == 0
    assert calls == [("locks", True, 12.5), ("roots", True, 12.5)]


def test_static_analysis_and_direct_execution_use_distinct_import_paths() -> None:
    """Direct execution must not make mypy load one helper under two names."""

    for module in (reaper, collection_lock_module):
        module_file = module.__file__
        assert module_file is not None
        source = Path(module_file).read_text(encoding="utf-8")
        assert "if TYPE_CHECKING or __package__:" in source
        assert "except ModuleNotFoundError" not in source
