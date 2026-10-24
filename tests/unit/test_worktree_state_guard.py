"""Tests for scripts/worktree_state_guard.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.worktree_state_guard import (
    all_worktree_states,
    current_state,
    list_worktree_paths,
    parse_worktree_paths,
)

SAMPLE_PORCELAIN = """worktree /Users/shawnwilson/gludd
HEAD 4cfccef64e3dbeec9d3dbbadced9f983c13c2a3d
branch refs/heads/release-candidate/v012-git-mutex-20261022

worktree /private/tmp/gludd-worktrees/agent-v012-coverage-floor
HEAD 0344d12fa95180f43bf07eedd130428da6c30fc
branch refs/heads/agent-v012-coverage-floor
prunable

worktree /private/tmp/gludd-worktrees/feature/v012-checkpoint-link-repair-20261024
HEAD 2289dce9cbad2865bde97b223088cad81c147096
branch refs/heads/feature/v012-checkpoint-link-repair-20261024
"""


def _completed_process(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_parse_worktree_paths_extracts_paths() -> None:
    paths = parse_worktree_paths(SAMPLE_PORCELAIN)
    assert paths == [
        "/Users/shawnwilson/gludd",
        "/private/tmp/gludd-worktrees/agent-v012-coverage-floor",
        "/private/tmp/gludd-worktrees/feature/v012-checkpoint-link-repair-20261024",
    ]


def test_parse_worktree_paths_empty() -> None:
    assert parse_worktree_paths("") == []


def test_list_worktree_paths_uses_run() -> None:
    def fake_run(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        assert argv == ["git", "worktree", "list", "--porcelain"]
        return _completed_process(SAMPLE_PORCELAIN)

    assert list_worktree_paths(run=fake_run) == parse_worktree_paths(SAMPLE_PORCELAIN)


def test_current_state_returns_worktree_state(tmp_path: Path) -> None:
    # current_state is exercised against a real git repo created in tmp_path.
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=tmp_path, check=True, capture_output=True)

    state = current_state(cwd=str(tmp_path))

    assert state.path == str(tmp_path)
    assert state.branch == "feature"
    assert len(state.head) == 40
    assert state.dirty_count == 0
    assert state.staged_count == 0
    assert state.untracked_count == 0
    assert state.status == []


def test_all_worktree_states_survives_missing_directory() -> None:
    """A worktree whose directory was removed must not crash the inventory."""

    def fake_run(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ["git", "worktree", "list"]:
            return _completed_process(SAMPLE_PORCELAIN)
        if argv[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _completed_process(cwd)
        if argv[:2] == ["git", "branch"]:
            return _completed_process("feature-branch")
        if argv[:3] == ["git", "rev-parse", "--verify"]:
            return _completed_process("2289dce9cbad2865bde97b223088cad81c147096")  # pragma: allowlist secret
        if argv[:2] == ["git", "status"]:
            return _completed_process("")
        return _completed_process("")

    states = all_worktree_states(run=fake_run)

    paths = {state.path for state in states}
    assert "/Users/shawnwilson/gludd" in paths
    assert "/private/tmp/gludd-worktrees/agent-v012-coverage-floor" in paths
    assert "/private/tmp/gludd-worktrees/feature/v012-checkpoint-link-repair-20261024" in paths

    missing = next(state for state in states if state.path == "/private/tmp/gludd-worktrees/agent-v012-coverage-floor")
    assert missing.branch == "UNKNOWN"
    assert missing.head == ""
    assert missing.dirty_count == -1
    assert any("does not exist" in line for line in missing.status)


def test_all_worktree_states_survives_git_failure() -> None:
    def fake_run(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ["git", "worktree", "list"]:
            return _completed_process(SAMPLE_PORCELAIN)
        if cwd == "/private/tmp/gludd-worktrees/feature/v012-checkpoint-link-repair-20261024" and argv[:3] == [
            "git",
            "rev-parse",
            "--show-toplevel",
        ]:
            return _completed_process("fatal: not a git repo", returncode=128)
        if argv[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _completed_process(cwd)
        if argv[:2] == ["git", "branch"]:
            return _completed_process("feature-branch")
        if argv[:3] == ["git", "rev-parse", "--verify"]:
            return _completed_process("2289dce9cbad2865bde97b223088cad81c147096")  # pragma: allowlist secret
        if argv[:2] == ["git", "status"]:
            return _completed_process("")
        return _completed_process("")

    states = all_worktree_states(run=fake_run)
    broken = next(
        state
        for state in states
        if state.path == "/private/tmp/gludd-worktrees/feature/v012-checkpoint-link-repair-20261024"
    )
    assert broken.branch == "UNKNOWN"
    assert broken.dirty_count == -1
    assert any(("not a git repo" in line) or ("does not exist" in line) for line in broken.status)
