"""Fail-closed branch coverage for filesystem Git release evidence."""

from __future__ import annotations

import builtins
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest

from general_ludd.git_release import evidence


def test_git_dir_resolves_worktree_pointer_and_rejects_unreadable_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid linked-worktree pointer resolves; read failure returns no evidence."""
    repo = tmp_path / "repo"
    common = tmp_path / "common"
    repo.mkdir()
    common.mkdir()
    (repo / ".git").write_text(f"gitdir: {common}\n", encoding="utf-8")

    assert evidence._git_dir(str(repo)) == str(common)

    def deny_open(*args: object, **kwargs: object) -> NoReturn:
        raise OSError("permission denied")

    monkeypatch.setattr(builtins, "open", deny_open)
    assert evidence._git_dir(str(repo)) is None


def test_read_head_returns_empty_when_metadata_disappears(tmp_path: Path) -> None:
    """Concurrent removal of HEAD yields an explicit empty snapshot."""
    assert evidence._read_head(str(tmp_path)) == ("", "", False)


def test_resolve_head_sha_rejects_process_failure_and_invalid_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Git and malformed output both become an absent SHA."""

    def fail_run(*args: object, **kwargs: object) -> NoReturn:
        raise OSError("git unavailable")

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert evidence._resolve_head_sha("/repo") == ""

    def invalid_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(["git"], 0, stdout="not-a-sha\n", stderr="")

    monkeypatch.setattr(subprocess, "run", invalid_run)
    assert evidence._resolve_head_sha("/repo") == ""


def test_operation_marker_short_circuits_dirty_detection(tmp_path: Path) -> None:
    """An in-flight Git operation is dirty without invoking a diff subprocess."""
    (tmp_path / "MERGE_HEAD").touch()

    assert evidence._has_untracked(str(tmp_path)) is True
    assert evidence._is_dirty("/repo", str(tmp_path)) is True


def test_dirty_detection_fails_closed_when_git_cannot_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without filesystem markers, unavailable Git does not invent dirty state."""

    def fail_run(*args: object, **kwargs: object) -> NoReturn:
        raise subprocess.SubprocessError("failed")

    monkeypatch.setattr(subprocess, "run", fail_run)

    assert evidence._is_dirty("/repo", str(tmp_path)) is False
