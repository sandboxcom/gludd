"""Branch coverage for project-scoped collection lock ownership."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import scripts.collection_lock as locks


def test_resource_paths_and_timeout_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "configured.lock"
    monkeypatch.setenv("GLUDD_COLLECTION_LOCK", str(configured))
    assert locks.default_resource_lock() == configured

    monkeypatch.delenv("GLUDD_COLLECTION_LOCK")
    monkeypatch.setattr(locks, "resource_path", lambda resource: tmp_path / resource)
    assert locks.default_collection_lock() == tmp_path / "collection"
    assert locks.default_resource_lock("gate-refresh") == tmp_path / "gate-refresh"

    monkeypatch.setenv("GLUDD_COLLECTION_LOCK_TIMEOUT", "7.5")
    assert locks.lock_timeout() == 7.5
    assert locks.lock_timeout("gate-refresh") == 7.5
    monkeypatch.setenv("GLUDD_GATE_REFRESH_LOCK_TIMEOUT", "2.5")
    assert locks.lock_timeout("gate-refresh") == 2.5


def test_collection_lock_validates_times_out_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "locks" / "collection.lock"
    with pytest.raises(ValueError, match="non-negative"), locks.collection_lock(
        path, timeout=-1
    ):
        pass
    with pytest.raises(ValueError, match="positive"), locks.collection_lock(
        path, poll_interval=0
    ):
        pass

    with locks.collection_lock(path, timeout=0) as acquired:
        assert acquired == path
        assert path.read_text(encoding="utf-8").startswith("pid=")
        with pytest.raises(
            TimeoutError, match="collection lock is busy"
        ), locks.collection_lock(path, timeout=0):
            pass

    with locks.collection_lock(path, timeout=0) as reacquired:
        assert reacquired == path


def test_run_locked_propagates_child_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["tool", "--check"]
    observed: list[list[str]] = []

    def completed(args: list[str], *, check: bool) -> subprocess.CompletedProcess[list[str]]:
        assert check is False
        observed.append(args)
        return subprocess.CompletedProcess(args, 23)

    monkeypatch.setattr(locks, "resource_path", lambda resource: tmp_path / resource)
    monkeypatch.setattr(subprocess, "run", completed)
    assert locks.run_locked(command, timeout=0) == 23
    assert observed == [command]


def test_main_validates_arguments_and_maps_busy_lock_to_temporary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert locks.main([]) == 2
    assert locks.main(["--resource", "gate-refresh"]) == 2
    assert locks.main(["tool"]) == 2

    observed: list[tuple[list[str], str]] = []

    def successful(command: list[str], *, resource: str) -> int:
        observed.append((command, resource))
        return 17

    monkeypatch.setattr(locks, "run_locked", successful)
    assert locks.main(["--resource", "gate-refresh", "--run", "tool", "arg"]) == 17
    assert observed == [(["tool", "arg"], "gate-refresh")]

    def busy(_command: list[str], *, resource: str) -> int:
        raise TimeoutError(resource)

    monkeypatch.setattr(locks, "run_locked", busy)
    assert locks.main(["--run", "tool"]) == 75
