"""Tests for the worktree-per-subagent dispatch protocol.

Verifies the Makefile targets `agent-worktree`, `agent-merge`, `agent-cleanup`,
and `agent-worktree-list` exist and that the create/teardown lifecycle actually
manipulates an isolated git worktree on disk. Mirrors the structural-scan style
of test_gate_background_targets.py and test_makefile_targets.py.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return MAKEFILE.read_text()


def _recipe(target: str) -> str:
    """Return the full recipe body for a make target (asserts existence)."""
    content = _content()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    nxt = content.find("\n\n", start)
    return content[start:nxt] if nxt != -1 else content[start:]


class TestAgentWorktreeTargetsExist:
    """The four worktree-per-subagent targets must be present in the Makefile."""

    def test_agent_worktree_target_exists(self):
        assert "agent-worktree:" in _content(), (
            "Makefile missing 'agent-worktree:' target"
        )

    def test_agent_merge_target_exists(self):
        assert "agent-merge:" in _content(), (
            "Makefile missing 'agent-merge:' target"
        )

    def test_agent_cleanup_target_exists(self):
        assert "agent-cleanup:" in _content(), (
            "Makefile missing 'agent-cleanup:' target"
        )

    def test_agent_worktree_list_target_exists(self):
        assert "agent-worktree-list:" in _content(), (
            "Makefile missing 'agent-worktree-list:' target"
        )


class TestAgentWorktreeRecipeShape:
    """Recipe bodies enforce the documented contract."""

    def test_agent_worktree_uses_git_worktree_add(self):
        recipe = _recipe("agent-worktree")
        assert "git worktree add" in recipe, (
            "agent-worktree must invoke `git worktree add` to create the checkout"
        )

    def test_agent_worktree_writes_worktree_path_marker(self):
        recipe = _recipe("agent-worktree")
        assert "WORKTREE_PATH=" in recipe, (
            "agent-worktree must print WORKTREE_PATH=<path> for the caller"
        )

    def test_agent_worktree_uses_quarantine_dir(self):
        recipe = _recipe("agent-worktree")
        assert "/tmp/gludd-worktrees/" in recipe, (
            "agent-worktree must place worktrees under /tmp/gludd-worktrees/ "
            "(not inside the shared master checkout)"
        )

    def test_agent_worktree_requires_branch_arg(self):
        recipe = _recipe("agent-worktree")
        assert "$(BRANCH)" in recipe, (
            "agent-worktree must consume a BRANCH=<name> argument"
        )

    def test_agent_merge_uses_no_ff(self):
        recipe = _recipe("agent-merge")
        assert "--no-ff" in recipe, (
            "agent-merge must use --no-ff (preserves branch topology per ORCHESTRATION.md)"
        )

    def test_agent_cleanup_removes_worktree_and_branch(self):
        recipe = _recipe("agent-cleanup")
        assert "git worktree remove" in recipe, (
            "agent-cleanup must remove the worktree"
        )
        assert "git branch -d" in recipe, (
            "agent-cleanup must delete the branch"
        )

    def test_agent_worktree_list_is_readonly(self):
        recipe = _recipe("agent-worktree-list")
        assert "git worktree list" in recipe, (
            "agent-worktree-list must invoke `git worktree list`"
        )


@pytest.mark.skipif(
    not MAKEFILE.exists(),
    reason="Makefile not present — cannot run end-to-end make targets",
)
class TestAgentWorktreeLifecycle:
    """End-to-end: actually create a worktree, then clean it up."""

    @pytest.fixture(autouse=True)
    def _branch(self) -> str:
        # Unique branch per test run so parallel pytest workers never collide.
        tag = uuid.uuid4().hex[:12]
        self.branch = f"agent-test-{tag}"
        yield self.branch
        # Hard cleanup: never leave a worktree behind on a test failure.
        wt = Path("/tmp/gludd-worktrees") / self.branch
        subprocess.run(
            ["git", "worktree", "remove", str(wt), "--force"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-D", self.branch],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
        )

    def test_agent_worktree_creates_isolated_checkout(self):
        result = subprocess.run(
            ["make", "--no-print-directory", "agent-worktree", f"BRANCH={self.branch}"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"agent-worktree failed (rc={result.returncode}):\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        # The target must print WORKTREE_PATH=<path>.
        assert "WORKTREE_PATH=" in result.stdout, (
            f"agent-worktree output missing WORKTREE_PATH= marker: {result.stdout!r}"
        )
        wt_path = next(
            line.split("=", 1)[1].strip()
            for line in result.stdout.splitlines()
            if line.startswith("WORKTREE_PATH=")
        )
        wt = Path(wt_path)
        # The worktree exists on disk.
        assert wt.exists() and wt.is_dir(), (
            f"worktree path {wt} was not created on disk"
        )
        # It is a valid git checkout with its own HEAD.
        head = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(wt),
            capture_output=True,
            text=True,
            check=False,
        )
        assert head.returncode == 0 and head.stdout.strip() == self.branch, (
            f"worktree HEAD is not on branch {self.branch}: "
            f"rc={head.returncode} out={head.stdout!r}"
        )
        # The worktree is linked to the repo (shows up in `git worktree list`).
        listing = subprocess.run(
            ["git", "worktree", "list"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        assert str(wt) in listing.stdout, (
            f"worktree {wt} not registered in `git worktree list`:\n{listing.stdout}"
        )

    def test_agent_cleanup_removes_worktree(self):
        # Create first, then clean up.
        create = subprocess.run(
            ["make", "--no-print-directory", "agent-worktree", f"BRANCH={self.branch}"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert create.returncode == 0, f"setup agent-worktree failed: {create.stderr}"
        wt = Path("/tmp/gludd-worktrees") / self.branch
        assert wt.exists(), "precondition: worktree should exist before cleanup"

        cleanup = subprocess.run(
            ["make", "--no-print-directory", "agent-cleanup", f"BRANCH={self.branch}"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        assert cleanup.returncode == 0, (
            f"agent-cleanup failed (rc={cleanup.returncode}):\n"
            f"stdout={cleanup.stdout}\nstderr={cleanup.stderr}"
        )
        assert not wt.exists(), (
            f"agent-cleanup did not remove worktree path {wt}"
        )
        # Branch is deleted too.
        branch_check = subprocess.run(
            ["git", "rev-parse", "--verify", self.branch],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert branch_check.returncode != 0, (
            f"agent-cleanup did not delete branch {self.branch}"
        )
