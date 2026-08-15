"""Unit tests for git_automation/worktree.py — worktree lifecycle operations."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.git_automation.types import MergeResult, WorktreeInfo, WorktreeResult
from general_ludd.git_automation.worktree import (
    WorktreeHealthViolation,
    worktree_cleanup,
    worktree_create,
    worktree_health_check,
    worktree_list,
    worktree_merge,
    worktree_merge_all,
)

# The real repo root on THIS machine — the functions under test validate the
# repo path exists, so a hardcoded developer path breaks on CI runners.
_REPO = str(Path(__file__).resolve().parents[2])


def _git_success(stdout: str = "", rc: int = 0, stderr: str = "") -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = rc
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _git_fail(stderr: str = "CONFLICT (content): Merge conflict\nAutomatic merge failed", rc: int = 1) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = rc
    proc.stdout = ""
    proc.stderr = stderr
    return proc


# ── mock worktree porcelain output ──────────────────────────────────────────

_WT_PORCELAIN_MAIN_ONLY = f"""worktree {_REPO}
HEAD abc123def4567890123456789012345678abcdef
branch refs/heads/development
"""

_WT_PORCELAIN_WITH_AGENT = f"""worktree {_REPO}
HEAD abc123def4567890123456789012345678abcdef
branch refs/heads/development

worktree /tmp/gludd-worktrees/agent-fix
HEAD def4567890123456789012345678abcdef012345
branch refs/heads/agent-fix
"""

_WT_PORCELAIN_TWO_AGENTS = f"""worktree {_REPO}
HEAD abc123def4567890123456789012345678abcdef
branch refs/heads/development

worktree /tmp/gludd-worktrees/agent-1
HEAD 1114567890123456789012345678abcdef012345
branch refs/heads/agent-1

worktree /tmp/gludd-worktrees/agent-2
HEAD 2224567890123456789012345678abcdef012345
branch refs/heads/agent-2
"""


class TestWorktreeCreate:
    """worktree_create(repo_path, branch, base_branch=None, worktree_root=...) → WorktreeResult"""

    def test_creates_worktree_with_branch(self):
        result = WorktreeResult(path="/tmp/gludd-worktrees/agent-x", branch="agent-x", success=True)
        assert result.success is True
        assert "agent-x" in result.path
        assert result.branch == "agent-x"

    def test_reuses_existing_branch_worktree(self):
        result = WorktreeResult(
            path="/tmp/gludd-worktrees/agent-x",
            branch="agent-x",
            success=True,
            message="attached to existing branch",
        )
        assert result.success is True
        assert "attached" in result.message.lower()

    def test_rejects_invalid_branch_names(self):
        result = WorktreeResult(
            path="", branch="-bad", success=False, message="refusing branch name that begins with '-'"
        )
        assert result.success is False
        assert "-" in result.message

    def test_returns_worktree_path(self):
        result = WorktreeResult(path="/tmp/gludd-worktrees/agent-y", branch="agent-y", success=True)
        assert result.path == "/tmp/gludd-worktrees/agent-y"

    def test_creates_with_explicit_base_branch(self):
        result = WorktreeResult(
            path="/tmp/gludd-worktrees/agent-z",
            branch="agent-z",
            success=True,
            message="created from base development",
        )
        assert result.success is True
        assert "development" in result.message.lower()

    def test_rejects_leading_dash_in_path(self):
        result = WorktreeResult(
            path="", branch="agent-ok", success=False, message="refusing worktree path that begins with '-'"
        )
        assert result.success is False

    def test_rejects_leading_dash_branch_via_fn(self):
        result = worktree_create("/tmp/gludd-worktrees", "-evil", base_branch=None)
        assert result.success is False
        assert "-" in result.message

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_creates_worktree_via_git(self, mock_run: MagicMock):
        mock_run.side_effect = [_git_success()]
        result = worktree_create(_REPO, "agent-real-test")
        assert result.success is True
        assert result.branch == "agent-real-test"
        assert "agent-real-test" in result.path

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_fallback_to_existing_branch(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_fail(rc=128, stderr="fatal: already exists"),
            _git_success(),
        ]
        result = worktree_create(_REPO, "agent-existing")
        assert result.success is True


class TestWorktreeMerge:
    """worktree_merge(repo_path, branch, target_branch="development") → MergeResult"""

    def test_no_ff_merge_into_target_branch(self):
        result = MergeResult(
            success=True,
            strategy="no-ff",
            message="merge: agent-fix worktree work into development",
        )
        assert result.success is True
        assert result.strategy == "no-ff"

    def test_merges_worktree_branch(self):
        result = MergeResult(success=True, strategy="no-ff")
        assert result.success is True

    def test_reports_conflicts_clearly(self):
        result = MergeResult(
            success=False,
            strategy="no-ff",
            message="CONFLICT (content): Merge conflict in daemon.py\nAutomatic merge failed",
            conflicts=["agent-conflict"],
        )
        assert result.success is False
        assert result.conflicts == ["agent-conflict"]
        assert "CONFLICT" in result.message

    def test_errors_on_missing_branch(self):
        result = MergeResult(
            success=False,
            strategy="no-ff",
            message="merge: agent-nonexistent - not something we can merge",
        )
        assert result.success is False

    def test_rejects_leading_dash_branch(self):
        result = worktree_merge("/tmp/test", "-bad")
        assert result.success is False
        assert "-" in result.message


class TestWorktreeCleanup:
    """worktree_cleanup(repo_path, branch, worktree_root="...") → dict"""

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_removes_worktree_directory(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_success(),  # worktree remove
            _git_success(),  # worktree prune
            _git_success(),  # branch -d
        ]
        result = worktree_cleanup(_REPO, "agent-test")
        assert result["success"] is True
        assert result["branch"] == "agent-test"
        assert result["cleaned"] is True

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_deletes_branch_after_merge(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_success(),  # worktree remove
            _git_success(),  # worktree prune
            _git_success(),  # branch -d
        ]
        result = worktree_cleanup(_REPO, "agent-merged")
        assert result["branch_removed"] is True

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_handles_already_removed_worktree(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_fail(rc=128, stderr="not a working tree"),
            _git_success(),  # prune succeeds
            _git_success(),  # branch -d succeeds
        ]
        result = worktree_cleanup(_REPO, "agent-gone")
        assert result["success"] is True

    def test_rejects_leading_dash(self):
        result = worktree_cleanup("/tmp/test", "-bad")
        assert result["success"] is False
        assert "-" in result.get("error", "")


class TestWorktreeList:
    """worktree_list(repo_path) → list[WorktreeInfo]"""

    def test_lists_all_worktrees(self):
        info = [
            WorktreeInfo(path="/main", branch="development", is_main=True, commit="abc123"),
            WorktreeInfo(path="/tmp/gludd-worktrees/agent-1", branch="agent-1", commit="def456"),
        ]
        assert len(info) == 2

    def test_excludes_main_checkout_from_count(self):
        info = [
            WorktreeInfo(path="/main", branch="development", is_main=True, commit="abc123"),
            WorktreeInfo(path="/tmp/gludd-worktrees/agent-1", branch="agent-1", commit="def456"),
        ]
        non_main = [w for w in info if not w.is_main]
        assert len(non_main) == 1

    def test_empty_list_when_no_agent_worktrees(self):
        info: list[WorktreeInfo] = [
            WorktreeInfo(path=_REPO, branch="development", is_main=True, commit="abc123"),
        ]
        non_main = [w for w in info if not w.is_main]
        assert len(non_main) == 0

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_parses_porcelain_output_main_only(self, mock_run: MagicMock):
        mock_run.return_value = _git_success(_WT_PORCELAIN_MAIN_ONLY)
        result = worktree_list(_REPO)
        assert len(result) == 1
        assert result[0].is_main is True

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_parses_porcelain_with_agent_worktree(self, mock_run: MagicMock):
        mock_run.return_value = _git_success(_WT_PORCELAIN_WITH_AGENT)
        result = worktree_list(_REPO)
        assert len(result) == 2
        agent = [w for w in result if not w.is_main]
        assert len(agent) == 1
        assert agent[0].path == "/tmp/gludd-worktrees/agent-fix"


class TestWorktreeHealth:
    """worktree_health_check(repo_path, max_age_hours=24, ...) → list[WorktreeHealthViolation]"""

    def test_violation_repr(self):
        v = WorktreeHealthViolation("/tmp/wt", "agent-x", "Stale >24h", "error")
        assert "Stale" in repr(v)
        assert "agent-x" in repr(v)

    def test_healthy_with_no_agent_worktrees(self):
        violations = worktree_health_check(repo_path=_REPO)
        assert isinstance(violations, list)

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_flags_stale_unmerged_worktree(self, mock_run: MagicMock):
        two_days_age = 48 * 3600
        mock_run.side_effect = [
            _git_success(_WT_PORCELAIN_WITH_AGENT),  # worktree list
            _git_fail(rc=1),  # merge-base --is-ancestor returns 1 (not merged)
            _git_success("abc123\trefs/heads/agent-fix"),  # ls-remote: exists
        ]
        with patch("general_ludd.git_automation.worktree._get_tree_age_seconds", return_value=two_days_age):
            violations = worktree_health_check(
                _REPO,
                max_age_hours=24,
                remote_name="sandboxcom",
            )
        assert len(violations) >= 1

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_flags_healthy_when_merged(self, mock_run: MagicMock):
        one_hour_age = 3600
        mock_run.side_effect = [
            _git_success(_WT_PORCELAIN_WITH_AGENT),  # worktree list
            _git_success(),  # merge-base --is-ancestor returns 0 (merged)
            _git_success("abc123\trefs/heads/agent-fix"),  # ls-remote: exists
        ]
        with patch("general_ludd.git_automation.worktree._get_tree_age_seconds", return_value=one_hour_age):
            violations = worktree_health_check(
                _REPO,
                max_age_hours=24,
                remote_name="sandboxcom",
            )
        assert len(violations) == 0


class TestWorktreeMergeAll:
    """worktree_merge_all(repo_path, target_branch="development", ...) → dict"""

    def test_returns_merge_summary_shape(self):
        result = {
            "total": 0,
            "merged": 0,
            "conflicts": 0,
            "skipped": 0,
            "errors": [],
        }
        assert isinstance(result, dict)
        assert "total" in result
        assert "merged" in result
        assert "conflicts" in result

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_zero_total_when_no_agent_worktrees(self, mock_run: MagicMock):
        mock_run.return_value = _git_success(_WT_PORCELAIN_MAIN_ONLY)
        result = worktree_merge_all(repo_path=_REPO)
        assert result["total"] == 0

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_merges_and_cleans_worktrees(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_success(_WT_PORCELAIN_WITH_AGENT),  # worktree list
            _git_fail(rc=1),  # merge-base --is-ancestor (not merged)
            _git_success("HEAD"),  # rev-parse --abbrev-ref HEAD
            _git_success(),  # checkout development
            _git_success(),  # merge --no-ff (success)
            _git_success("development"),  # (previous branch)
            _git_success(),  # worktree remove
            _git_success(),  # worktree prune
            _git_success(),  # branch -d
        ]
        result = worktree_merge_all(repo_path=_REPO)
        assert result["merged"] == 1
        assert result["total"] == 1
        assert result["conflicts"] == 0

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_skips_already_merged(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_success(_WT_PORCELAIN_WITH_AGENT),  # worktree list
            _git_success(),  # merge-base --is-ancestor returns 0 (already merged)
            _git_success(),  # worktree remove
            _git_success(),  # worktree prune
            _git_success(),  # branch -d
        ]
        result = worktree_merge_all(repo_path=_REPO)
        assert result["skipped"] == 1
        assert result["merged"] == 0
        assert result["total"] == 1

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_handles_merge_conflict(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_success(_WT_PORCELAIN_WITH_AGENT),  # worktree list
            _git_fail(rc=1),  # merge-base --is-ancestor (not merged)
            _git_success("HEAD"),  # rev-parse
            _git_success(),  # checkout development
            _git_fail(rc=1, stderr="CONFLICT (content): Merge conflict in daemon.py"),  # merge fails with conflict
            _git_success(),  # merge --abort
            _git_success("development"),  # checkout -
        ]
        result = worktree_merge_all(repo_path=_REPO)
        assert result["conflicts"] == 1
        assert result["total"] == 1
