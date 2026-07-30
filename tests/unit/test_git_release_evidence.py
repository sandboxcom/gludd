"""Unit tests for general_ludd.git_release.evidence — RepoEvidence collection.

The evidence collector wraps general_ludd.git_automation.GitAutomation in a
read-only way: no fetches, no index writes, no ref mutations. These tests
inject a mock GitAutomation to exercise clean / dirty / detached-HEAD / not-a-
repo / multi-worktree / in-progress-operation / policies-detected shapes.

Mirrors the MagicMock-with-spec convention used by tests/unit/test_git_automation_worktree.py.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from general_ludd.git_automation.types import GitStateResult, WorktreeInfo
from general_ludd.git_release.evidence import (
    DirtyPath,
    NotARepoError,
    Operation,
    Policy,
    RepoEvidence,
    Upstream,
    WorktreeEvidence,
    collect_repo_evidence,
)


# ── mock GitAutomation builder ───────────────────────────────────────────────


def _mock_git(
    *,
    repo_path: str = "/fake/repo",
    is_repo: bool = True,
    head_sha: str = "abc123def4567890123456789012345678abcdef0",
    branch: str = "development",
    state: GitStateResult | None = None,
    worktrees: list[WorktreeInfo] | None = None,
    branches: list[str] | None = None,
) -> MagicMock:
    """Build a MagicMock standing in for GitAutomation with the given returns."""
    git = MagicMock()
    git.is_repo.return_value = is_repo
    git.get_current_commit.return_value = head_sha
    git.current_branch.return_value = branch
    git.repo_path = repo_path
    if state is None:
        state = GitStateResult(
            success=True,
            branch=branch,
            head=head_sha,
            remote="sandboxcom",
            remote_ref=f"refs/heads/{branch}",
            remote_head=head_sha,
        )
    git.workflow_state.return_value = state
    git.list_worktrees.return_value = (
        worktrees
        if worktrees is not None
        else [
            WorktreeInfo(path="/fake/repo", branch=branch, is_main=True, commit=head_sha),
        ]
    )
    git.list_branches.return_value = branches if branches is not None else [branch]
    return git


def _state(
    *,
    branch: str = "development",
    head: str = "abc123def4567890123456789012345678abcdef0",
    status_lines: list[str] | None = None,
    remote_head: str = "",
    remote: str = "sandboxcom",
) -> GitStateResult:
    return GitStateResult(
        success=True,
        branch=branch,
        head=head,
        status=status_lines or [],
        remote=remote,
        remote_ref=f"refs/heads/{branch}",
        remote_head=remote_head,
    )


# ── RepoEvidence dataclass shape ─────────────────────────────────────────────


class TestRepoEvidenceShape:
    def test_schema_version_is_one_dot_zero(self):
        ev = RepoEvidence(
            schema_version="1.0",
            repo_root="/abs/path",
            head_sha="abc",
            branch="main",
        )
        assert ev.schema_version == "1.0"

    def test_branch_can_be_null(self):
        ev = RepoEvidence(
            schema_version="1.0",
            repo_root="/abs/path",
            head_sha="abc",
            branch=None,
        )
        assert ev.branch is None

    def test_default_collections_are_independent(self):
        a = RepoEvidence(schema_version="1.0", repo_root="/a", head_sha="x", branch="b")
        b = RepoEvidence(schema_version="1.0", repo_root="/b", head_sha="y", branch="b")
        a.dirty_paths.append(DirtyPath(path="foo", index_state="M", worktree_state=" "))
        assert b.dirty_paths == []


# ── collect_repo_evidence — happy paths ──────────────────────────────────────


class TestCollectCleanRepo:
    def test_clean_repo_has_no_dirty_paths(self, tmp_path):
        git = _mock_git(repo_path=str(tmp_path), state=_state())
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert ev.dirty_paths == []
        assert ev.head_sha == "abc123def4567890123456789012345678abcdef0"
        assert ev.branch == "development"

    def test_repo_root_is_absolute(self, tmp_path):
        git = _mock_git(repo_path=str(tmp_path))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert os.path.isabs(ev.repo_root)

    def test_evidence_time_is_rfc3339_parseable(self, tmp_path):
        git = _mock_git(repo_path=str(tmp_path))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        # Should parse without raising.
        datetime.fromisoformat(ev.evidence_time)


class TestCollectDirtyRepo:
    def test_modified_file_is_flagged(self, tmp_path):
        git = _mock_git(
            repo_path=str(tmp_path),
            state=_state(status_lines=[" M src/app.py"]),
        )
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert len(ev.dirty_paths) == 1
        dp = ev.dirty_paths[0]
        assert dp.path == "src/app.py"
        assert dp.index_state == " "
        assert dp.worktree_state == "M"
        assert dp.untracked is False

    def test_untracked_file_is_flagged(self, tmp_path):
        git = _mock_git(
            repo_path=str(tmp_path),
            state=_state(status_lines=["?? new/untracked.py"]),
        )
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert len(ev.dirty_paths) == 1
        assert ev.dirty_paths[0].untracked is True
        assert ev.dirty_paths[0].path == "new/untracked.py"

    def test_staged_file_index_state_captured(self, tmp_path):
        git = _mock_git(
            repo_path=str(tmp_path),
            state=_state(status_lines=["M  src/staged.py"]),
        )
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert ev.dirty_paths[0].index_state == "M"
        assert ev.dirty_paths[0].worktree_state == " "

    def test_multiple_dirty_paths_all_captured(self, tmp_path):
        status = [" M a.py", "M  b.py", "?? c.py", "RM c.py -> d.py"]
        git = _mock_git(repo_path=str(tmp_path), state=_state(status_lines=status))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert len(ev.dirty_paths) == 4
        paths = {dp.path for dp in ev.dirty_paths}
        assert "a.py" in paths
        assert "b.py" in paths


# ── detached HEAD ────────────────────────────────────────────────────────────


class TestDetachedHead:
    @pytest.mark.parametrize("raw", ["unknown", "DETACHED", ""])
    def test_detached_head_branch_is_null(self, tmp_path, raw):
        git = _mock_git(repo_path=str(tmp_path), branch=raw)
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert ev.branch is None


# ── not-a-repo ───────────────────────────────────────────────────────────────


class TestNotARepo:
    def test_not_a_repo_raises(self, tmp_path):
        git = _mock_git(repo_path=str(tmp_path), is_repo=False)
        with pytest.raises(NotARepoError):
            collect_repo_evidence(str(tmp_path), git=git)


# ── worktrees ────────────────────────────────────────────────────────────────


class TestWorktrees:
    def test_worktrees_collected(self, tmp_path):
        head = "abc123def4567890123456789012345678abcdef0"
        wts = [
            WorktreeInfo(path="/fake/repo", branch="development", is_main=True, commit=head),
            WorktreeInfo(path="/tmp/wt-agent", branch="agent-fix", is_main=False, commit="def456"),
        ]
        git = _mock_git(repo_path=str(tmp_path), worktrees=wts)
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert len(ev.worktrees) == 2
        agent_wt = [w for w in ev.worktrees if not w.path.endswith("repo")][0]
        assert agent_wt.branch == "agent-fix"
        assert agent_wt.head_sha == "def456"


# ── operations in progress ───────────────────────────────────────────────────


class TestOperationsInProgress:
    def test_rebase_detected(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "rebase-merge").mkdir()
        git = _mock_git(repo_path=str(tmp_path))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert any(op.kind == "rebase" for op in ev.operations)

    def test_merge_detected(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("deadbeef\n")
        git = _mock_git(repo_path=str(tmp_path))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert any(op.kind == "merge" for op in ev.operations)

    def test_no_operations_when_clean(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        git = _mock_git(repo_path=str(tmp_path))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert ev.operations == []


# ── policies ─────────────────────────────────────────────────────────────────


class TestPolicies:
    def test_agents_md_detected(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# policies\n")
        git = _mock_git(repo_path=str(tmp_path))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert any(p.source == "AGENTS.md" for p in ev.policies)

    def test_policy_has_text_digest(self, tmp_path):
        (tmp_path / "CONTRIBUTING.md").write_text("# contribute\n")
        git = _mock_git(repo_path=str(tmp_path))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        contrib = [p for p in ev.policies if p.source == "CONTRIBUTING.md"][0]
        assert len(contrib.text_digest) == 64  # sha256 hex

    def test_no_policies_when_absent(self, tmp_path):
        git = _mock_git(repo_path=str(tmp_path))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert ev.policies == []


# ── upstreams ────────────────────────────────────────────────────────────────


class TestUpstreams:
    def test_upstream_in_sync(self, tmp_path):
        head = "abc123def4567890123456789012345678abcdef0"
        git = _mock_git(
            repo_path=str(tmp_path),
            state=_state(head=head, remote_head=head),
        )
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert len(ev.upstreams) == 1
        assert ev.upstreams[0].ahead == 0
        assert ev.upstreams[0].behind == 0

    def test_upstream_diverged_reports_unknown_counts(self, tmp_path):
        head = "abc123def4567890123456789012345678abcdef0"
        git = _mock_git(
            repo_path=str(tmp_path),
            state=_state(head=head, remote_head="ffffffffffffffffffffffffffffffffffffffff"),
        )
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert ev.upstreams[0].ahead == -1
        assert ev.upstreams[0].behind == -1

    def test_no_upstream_when_remote_head_empty(self, tmp_path):
        git = _mock_git(
            repo_path=str(tmp_path),
            state=_state(remote_head=""),
        )
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert ev.upstreams == []


# ── dependency injection ─────────────────────────────────────────────────────


class TestDependencyInjection:
    def test_collect_accepts_injected_git(self, tmp_path):
        git = _mock_git(repo_path=str(tmp_path))
        ev = collect_repo_evidence(str(tmp_path), git=git)
        assert ev.head_sha == "abc123def4567890123456789012345678abcdef0"
        git.is_repo.assert_called_once()
