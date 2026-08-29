"""Tests for scripts/check_worktree_health.py — the worktree health gate.

Covers: stale worktree detection (>24h), unmerged branch detection,
remote tracking verification, main checkout exclusion, merge-all behavior
with conflicts, cleanup after merge, error handling for missing branches,
prunable worktree flagging, and fail-open on git unavailability.

Uses mocked subprocess.run so no real git operations occur.
"""

from __future__ import annotations

import importlib
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

# Module under test
cwh = importlib.import_module("check_worktree_health")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_run(
    return_values: dict[str, tuple[int, str, str]],
) -> Callable[..., MagicMock]:
    """Return a mock subprocess.run that maps commands to (rc, stdout, stderr).

    The key is matched against the first argument of the first cmd element.
    Example: {"git": (0, "output", "")} matches any cmd whose [0] == "git".
    """

    def _runner(cmd: list[str], **kwargs: Any) -> MagicMock:
        key = cmd[0]
        if key in return_values:
            rc, out, err = return_values[key]
            return _result(rc, out, err)
        return _result(0, "", "")

    return _runner


def _result(rc: int, stdout: str, stderr: str) -> MagicMock:
    r = MagicMock()
    r.returncode = rc
    r.stdout = stdout
    r.stderr = stderr
    return r


def _worktree_entry(
    path: str,
    branch: str,
    head: str = "abcdef1234567890abcdef1234567890abcdef12",
    detached: bool = False,
    locked: bool = False,
    prunable: str = "",
) -> dict[str, str]:
    d: dict[str, str] = {"worktree": path, "head": head}
    if branch:
        d["branch"] = f"refs/heads/{branch}"
    if detached:
        d["detached"] = "true"
    if locked:
        d["locked"] = "true"
    if prunable:
        d["prunable"] = prunable
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetWorktreesExcludesMainCheckout:
    """get_worktrees() must exclude the main checkout."""

    def test_excludes_main_checkout(self) -> None:
        porcelain = (
            f"worktree {cwh.MAIN_CHECKOUT}\nHEAD abc\nbranch refs/heads/development\n\n"
            f"worktree /tmp/gludd-worktrees/agent-foo\nHEAD def\nbranch refs/heads/agent-foo\n"
        )
        with patch("check_worktree_health.run", return_value=(0, porcelain.strip(), "")):
            wts = cwh.get_worktrees()
        paths = [w["worktree"] for w in wts]
        expected_agent_path = str(
            Path("/tmp/gludd-worktrees/agent-foo").resolve()
        )
        assert cwh.MAIN_CHECKOUT not in paths, "main checkout must be excluded"
        assert expected_agent_path in paths

    def test_no_worktrees_returns_empty(self) -> None:
        porcelain = f"worktree {cwh.MAIN_CHECKOUT}\nHEAD abc\nbranch refs/heads/development\n"
        with patch("check_worktree_health.run", return_value=(0, porcelain.strip(), "")):
            wts = cwh.get_worktrees()
        assert wts == []

    def test_git_failure_returns_empty(self) -> None:
        with patch("check_worktree_health.run", return_value=(1, "", "error")):
            wts = cwh.get_worktrees()
        assert wts == []


class TestGetTreeAge:
    """get_tree_age() returns age from commit time or falls back to mtime."""

    def test_commit_epoch_fresh(self) -> None:
        now = int(time.time())
        epoch = now - 3600  # 1h ago
        with patch("check_worktree_health.run", return_value=(0, str(epoch), "")):
            age = cwh.get_tree_age("/tmp/wt")
        assert age is not None
        assert 3500 < age < 3700, f"expected ~3600, got {age}"

    def test_commit_epoch_stale(self) -> None:
        epoch = 1000000000  # ~2001
        with patch("check_worktree_health.run", return_value=(0, str(epoch), "")):
            age = cwh.get_tree_age("/tmp/wt")
        assert age is not None
        assert age > cwh.MAX_AGE_SECONDS, "ancient commit must exceed max age"

    def test_fallback_mtime(self) -> None:
        with (
            patch("check_worktree_health.run", return_value=(1, "", "")),
            patch("os.path.getmtime", return_value=time.time() - 7200),
            patch("os.path.isdir", return_value=True),
        ):
            age = cwh.get_tree_age("/tmp/wt")
        assert age is not None
        assert 7100 < age < 7300, f"expected ~7200, got {age}"

    def test_missing_dir_returns_none(self) -> None:
        with patch("check_worktree_health.run", return_value=(1, "", "")):
            age = cwh.get_tree_age("/nonexistent/path")
        assert age is None


class TestBranchMerged:
    """is_merged() uses git merge-base --is-ancestor."""

    def test_merged_branch_returns_true(self) -> None:
        with patch(
            "check_worktree_health.run",
            return_value=(0, "", ""),
        ):
            assert cwh.is_merged("agent-merged", "development")

    def test_unmerged_branch_returns_false(self) -> None:
        with patch(
            "check_worktree_health.run",
            return_value=(1, "", "not ancestor"),
        ):
            assert not cwh.is_merged("agent-unmerged", "development")

    def test_custom_target(self) -> None:
        with patch(
            "check_worktree_health.run",
            return_value=(0, "", ""),
        ) as mock_run:
            assert cwh.is_merged("agent-x", "master")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "master" in args


class TestBranchExistsOnRemote:
    """branch_exists_on_remote() checks git ls-remote for the branch ref."""

    def test_branch_exists(self) -> None:
        with patch(
            "check_worktree_health.run",
            return_value=(0, "abc123\trefs/heads/my-branch\n", ""),
        ):
            assert cwh.branch_exists_on_remote("my-branch")

    def test_branch_missing(self) -> None:
        with patch(
            "check_worktree_health.run",
            return_value=(0, "", ""),
        ):
            assert not cwh.branch_exists_on_remote("no-such-branch")

    def test_remote_check_fails_fail_open(self) -> None:
        with patch(
            "check_worktree_health.run",
            return_value=(1, "", "network error"),
        ):
            assert cwh.branch_exists_on_remote("any-branch")


class TestGetBranchCommit:
    """get_branch_commit() resolves branch tip."""

    def test_valid_branch(self) -> None:
        sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        with patch(
            "check_worktree_health.run",
            return_value=(0, sha, ""),
        ):
            assert cwh.get_branch_commit("agent-x") == sha

    def test_nonexistent_branch_returns_none(self) -> None:
        with patch(
            "check_worktree_health.run",
            return_value=(1, "", "not found"),
        ):
            assert cwh.get_branch_commit("no-such") is None


class TestMainPass:
    """main() returns 0 when healthy."""

    def test_no_worktrees_returns_zero(self) -> None:
        with patch.object(cwh, "get_worktrees", return_value=[]):
            assert cwh.main() == 0, "no worktrees = PASS (exit 0)"

    def test_merged_worktree_under_24h_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _worktree_entry("/tmp/gludd-worktrees/agent-fresh", "agent-fresh")
        with (
            patch.object(cwh, "get_worktrees", return_value=[wt]),
            patch.object(cwh, "get_tree_age", return_value=3600),  # 1h
            patch.object(cwh, "is_merged", return_value=True),
            patch.object(cwh, "branch_exists_on_remote", return_value=True),
        ):
            exit_code = cwh.main()
        assert exit_code == 0, "fresh merged worktree must pass"


class TestMainFailViolations:
    """main() returns 1 on violations."""

    def test_stale_unmerged_violation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _worktree_entry("/tmp/gludd-worktrees/agent-stale", "agent-stale")
        stale_age = cwh.MAX_AGE_SECONDS + 3600
        with (
            patch.object(cwh, "get_worktrees", return_value=[wt]),
            patch.object(cwh, "get_tree_age", return_value=stale_age),
            patch.object(cwh, "is_merged", return_value=False),
            patch.object(cwh, "branch_exists_on_remote", return_value=True),
            patch("os.path.isdir", return_value=True),
        ):
            exit_code = cwh.main()
        assert exit_code == 1, "stale + unmerged = exit 1"

    def test_prunable_violation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _worktree_entry("/tmp/gludd-worktrees/agent-prune", "agent-prune", prunable="reason")
        with (
            patch.object(cwh, "get_worktrees", return_value=[wt]),
            patch.object(cwh, "get_tree_age", return_value=3600),
            patch.object(cwh, "is_merged", return_value=True),
            patch.object(cwh, "branch_exists_on_remote", return_value=True),
        ):
            exit_code = cwh.main()
        assert exit_code == 1, "prunable worktree = exit 1"

    def test_remote_missing_violation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _worktree_entry("/tmp/gludd-worktrees/agent-orphan", "agent-orphan")
        with (
            patch.object(cwh, "get_worktrees", return_value=[wt]),
            patch.object(cwh, "get_tree_age", return_value=3600),
            patch.object(cwh, "is_merged", return_value=True),
            patch.object(cwh, "branch_exists_on_remote", return_value=False),
        ):
            exit_code = cwh.main()
        assert exit_code == 1, "missing remote branch = exit 1"


class TestMainDetachedWorktree:
    """Detached HEAD worktrees have no branch — some checks skip."""

    def test_detached_passes_if_not_stale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _worktree_entry("/tmp/gludd-worktrees/agent-detached", "", detached=True)
        with (
            patch.object(cwh, "get_worktrees", return_value=[wt]),
            patch.object(cwh, "get_tree_age", return_value=3600),
            patch.object(cwh, "branch_exists_on_remote", return_value=True),
        ):
            exit_code = cwh.main()
        assert exit_code == 0, "detached HEAD with no branch skips merge/remote checks"


class TestExitCodes:
    """Ensure exit codes match the documented contract."""

    def test_pass_returns_0(self) -> None:
        assert True  # structural: function exists
        with patch.object(cwh, "get_worktrees", return_value=[]):
            assert cwh.main() == 0

    def test_violation_returns_1(self) -> None:
        wt = _worktree_entry("/tmp/gludd-worktrees/agent-bad", "agent-bad", prunable="yes")
        with (
            patch.object(cwh, "get_worktrees", return_value=[wt]),
            patch.object(cwh, "get_tree_age", return_value=3600),
            patch.object(cwh, "is_merged", return_value=True),
            patch.object(cwh, "branch_exists_on_remote", return_value=True),
        ):
            assert cwh.main() == 1


class TestConstantsDocumented:
    """Constants match the AGENTS.md contract."""

    def test_max_age_is_24h(self) -> None:
        assert cwh.MAX_AGE_SECONDS == 24 * 60 * 60

    def test_worktree_root_is_tmp_gludd_worktrees(self) -> None:
        assert cwh.WORKTREE_ROOT == "/tmp/gludd-worktrees"

    def test_main_checkout_is_gludd_repo(self) -> None:
        assert cwh.MAIN_CHECKOUT == "/Users/shawnwilson/gludd"

    def test_remote_is_sandboxcom(self) -> None:
        assert cwh.REMOTE_NAME == "sandboxcom"


class TestMergeAllBehavior:
    """The merge_all script (scripts/worktree_merge_all.py) is the runtime
    backing for `make worktree-merge-all`. These tests verify the script's
    units: merge resolution, cleanup, and conflict reporting.
    """

    wma: ModuleType

    @pytest.fixture(autouse=True)
    def _import_merge_all(self) -> None:
        sys.path.insert(0, str(SCRIPT_DIR))
        import worktree_merge_all as wma

        self.wma = wma

    def test_is_merged_ancestor(self) -> None:
        with patch(
            "worktree_merge_all.run",
            return_value=(0, "", ""),
        ) as mock_run:
            assert self.wma.is_merged("agent-merged")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "agent-merged" in cmd
            assert "development" in cmd

    def test_is_merged_not_ancestor(self) -> None:
        with patch(
            "worktree_merge_all.run",
            return_value=(1, "", ""),
        ):
            assert not self.wma.is_merged("agent-diverged")

    def test_merge_branch_success(self) -> None:
        with patch(
            "worktree_merge_all.run",
            return_value=(0, "merge ok", ""),
        ) as mock_run:
            assert self.wma.merge_branch("agent-good")
            cmd = mock_run.call_args[0][0]
            assert "git" in cmd
            assert "merge" in cmd
            assert "--no-ff" in cmd

    def test_merge_branch_conflict(self) -> None:
        call_count = 0

        def _side_effect(
            cmd: list[str], **kwargs: Any
        ) -> tuple[int, str, str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (1, "", "CONFLICT")
            return (0, "", "")

        with patch("worktree_merge_all.run", side_effect=_side_effect) as mock_run:
            assert not self.wma.merge_branch("agent-conflict")
            assert mock_run.call_count == 2  # merge + merge --abort

    def test_cleanup_branch_success(self) -> None:
        with patch(
            "worktree_merge_all.run",
            return_value=(0, "cleaned", ""),
        ) as mock_run:
            assert self.wma.cleanup_branch("agent-done")
            cmd = mock_run.call_args[0][0]
            assert "make" in cmd
            assert "agent-cleanup" in cmd

    def test_get_worktrees_excludes_main(self) -> None:
        porcelain = (
            f"worktree {self.wma.MAIN_CHECKOUT}\nHEAD a1b2\nbranch refs/heads/development\n\n"
            f"worktree /tmp/gludd-worktrees/agent-1\nHEAD c3d4\nbranch refs/heads/agent-1\n\n"
            f"worktree /tmp/gludd-worktrees/agent-2\nHEAD e5f6\nbranch refs/heads/agent-2\n"
        )
        with patch(
            "worktree_merge_all.run",
            return_value=(0, porcelain.strip(), ""),
        ):
            wts = self.wma.get_worktrees()
        paths = [w["worktree"] for w in wts]
        assert self.wma.MAIN_CHECKOUT not in paths, "main checkout excluded"
        assert len(wts) == 2
        assert "/tmp/gludd-worktrees/agent-1" in paths
        assert "/tmp/gludd-worktrees/agent-2" in paths

    def test_main_no_worktrees_returns_zero(self) -> None:
        with (
            patch.object(self.wma, "get_worktrees", return_value=[]),
            patch.object(self.wma, "prune_worktrees"),
        ):
            assert self.wma.main() == 0

    def test_main_with_conflicts_returns_one(self) -> None:
        wts = [
            {
                "worktree": "/tmp/gludd-worktrees/w1",
                "branch": "refs/heads/w1",
                "head": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            {
                "worktree": "/tmp/gludd-worktrees/w2",
                "branch": "refs/heads/w2",
                "head": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            },
        ]
        with (
            patch.object(self.wma, "get_worktrees", return_value=wts),
            patch.object(self.wma, "is_merged", return_value=False),
            patch.object(self.wma, "merge_branch", side_effect=[True, False]),
            patch.object(self.wma, "cleanup_branch", return_value=True),
            patch.object(self.wma, "prune_worktrees"),
        ):
            exit_code = self.wma.main()
        assert exit_code == 1, "conflict should produce exit 1"

    def test_main_all_clean_returns_zero(self) -> None:
        wts = [
            {
                "worktree": "/tmp/gludd-worktrees/w1",
                "branch": "refs/heads/w1",
                "head": "cccccccccccccccccccccccccccccccccccccccc",
            },
        ]
        with (
            patch.object(self.wma, "get_worktrees", return_value=wts),
            patch.object(self.wma, "is_merged", return_value=False),
            patch.object(self.wma, "merge_branch", return_value=True),
            patch.object(self.wma, "cleanup_branch", return_value=True),
            patch.object(self.wma, "prune_worktrees"),
        ):
            exit_code = self.wma.main()
        assert exit_code == 0

    def test_main_already_merged_skips_merge(self) -> None:
        wts = [
            {
                "worktree": "/tmp/gludd-worktrees/w1",
                "branch": "refs/heads/w1",
                "head": "dddddddddddddddddddddddddddddddddddddddd",
            },
        ]
        with (
            patch.object(self.wma, "get_worktrees", return_value=wts),
            patch.object(self.wma, "is_merged", return_value=True),
            patch.object(self.wma, "merge_branch") as mock_merge,
            patch.object(self.wma, "cleanup_branch", return_value=True),
            patch.object(self.wma, "prune_worktrees"),
        ):
            exit_code = self.wma.main()
        mock_merge.assert_not_called()
        assert exit_code == 0
