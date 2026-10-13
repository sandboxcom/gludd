"""Exercise the push guard against real Git state, not a success stub."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[2] / "scripts" / "check_clean_tree.py"


def _git(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Return a committed, clean repository isolated from user Git settings."""
    _git(tmp_path, "init", "--initial-branch=development")
    (tmp_path / "feature.txt").write_text("original\n", encoding="utf-8")
    _git(tmp_path, "add", "feature.txt")
    _git(
        tmp_path,
        "-c",
        "user.name=Gludd guard regression",
        "-c",
        "user.email=guard-test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "fixture",
    )
    return tmp_path


def _guard(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_committed_clean_repository_emits_native_evidence(repository: Path) -> None:
    """A success must contain the existing owner's checked worktree identity."""
    result = _guard(repository)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WORKTREE-CLEAN" in result.stdout
    assert "branch=development" in result.stdout
    assert "dirty=0" in result.stdout
    assert "head=" in result.stdout


def test_entrypoint_delegates_in_process(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the actual entrypoint in the coverage-owning test process too."""
    monkeypatch.chdir(repository)
    monkeypatch.setattr(sys, "path", [str(GUARD.parent), *sys.path])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(GUARD), run_name="__main__")
    assert result.value.code == 0


@pytest.mark.parametrize("change", ["modified", "staged", "untracked", "deleted"])
def test_dirty_repository_blocks_push(repository: Path, change: str) -> None:
    """Reject every Git status class without mutating or hiding the changes."""
    feature = repository / "feature.txt"
    if change in {"modified", "staged"}:
        feature.write_text("changed\n", encoding="utf-8")
        if change == "staged":
            _git(repository, "add", "feature.txt")
    elif change == "untracked":
        (repository / "new-feature.txt").write_text("new\n", encoding="utf-8")
    else:
        feature.unlink()
    before = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result = _guard(repository)
    assert result.returncode != 0
    assert "WORKTREE-DIRTY" in result.stdout
    assert "WORKTREE-CLEAN" not in result.stdout
    after = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert after == before


def test_unknown_repository_fails_closed(tmp_path: Path) -> None:
    """Git failures must be visible failures, never clean evidence."""
    result = _guard(tmp_path)
    assert result.returncode != 0
    assert "WORKTREE-UNKNOWN error=" in result.stdout
    assert "WORKTREE-CLEAN" not in result.stdout
