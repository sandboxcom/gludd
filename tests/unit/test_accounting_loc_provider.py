"""Tests for the accounting loc_changed git-numstat integration.

Covers:
  * a real temp git repo with a known working-tree diff -> expected loc sum
  * a non-git directory -> 0 (fail-safe)
  * a missing path / None -> 0 (fail-safe)
  * _project_repo_dir resolving workspace_path -> <workspace>/repo
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.routers.accounting import _project_loc_changed, _project_repo_dir

_GIT = shutil.which("git")
_needs_git = pytest.mark.skipif(_GIT is None, reason="git not available")


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@_needs_git
def test_loc_changed_counts_added_and_deleted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "t@example.com")
    _run_git(repo, "config", "user.name", "Tester")

    # Commit a baseline file with 3 lines.
    target = repo / "file.txt"
    target.write_text("a\nb\nc\n")
    _run_git(repo, "add", "file.txt")
    _run_git(repo, "commit", "-m", "baseline")

    # Working-tree change: delete the 3 lines, add 2 new ones.
    # git diff HEAD --numstat reports added=2, deleted=3 -> sum 5.
    target.write_text("x\ny\n")

    assert _project_loc_changed(repo) == 5


@_needs_git
def test_loc_changed_clean_tree_is_zero(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "t@example.com")
    _run_git(repo, "config", "user.name", "Tester")
    (repo / "f.txt").write_text("one\n")
    _run_git(repo, "add", "f.txt")
    _run_git(repo, "commit", "-m", "c")

    assert _project_loc_changed(repo) == 0


def test_loc_changed_non_git_dir_is_zero(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "data.txt").write_text("hello\n")
    assert _project_loc_changed(plain) == 0


def test_loc_changed_missing_path_is_zero(tmp_path: Path) -> None:
    assert _project_loc_changed(tmp_path / "does-not-exist") == 0


def test_loc_changed_none_is_zero() -> None:
    assert _project_loc_changed(None) == 0


def test_loc_changed_empty_str_is_zero() -> None:
    assert _project_loc_changed("") == 0


def test_project_repo_dir_resolves_workspace_repo(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = SimpleNamespace(workspace_path=str(workspace))
    pm = SimpleNamespace(get_project=lambda pid: project)
    app = SimpleNamespace(state=SimpleNamespace(_project_manager=pm))

    resolved = _project_repo_dir(app, "proj-1")
    assert resolved == workspace / "repo"


def test_project_repo_dir_none_when_no_manager() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    assert _project_repo_dir(app, "proj-1") is None


def test_project_repo_dir_none_when_project_missing() -> None:
    pm = SimpleNamespace(get_project=lambda pid: None)
    app = SimpleNamespace(state=SimpleNamespace(_project_manager=pm))
    assert _project_repo_dir(app, "proj-x") is None


def test_project_repo_dir_none_when_no_workspace_path() -> None:
    project = SimpleNamespace(workspace_path="")
    pm = SimpleNamespace(get_project=lambda pid: project)
    app = SimpleNamespace(state=SimpleNamespace(_project_manager=pm))
    assert _project_repo_dir(app, "proj-1") is None


@_needs_git
def test_loc_changed_skips_binary_rows(tmp_path: Path) -> None:
    """Binary files report '-' in numstat; they must be skipped, not crash."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "t@example.com")
    _run_git(repo, "config", "user.name", "Tester")

    text = repo / "t.txt"
    text.write_text("a\n")
    binary = repo / "b.bin"
    binary.write_bytes(b"\x00\x01\x02")
    _run_git(repo, "add", "t.txt", "b.bin")
    _run_git(repo, "commit", "-m", "c")

    # Modify both: +1 line in text, binary changes (numstat '-' '-').
    text.write_text("a\nb\n")
    binary.write_bytes(b"\x00\x01\x02\x03\x04")

    # Only the text line counts: added=1, deleted=0 -> 1.
    assert _project_loc_changed(repo) == 1


# ---------------------------------------------------------------------------
# Tests: GitAutomation.lines_changed_in_commit (per-commit delta primitive)
# ---------------------------------------------------------------------------


@_needs_git
def test_lines_changed_in_commit_counts_added_plus_deleted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "t@example.com")
    _run_git(repo, "config", "user.name", "Tester")
    (repo / "f.txt").write_text("a\nb\nc\n")
    _run_git(repo, "add", "f.txt")
    _run_git(repo, "commit", "-m", "baseline")

    # Second commit deletes the 3 baseline lines and adds 2 -> delta 5.
    (repo / "f.txt").write_text("x\ny\n")
    _run_git(repo, "add", "f.txt")
    _run_git(repo, "commit", "-m", "change")

    ga = GitAutomation(repo_path=str(repo))
    assert ga.lines_changed_in_commit() == 5


@_needs_git
def test_lines_changed_in_commit_clean_commit_is_zero(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "t@example.com")
    _run_git(repo, "config", "user.name", "Tester")
    # Empty commit introduces no line changes.
    _run_git(repo, "commit", "--allow-empty", "-m", "empty")
    ga = GitAutomation(repo_path=str(repo))
    assert ga.lines_changed_in_commit() == 0


@_needs_git
def test_lines_changed_in_commit_skips_binary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "t@example.com")
    _run_git(repo, "config", "user.name", "Tester")
    (repo / "t.txt").write_text("a\n")
    (repo / "b.bin").write_bytes(b"\x00\x01")
    _run_git(repo, "add", "t.txt", "b.bin")
    _run_git(repo, "commit", "-m", "base")

    # Text +1 line; binary changes (numstat '- -'); only text counts -> 1.
    (repo / "t.txt").write_text("a\nb\n")
    (repo / "b.bin").write_bytes(b"\x00\x01\x02\x03")
    _run_git(repo, "add", "t.txt", "b.bin")
    _run_git(repo, "commit", "-m", "mixed")

    ga = GitAutomation(repo_path=str(repo))
    assert ga.lines_changed_in_commit() == 1


def test_lines_changed_in_commit_non_repo_is_zero(tmp_path: Path) -> None:
    ga = GitAutomation(repo_path=str(tmp_path / "does-not-exist"))
    assert ga.lines_changed_in_commit() == 0
