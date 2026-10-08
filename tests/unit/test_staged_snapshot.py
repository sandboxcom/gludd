"""Tests for the shared, staged-content-safe Git snapshot boundary."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts import staged_snapshot


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=True
    )


def _repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "snapshot@example.invalid")
    _git(tmp_path, "config", "user.name", "Snapshot Test")
    source = tmp_path / "src"
    source.mkdir()
    (source / "module.py").write_text("COMMITTED = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "src/module.py")
    _git(tmp_path, "commit", "-qm", "seed")
    return source / "module.py"


def test_index_blob_and_changed_paths_ignore_unstaged_content(tmp_path: Path) -> None:
    module = _repository(tmp_path)
    module.write_text("STAGED = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "src/module.py")
    module.write_text("UNSTAGED = 3\n", encoding="utf-8")

    assert staged_snapshot.discover_staged_paths(tmp_path) == ("src/module.py",)
    blob = staged_snapshot.read_index_blob(tmp_path, "src/module.py")
    assert blob.mode == "100644"
    assert blob.data == b"STAGED = 2\n"


def test_materialized_ref_and_index_are_exact_distinct_snapshots(
    tmp_path: Path,
) -> None:
    module = _repository(tmp_path)
    module.write_text("STAGED = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "src/module.py")
    module.write_text("UNSTAGED = 3\n", encoding="utf-8")
    base = tmp_path / "base"
    current = tmp_path / "current"
    base.mkdir()
    current.mkdir()

    staged_snapshot.materialize_ref(tmp_path, "HEAD", base, ("src",))
    staged_snapshot.materialize_index(tmp_path, current, ("src",))

    assert (base / "src/module.py").read_text(encoding="utf-8") == "COMMITTED = 1\n"
    assert (current / "src/module.py").read_text(encoding="utf-8") == "STAGED = 2\n"


def test_changed_ref_paths_use_merge_base_and_are_sorted(tmp_path: Path) -> None:
    module = _repository(tmp_path)
    _git(tmp_path, "branch", "base")
    module.write_text("HEAD = 4\n", encoding="utf-8")
    (tmp_path / "src" / "added.py").write_text("ADDED = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "src")
    _git(tmp_path, "commit", "-qm", "change")

    assert staged_snapshot.discover_changed_paths(tmp_path, "base", "HEAD") == (
        "src/added.py",
        "src/module.py",
    )


def test_non_repository_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(staged_snapshot.SnapshotError, match="Git"):
        staged_snapshot.discover_staged_paths(tmp_path)


def test_tracked_inventory_is_nul_delimited_sorted_and_shared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=b"Makefile\x00src/app.py\x00",
            stderr=b"",
        )

    monkeypatch.setattr(staged_snapshot.subprocess, "run", fake_run)

    assert staged_snapshot.discover_tracked_paths(tmp_path) == (
        "Makefile",
        "src/app.py",
    )
    assert observed["command"] == ["git", "-C", str(tmp_path), "ls-files", "-z"]
    assert observed["kwargs"]["timeout"] == staged_snapshot.GIT_TIMEOUT_SECONDS
    assert observed["kwargs"]["shell"] is False


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (3, b""),
        (0, b"unterminated"),
        (0, b"same\x00same\x00"),
        (0, b"../escape\x00"),
        (0, b"z-last\x00a-first\x00"),
    ],
)
def test_invalid_tracked_inventory_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: bytes,
) -> None:
    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=b"bad")

    monkeypatch.setattr(staged_snapshot.subprocess, "run", fake_run)

    with pytest.raises(staged_snapshot.SnapshotError):
        staged_snapshot.discover_tracked_paths(tmp_path)


@pytest.mark.parametrize("failure", ["timeout", "os-error"])
def test_git_process_start_and_timeout_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["git"], 30)
        raise OSError("cannot execute")

    monkeypatch.setattr(staged_snapshot.subprocess, "run", fail)

    with pytest.raises(staged_snapshot.SnapshotError):
        staged_snapshot.discover_staged_paths(tmp_path)


def test_non_utf8_inventory_and_invalid_refs_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        staged_snapshot,
        "_git",
        lambda *_args, **_kwargs: b"bad-\xff\x00",
    )
    with pytest.raises(staged_snapshot.SnapshotError, match="non-UTF-8"):
        staged_snapshot.discover_staged_paths(tmp_path)

    for ref in ("", "bad\x00ref"):
        with pytest.raises(staged_snapshot.SnapshotError, match="Git ref"):
            staged_snapshot._resolve_commit(tmp_path, ref)


@pytest.mark.parametrize("resolved", [b"\xff", b"short\n", (b"g" * 40) + b"\n"])
def test_malformed_resolved_commit_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolved: bytes,
) -> None:
    monkeypatch.setattr(
        staged_snapshot,
        "_git",
        lambda *_args, **_kwargs: resolved,
    )

    with pytest.raises(staged_snapshot.SnapshotError):
        staged_snapshot._resolve_commit(tmp_path, "HEAD")
