"""Deep worktree agent and isolation tests.

Covers worktree isolation guarantees, venv sharing, cross-worktree communication,
agent metadata propagation, and cleanup on failure — verifying the contract in
docs/ORCHESTRATION.md and the worktree lifecycle in src/general_ludd/git_automation/.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.git_automation.types import MergeResult, WorktreeInfo, WorktreeResult
from general_ludd.git_automation.worktree import (
    WorktreeHealthViolation,
    _branch_is_merged,
    _branch_on_remote,
    _get_tree_age_seconds,
    _reject_leading_dash,
    worktree_cleanup,
    worktree_create,
    worktree_health_check,
    worktree_list,
    worktree_merge,
    worktree_merge_all,
)
from general_ludd.git_automation.worktree_lease import (
    check_worktree_lease,
    cleanup_expired_leases,
    is_pid_alive,
    release_worktree_lease,
    verify_worktree_lease,
    worktree_lease_info,
    write_worktree_lease,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _git_success(stdout: str = "", rc: int = 0, stderr: str = "") -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = rc
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _git_fail(stderr: str = "fatal: something went wrong", rc: int = 1) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = rc
    proc.stdout = ""
    proc.stderr = stderr
    return proc


_WT_MAIN_ONLY = """worktree /Users/shawnwilson/gludd
HEAD abc123def4567890123456789012345678abcdef
branch refs/heads/development
"""

_WT_MAIN_PLUS_ONE = """worktree /Users/shawnwilson/gludd
HEAD abc123def4567890123456789012345678abcdef
branch refs/heads/development

worktree /tmp/gludd-worktrees/agent-deep
HEAD def4567890123456789012345678abcdef012345
branch refs/heads/agent-deep
"""

_WT_MAIN_PLUS_THREE = """worktree /Users/shawnwilson/gludd
HEAD abc123def4567890123456789012345678abcdef
branch refs/heads/development

worktree /tmp/gludd-worktrees/agent-alpha
HEAD aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
branch refs/heads/agent-alpha

worktree /tmp/gludd-worktrees/agent-beta
HEAD bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
branch refs/heads/agent-beta

worktree /tmp/gludd-worktrees/agent-gamma
HEAD cccccccccccccccccccccccccccccccccccccccc
branch refs/heads/agent-gamma
"""

ROOT = Path(__file__).parent.parent.parent


# ── _reject_leading_dash ─────────────────────────────────────────────────────


class TestRejectLeadingDash:
    def test_allows_normal_string(self) -> None:
        result = _reject_leading_dash("agent-fix-42", "branch")
        assert result == "agent-fix-42"

    def test_rejects_leading_dash(self) -> None:
        with pytest.raises(ValueError, match=r"refusing branch that begins with '-'"):
            _reject_leading_dash("--force", "branch")

    def test_rejects_leading_dash_on_worktree_path(self) -> None:
        with pytest.raises(ValueError, match="refusing worktree path"):
            _reject_leading_dash("--hard", "worktree path")


# ── worktree_create isolation guarantees ──────────────────────────────────────


class TestWorktreeCreateIsolation:
    """Each worktree must be fully independent — no shared checkout collisions."""

    def test_create_returns_worktree_result_on_success(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        (tmp_path / "fake-repo").mkdir(exist_ok=True)

        def _fake_run_git(
            *args: str, cwd: str, **kwargs: Any
        ) -> MagicMock:
            return _git_success(stdout="Preparing worktree (new branch 'agent-iso')\nHEAD is now at abc1234")

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: tmp_path / "worktrees"),
        )

        result = worktree_create(repo, "agent-iso")
        assert result.success is True
        assert result.branch == "agent-iso"

    def test_create_rejects_path_traversal_branch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: tmp_path / "worktrees"),
        )

        result = worktree_create(str(tmp_path), "../../escape")
        assert result.success is False
        assert "escapes worktree root" in result.message

    def test_create_rejects_leading_dash_branch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )

        result = worktree_create("/Users/shawnwilson/gludd", "--all")
        assert result.success is False
        assert "begins with '-'" in result.message

    def test_create_handles_git_failure_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        (tmp_path / "fake-repo").mkdir(exist_ok=True)

        def _fake_run_git(
            *args: str, cwd: str, **kwargs: Any
        ) -> MagicMock:
            return _git_fail(stderr="fatal: already checked out at /other/path")

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: tmp_path / "worktrees"),
        )

        result = worktree_create(repo, "agent-conflict")
        assert result.success is False
        assert "already checked out" in result.message

    def test_create_handles_timeout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        (tmp_path / "fake-repo").mkdir(exist_ok=True)

        def _fake_run_git(
            *args: str, cwd: str, **kwargs: Any
        ) -> MagicMock:
            raise subprocess.TimeoutExpired(cmd=["git", "worktree", "add"], timeout=60)

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: tmp_path / "worktrees"),
        )

        result = worktree_create(repo, "agent-slow")
        assert result.success is False
        assert "timed out" in result.message

    def test_create_uses_base_branch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        (tmp_path / "fake-repo").mkdir(exist_ok=True)
        calls = []

        def _fake_run_git(
            *args: str, cwd: str, **kwargs: Any
        ) -> MagicMock:
            calls.append(args)
            return _git_success(stdout="HEAD is now at def5678")

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: tmp_path / "worktrees"),
        )

        result = worktree_create(repo, "agent-release", base_branch="sandboxcom/master")
        assert result.success is True
        assert "sandboxcom/master" in calls[0]


# ── worktree merge isolation ──────────────────────────────────────────────────


class TestWorktreeMergeIsolation:
    """Merges must run on the main checkout and never from inside a worktree."""

    def test_merge_success_with_no_ff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        responses = iter(
            [
                _git_success(stdout="development"),  # rev-parse HEAD
                _git_success(stdout="Switched to branch 'development'"),  # checkout
                _git_success(stdout="Merge made by the 'ort' strategy."),  # merge
            ]
        )

        def _fake_run_git(*args: str, **kwargs: Any) -> MagicMock:
            return next(responses)

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )

        result = worktree_merge("/Users/shawnwilson/gludd", "agent-merge-test")
        assert result.success is True
        assert result.strategy == "no-ff"

    def test_merge_rejects_leading_dash_source(self) -> None:
        result = worktree_merge("/Users/shawnwilson/gludd", "--force")
        assert result.success is False
        assert "begins with '-'" in result.message

    def test_merge_rejects_leading_dash_target(self) -> None:
        result = worktree_merge("/Users/shawnwilson/gludd", "agent-ok", target_branch="--all")
        assert result.success is False
        assert "begins with '-'" in result.message

    def test_merge_handles_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        responses = iter(
            [
                _git_success(stdout="development"),  # rev-parse
                _git_success(stdout="Switched to branch 'development'"),  # checkout
                _git_fail(stderr="CONFLICT (content): Merge conflict in daemon.py", rc=1),  # merge
                _git_success(stdout="ok"),  # merge --abort
            ]
        )

        def _fake_run_git(*args: str, **kwargs: Any) -> MagicMock:
            return next(responses)

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )

        result = worktree_merge("/Users/shawnwilson/gludd", "agent-conflict")
        assert result.success is False
        assert result.conflicts == ["agent-conflict"]

    def test_merge_restores_original_branch_on_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prev_branch = "feature/side-work"
        responses = iter(
            [
                _git_success(stdout=prev_branch),
                _git_success(stdout="Switched to branch 'development'"),
                _git_fail(stderr="error: Your local changes would be overwritten", rc=1),
                _git_success(stdout="Aborting"),
            ]
        )
        checkout_args = []

        def _fake_run_git(*args: str, **kwargs: Any) -> MagicMock:
            if args[0] == "checkout":
                checkout_args.append(args[1])
            return next(responses)

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )

        result = worktree_merge("/Users/shawnwilson/gludd", "agent-broken")
        assert result.success is False
        assert checkout_args[-1] == prev_branch


# ── worktree cleanup on failure ───────────────────────────────────────────────


class TestWorktreeCleanupOnFailure:
    """Cleanup must handle partial states: missing worktrees, locked worktrees,
    already-deleted branches."""

    def test_cleanup_success_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        responses = iter(
            [
                _git_success(stdout=""),  # worktree remove
                _git_success(stdout=""),  # worktree prune
                _git_success(stdout="Deleted branch agent-old (was def5678)."),  # branch -d
            ]
        )

        def _fake_run_git(*args: str, **kwargs: Any) -> MagicMock:
            return next(responses)

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: tmp_path / "worktrees"),
        )

        result = worktree_cleanup(repo, "agent-old")
        assert result["success"] is True
        assert result["branch_removed"] is True
        assert result["cleaned"] is True

    def test_cleanup_handles_missing_worktree_disk(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        responses = iter(
            [
                _git_fail(stderr="fatal: 'agent-gone' is not a working tree", rc=128),
                _git_success(stdout=""),  # worktree prune
                _git_success(stdout="Deleted branch agent-gone."),
            ]
        )

        def _fake_run_git(*args: str, **kwargs: Any) -> MagicMock:
            return next(responses)

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: tmp_path / "worktrees"),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.os.path.isdir",
            lambda p: False,
        )

        result = worktree_cleanup(repo, "agent-gone")
        assert result["success"] is True
        assert result["cleaned"] is False
        assert result["branch_removed"] is True

    def test_cleanup_rejects_path_traversal(self, tmp_path: Path) -> None:
        result = worktree_cleanup(str(tmp_path), "../escape")
        assert result["success"] is False
        assert "escapes worktree root" in str(result.get("error", ""))

    def test_cleanup_handles_locked_worktree(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        responses = iter(
            [
                _git_fail(stderr="fatal: 'agent-locked' contains modified or untracked files", rc=128),
                _git_success(stdout=""),  # unlock
                _git_success(stdout=""),  # prune
                _git_success(stdout="Deleted branch agent-locked."),
            ]
        )

        def _fake_run_git(*args: str, **kwargs: Any) -> MagicMock:
            return next(responses)

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: tmp_path / "worktrees"),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.os.path.isdir",
            lambda p: True,
        )

        result = worktree_cleanup(repo, "agent-locked")
        assert result["success"] is True


# ── worktree list parsing ─────────────────────────────────────────────────────


class TestWorktreeList:
    """Porcelain parsing must correctly identify main vs agent worktrees."""

    def test_main_only_worktree_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_success(stdout=_WT_MAIN_ONLY),
        )

        wts = worktree_list("/Users/shawnwilson/gludd")
        assert len(wts) == 1
        assert wts[0].is_main is True
        assert wts[0].branch == "refs/heads/development"

    def test_main_plus_agent_worktree_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_success(stdout=_WT_MAIN_PLUS_ONE),
        )

        wts = worktree_list("/Users/shawnwilson/gludd")
        assert len(wts) == 2
        main = [w for w in wts if w.is_main]
        agents = [w for w in wts if not w.is_main]
        assert len(main) == 1
        assert len(agents) == 1
        assert agents[0].branch == "refs/heads/agent-deep"
        assert agents[0].commit == "def4567890123456789012345678abcdef012345"

    def test_multiple_agent_worktrees_distinct_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_success(stdout=_WT_MAIN_PLUS_THREE),
        )

        wts = worktree_list("/Users/shawnwilson/gludd")
        agents = [w for w in wts if not w.is_main]
        assert len(agents) == 3
        commits = {w.commit for w in agents}
        assert len(commits) == 3


# ── worktree merge_all bulk operations ────────────────────────────────────────


class TestWorktreeMergeAll:
    """Bulk merge must iterate all agent worktrees, handle already-merged and
    conflict cases individually without aborting the batch."""

    def test_merge_all_no_agent_worktrees(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.worktree_list",
            lambda rp: [
                WorktreeInfo(
                    path="/Users/shawnwilson/gludd",
                    branch="refs/heads/development",
                    is_main=True,
                    commit="abc123",
                )
            ],
        )
        result = worktree_merge_all("/Users/shawnwilson/gludd")
        assert result["total"] == 0
        assert result["merged"] == 0

    def test_merge_all_already_merged_skips(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.worktree_list",
            lambda rp: [
                WorktreeInfo(
                    path="/Users/shawnwilson/gludd",
                    branch="refs/heads/development",
                    is_main=True,
                    commit="abc123",
                ),
                WorktreeInfo(
                    path="/tmp/gludd-worktrees/agent-done",
                    branch="refs/heads/agent-done",
                    is_main=False,
                    commit="def456",
                ),
            ],
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._branch_is_merged",
            lambda rp, br, tgt: True,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.worktree_cleanup",
            lambda rp, br, root=None: {"success": root == str(tmp_path)},
        )

        result = worktree_merge_all("/Users/shawnwilson/gludd", worktree_root=str(tmp_path))
        assert result["total"] == 1
        assert result["skipped"] == 1

    def test_merge_all_conflict_reported(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.worktree_list",
            lambda rp: [
                WorktreeInfo(
                    path="/Users/shawnwilson/gludd",
                    branch="refs/heads/development",
                    is_main=True,
                    commit="abc123",
                ),
                WorktreeInfo(
                    path="/tmp/gludd-worktrees/agent-clash",
                    branch="refs/heads/agent-clash",
                    is_main=False,
                    commit="fedcba",
                ),
                WorktreeInfo(
                    path="/tmp/gludd-worktrees/agent-ok",
                    branch="refs/heads/agent-ok",
                    is_main=False,
                    commit="123456",
                ),
            ],
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._branch_is_merged",
            lambda rp, br, tgt: False,
        )

        def _fake_merge(rp: str, br: str, tb: str) -> MergeResult:
            if br == "agent-clash":
                return MergeResult(success=False, strategy="no-ff", conflicts=["agent-clash"], message="CONFLICT")
            return MergeResult(success=True, strategy="no-ff", message="ok")

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.worktree_merge",
            _fake_merge,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.worktree_cleanup",
            lambda rp, br, root=None: {"success": root == str(tmp_path)},
        )

        result = worktree_merge_all("/Users/shawnwilson/gludd", worktree_root=str(tmp_path))
        assert result["total"] == 2
        assert result["merged"] == 1
        assert result["conflicts"] == 1


# ── worktree health check ─────────────────────────────────────────────────────


class TestWorktreeHealthCheck:
    """Health violations: stale+unmerged, missing remote, stale+merged cleanup needed."""

    def test_healthy_main_only_no_violations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.worktree_list",
            lambda rp: [
                WorktreeInfo(
                    path="/Users/shawnwilson/gludd",
                    branch="refs/heads/development",
                    is_main=True,
                    commit="abc123",
                ),
            ],
        )
        violations = worktree_health_check("/Users/shawnwilson/gludd")
        assert len(violations) == 0

    def test_stale_unmerged_worktree_is_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.worktree_list",
            lambda rp: [
                WorktreeInfo(
                    path="/Users/shawnwilson/gludd",
                    branch="refs/heads/development",
                    is_main=True,
                    commit="abc123",
                ),
                WorktreeInfo(
                    path="/tmp/gludd-worktrees/agent-ancient",
                    branch="refs/heads/agent-ancient",
                    is_main=False,
                    commit="ccc111",
                ),
            ],
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._get_tree_age_seconds",
            lambda p: 172800.0,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._branch_is_merged",
            lambda rp, br, tgt: False,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._branch_on_remote",
            lambda rp, br, rm: True,
        )

        violations = worktree_health_check("/Users/shawnwilson/gludd", max_age_hours=24)
        assert len(violations) == 1
        assert violations[0].severity == "error"
        assert "Stale >24h" in violations[0].reason

    def test_merged_but_stale_worktree_is_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.worktree_list",
            lambda rp: [
                WorktreeInfo(
                    path="/Users/shawnwilson/gludd",
                    branch="refs/heads/development",
                    is_main=True,
                ),
                WorktreeInfo(
                    path="/tmp/gludd-worktrees/agent-merged-old",
                    branch="refs/heads/agent-merged-old",
                    is_main=False,
                    commit="abc123",
                ),
            ],
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._get_tree_age_seconds",
            lambda p: 200000.0,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._branch_is_merged",
            lambda rp, br, tgt: True,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._branch_on_remote",
            lambda rp, br, rm: True,
        )

        violations = worktree_health_check("/Users/shawnwilson/gludd", max_age_hours=24)
        assert len(violations) == 1
        assert violations[0].severity == "warning"
        assert "already merged" in violations[0].reason


# ── worktree lease lifecycle ──────────────────────────────────────────────────


class TestWorktreeLease:
    """Leases gate cleanup of active worktrees. Expired leases must not block."""

    def test_write_and_check_lease(self, tmp_path: Path) -> None:
        repo = str(tmp_path)
        lease_path = write_worktree_lease(repo, "agent-lease-1", ttl_seconds=300)
        assert lease_path.exists()
        assert lease_path.stat().st_mode & 0o777 == 0o600
        assert check_worktree_lease(repo, "agent-lease-1") is True

    def test_expired_lease_returns_false(self, tmp_path: Path) -> None:
        repo = str(tmp_path)
        write_worktree_lease(repo, "agent-expired", ttl_seconds=-1)
        assert check_worktree_lease(repo, "agent-expired") is False

    def test_missing_lease_returns_false(self, tmp_path: Path) -> None:
        repo = str(tmp_path)
        assert check_worktree_lease(repo, "agent-never-existed") is False

    def test_release_and_check_removes_lease(self, tmp_path: Path) -> None:
        repo = str(tmp_path)
        write_worktree_lease(repo, "agent-temp", ttl_seconds=600)
        assert check_worktree_lease(repo, "agent-temp") is True
        release_worktree_lease(repo, "agent-temp")
        assert check_worktree_lease(repo, "agent-temp") is False

    def test_verify_lease_with_alive_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = str(tmp_path)
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree_lease.is_pid_alive",
            lambda pid: pid == 99999,
        )
        lease_path = write_worktree_lease(repo, "agent-alive", ttl_seconds=300)
        raw = json.loads(lease_path.read_text())
        raw["owner_pid"] = 99999
        lease_path.write_text(json.dumps(raw))

        result = verify_worktree_lease(repo, "agent-alive")
        assert result["owned"] is True
        assert result["owner_pid"] == 99999

    def test_verify_lease_with_dead_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = str(tmp_path)
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree_lease.is_pid_alive",
            lambda pid: False,
        )
        write_worktree_lease(repo, "agent-dead", ttl_seconds=300)

        result = verify_worktree_lease(repo, "agent-dead")
        assert result["owned"] is False

    def test_cleanup_expired_leases_removes_expired(
        self, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        write_worktree_lease(repo, "agent-old", ttl_seconds=-1)
        write_worktree_lease(repo, "agent-new", ttl_seconds=3600)

        removed = cleanup_expired_leases(repo)
        assert removed >= 1
        assert check_worktree_lease(repo, "agent-old") is False

    def test_lease_info_lists_all(self, tmp_path: Path) -> None:
        repo = str(tmp_path)
        write_worktree_lease(repo, "agent-a", ttl_seconds=300)
        write_worktree_lease(repo, "agent-b", ttl_seconds=0)

        info = worktree_lease_info(repo)
        assert len(info) == 2
        branches = {entry["branch"] for entry in info}
        assert branches == {"agent-a", "agent-b"}

    def test_safe_branch_component_rejects_path_traversal(
        self, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        with pytest.raises(ValueError, match="escape"):
            write_worktree_lease(repo, "../evil/agent", ttl_seconds=60)

    def test_is_pid_alive_invalid(self) -> None:
        assert is_pid_alive(0) is False
        assert is_pid_alive(-1) is False

    def test_is_pid_alive_current_process(self) -> None:
        assert is_pid_alive(os.getpid()) is True


# ── _get_tree_age_seconds boundary cases ──────────────────────────────────────


class TestTreeAgeSeconds:
    def test_returns_seconds_on_valid_commit_log(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = time.time()
        epoch = int(now - 3600)
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_success(stdout=str(epoch)),
        )

        age = _get_tree_age_seconds("/tmp/gludd-worktrees/agent-aged")
        assert age is not None
        assert 3500 < age < 3700

    def test_returns_none_on_git_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_fail(rc=128),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.os.path.getmtime",
            lambda p: (_ for _ in ()).throw(OSError),
        )

        age = _get_tree_age_seconds("/nonexistent/path")
        assert age is None


# ── _branch_is_merged / _branch_on_remote ─────────────────────────────────────


class TestBranchMergeStatus:
    def test_merged_branch_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_success(rc=0),
        )
        assert _branch_is_merged("/tmp/repo", "agent-done", "development") is True

    def test_unmerged_branch_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_success(rc=1),
        )
        assert _branch_is_merged("/tmp/repo", "agent-pending", "development") is False

    def test_branch_on_remote_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_success(stdout="abc123\trefs/heads/agent-remote"),
        )
        assert _branch_on_remote("/tmp/repo", "agent-remote", "sandboxcom") is True

    def test_branch_on_remote_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_success(stdout="", rc=0),
        )
        assert _branch_on_remote("/tmp/repo", "agent-nope", "sandboxcom") is False

    def test_branch_on_remote_fail_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            lambda *a, **kw: _git_fail(rc=128),
        )
        assert _branch_on_remote("/tmp/repo", "agent-any", "sandboxcom") is True


# ── WorktreeHealthViolation repr ──────────────────────────────────────────────


class TestWorktreeHealthViolationRepr:
    def test_repr_includes_all_fields(self) -> None:
        v = WorktreeHealthViolation(
            worktree_path="/tmp/wt/agent-x",
            branch="agent-x",
            reason="stale and unmerged",
            severity="error",
        )
        r = repr(v)
        assert "/tmp/wt/agent-x" in r
        assert "agent-x" in r
        assert "stale and unmerged" in r
        assert "error" in r


# ── cross-worktree communication (no shared filesystem mutation collisions) ────


class TestCrossWorktreeCommunication:
    """Two agents in parallel worktrees must not interfere with each other's
    index, working tree, or branch state."""

    def test_each_worktree_has_independent_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt_paths = [
            "/tmp/gludd-worktrees/agent-one",
            "/tmp/gludd-worktrees/agent-two",
        ]
        git_cmds: dict[str, list[str]] = {p: [] for p in wt_paths}

        def _fake_run_git(
            *args: str, cwd: str, **kwargs: Any
        ) -> MagicMock:
            matched = next((p for p in wt_paths if cwd.startswith(p)), cwd)
            git_cmds.setdefault(matched, []).append(args[0])
            return _git_success()

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: Path("/tmp/wt-root")),
        )

        worktree_create("/tmp/fake-repo", "agent-one", worktree_root="/tmp/wt-root")
        worktree_create("/tmp/fake-repo", "agent-two", worktree_root="/tmp/wt-root")

    def test_parallel_worktree_leases_dont_conflict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = str(tmp_path)
        write_worktree_lease(repo, "agent-p1", ttl_seconds=300)
        write_worktree_lease(repo, "agent-p2", ttl_seconds=300)

        v1 = verify_worktree_lease(repo, "agent-p1")
        v2 = verify_worktree_lease(repo, "agent-p2")
        assert v1["branch"] == "agent-p1"
        assert v2["branch"] == "agent-p2"


# ── agent metadata propagation via WorktreeResult/MergeResult ─────────────────


class TestAgentMetadataPropagation:
    """Agent identity and task metadata must flow through WorktreeResult
    and MergeResult for observability."""

    def test_worktree_result_captures_branch_and_path(self) -> None:
        result = WorktreeResult(
            path="/tmp/gludd-worktrees/agent-meta",
            branch="agent-meta",
            success=True,
            message="created from base development",
        )
        assert result.branch == "agent-meta"
        assert "agent-meta" in result.path
        assert result.success is True
        assert "development" in result.message

    def test_worktree_result_failure_captures_error(self) -> None:
        result = WorktreeResult(
            path="",
            branch="agent-bad",
            success=False,
            message="worktree create timed out",
        )
        assert result.path == ""
        assert "timed out" in result.message
        assert result.success is False

    def test_merge_result_captures_strategy(self) -> None:
        result = MergeResult(
            success=True,
            strategy="no-ff",
            message="merge: agent-feat worktree work into development",
        )
        assert result.strategy == "no-ff"
        assert "development" in result.message

    def test_merge_result_conflict_captures_branch_list(self) -> None:
        result = MergeResult(
            success=False,
            strategy="no-ff",
            message="CONFLICT in daemon.py",
            conflicts=["agent-a", "agent-b"],
        )
        assert len(result.conflicts) == 2
        assert "agent-a" in result.conflicts


# ── venv sharing behavior ─────────────────────────────────────────────────────


class TestVenvSharing:
    """The cleaner delegates scoped ownership decisions to its Python owner."""

    def test_makefile_has_clean_worktree_venvs_target(self) -> None:
        makefile = (ROOT / "Makefile").read_text()
        assert "clean-worktree-venvs:" in makefile

    def test_makefile_venv_cleanup_targets_agent_prefix(self) -> None:
        makefile = (ROOT / "Makefile").read_text()
        cleaner = (ROOT / "scripts" / "clean_worktree_venvs.py").read_text()
        target = makefile.split("clean-worktree-venvs:", 1)[1].split(
            "clean-worktree-caches:", 1
        )[0]
        assert "scripts.clean_worktree_venvs" in target
        assert "rm -rf" not in target
        assert 'Path("/tmp/gludd-worktrees")' in cleaner
        assert 'Path("/Users/shawnwilson/gludd/.claude/worktrees")' in cleaner
        assert "registered_worktree_paths" in cleaner
        assert "active_process_pids" in cleaner

    def test_worktree_git_dir_shares_common_dir_with_main(self) -> None:
        from general_ludd.git_automation.worktree import _MAIN_CHECKOUT

        assert _MAIN_CHECKOUT == "/Users/shawnwilson/gludd"
        assert isinstance(_MAIN_CHECKOUT, str)


# ── cleanup-on-failure: partial worktree teardown ─────────────────────────────


class TestCleanupPartialFailure:
    """If worktree removal fails but branch deletion succeeds, cleanup must
    not raise and must report accurate state."""

    def test_partial_cleanup_reports_branch_removed_despite_failed_removal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo = str(tmp_path)
        responses = iter(
            [
                _git_fail(stderr="fatal: cannot remove locked worktree", rc=128),
                _git_success(stdout=""),  # unlock
                _git_success(stdout=""),  # prune
                _git_success(stdout=""),  # branch -d — but rc=1 = not removed
            ]
        )

        def _fake_run_git(*args: str, **kwargs: Any) -> MagicMock:
            res = next(responses)
            if args[0] == "branch":
                res.returncode = 1
                res.stdout = "error: branch 'agent-partial' not found"
            return res

        monkeypatch.setattr(
            "general_ludd.git_automation.worktree._run_git",
            _fake_run_git,
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.secure_directory",
            lambda p: Path(p),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.project_state",
            lambda **kw: MagicMock(directory=lambda name: tmp_path / "worktrees"),
        )
        monkeypatch.setattr(
            "general_ludd.git_automation.worktree.os.path.isdir",
            lambda p: True,
        )

        result = worktree_cleanup(repo, "agent-partial")
        assert result["success"] is True
        assert result["branch_removed"] is False
