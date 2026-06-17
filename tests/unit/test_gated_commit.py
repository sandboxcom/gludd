"""Tests for GitAutomation.gated_commit / gated_merge — the gated git primitive.

The gate command actually runs and the commit/merge is fail-closed: gate exit 0
applies the change (HEAD advances); a non-zero gate leaves the tree unchanged,
including rolling back a completed fast-forward merge.
"""
from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import GatedCommitResult

# Portable gate commands (sys.executable always exists; no coreutils dependency).
_PASS = [sys.executable, "-c", "raise SystemExit(0)"]
_FAIL = [sys.executable, "-c", "raise SystemExit(1)"]


def _repo(tmp_path):
    p = tmp_path / "r"
    p.mkdir()
    git = GitAutomation(repo_path=str(p))
    git.init_repo(path=str(p))
    (p / "README.md").write_text("# r\n")
    git._run_git("add", "-A")
    git._run_git("commit", "-m", "init")
    return git, p


def test_gated_commit_pass_commits(tmp_path):
    git, p = _repo(tmp_path)
    before = git.get_current_commit()
    (p / "f.txt").write_text("x")
    res = git.gated_commit(["f.txt"], "add f", _PASS)
    assert isinstance(res, GatedCommitResult)
    assert res.success is True
    assert res.commit_sha
    assert git.get_current_commit() == res.commit_sha
    assert git.get_current_commit() != before


def test_gated_commit_fail_no_commit(tmp_path):
    git, p = _repo(tmp_path)
    before = git.get_current_commit()
    (p / "f.txt").write_text("x")
    res = git.gated_commit(["f.txt"], "add f", _FAIL)
    assert res.success is False
    assert res.gate_returncode != 0
    assert git.get_current_commit() == before


def test_gated_merge_pass_merges(tmp_path):
    git, p = _repo(tmp_path)
    git.create_branch("feature")
    (p / "feat.txt").write_text("y")
    git._run_git("add", "-A")
    git._run_git("commit", "-m", "feat")
    git._run_git("checkout", "master")
    before = git.get_current_commit()
    res = git.gated_merge("feature", "master", _PASS)
    assert res.success is True
    assert git.get_current_commit() != before
    assert (p / "feat.txt").exists()


def test_gated_merge_fail_rolls_back(tmp_path):
    git, p = _repo(tmp_path)
    git.create_branch("feature")
    (p / "feat.txt").write_text("y")
    git._run_git("add", "-A")
    git._run_git("commit", "-m", "feat")
    git._run_git("checkout", "master")
    before = git.get_current_commit()
    res = git.gated_merge("feature", "master", _FAIL)
    assert res.success is False
    assert res.gate_returncode != 0
    # Fail-closed: the ff-merge that moved HEAD must be rolled back to pre_sha.
    assert git.get_current_commit() == before
    assert not (p / "feat.txt").exists()


# ---------------------------------------------------------------------------
# Focused unit tests for the shared _run_gate_cmd helper
# ---------------------------------------------------------------------------

def test_run_gate_cmd_success_returns_completed_process(tmp_path):
    """_run_gate_cmd returns CompletedProcess (not GatedCommitResult) on exit 0."""
    git, _ = _repo(tmp_path)
    result = git._run_gate_cmd(_PASS)
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 0


def test_run_gate_cmd_nonzero_returns_failure_result(tmp_path):
    """_run_gate_cmd returns GatedCommitResult with gate_returncode=1 on exit 1."""
    git, _ = _repo(tmp_path)
    result = git._run_gate_cmd(_FAIL)
    assert isinstance(result, GatedCommitResult)
    assert result.success is False
    assert result.gate_returncode == 1
    # No rollback suffix when rollback_sha is not supplied.
    assert "(rolled back)" not in result.message


def test_run_gate_cmd_nonzero_with_rollback_sha_resets_and_suffixes(tmp_path):
    """_run_gate_cmd resets HEAD and appends '(rolled back)' when rollback_sha given."""
    git, p = _repo(tmp_path)
    # Make a second commit to give us something to roll back from.
    (p / "extra.txt").write_text("extra")
    git._run_git("add", "--", "extra.txt")
    git._run_git("commit", "-m", "extra")
    after_sha = git.get_current_commit()
    # Capture the pre-extra SHA as the rollback target.
    pre_sha = git._run_git("rev-parse", "HEAD~1").stdout.strip()
    result = git._run_gate_cmd(_FAIL, rollback_sha=pre_sha)
    assert isinstance(result, GatedCommitResult)
    assert result.success is False
    assert result.gate_returncode == 1
    assert "(rolled back)" in result.message
    # HEAD must be reset to pre_sha, not after_sha.
    assert git.get_current_commit() == pre_sha
    assert git.get_current_commit() != after_sha


def test_run_gate_cmd_timeout_returns_124_no_rollback(tmp_path):
    """_run_gate_cmd returns gate_returncode=124 on timeout (no rollback_sha)."""
    git, _ = _repo(tmp_path)
    with patch("general_ludd.git_automation.repo.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd=["gate"], timeout=1)):
        result = git._run_gate_cmd(["gate"])
    assert isinstance(result, GatedCommitResult)
    assert result.success is False
    assert result.gate_returncode == 124
    assert "timed out" in result.message
    assert "(rolled back)" not in result.message


def test_run_gate_cmd_timeout_with_rollback_sha_resets_and_suffixes(tmp_path):
    """_run_gate_cmd resets HEAD and appends '(rolled back)' on timeout when rollback_sha given."""
    git, p = _repo(tmp_path)
    (p / "extra.txt").write_text("extra")
    git._run_git("add", "--", "extra.txt")
    git._run_git("commit", "-m", "extra")
    after_sha = git.get_current_commit()
    pre_sha = git._run_git("rev-parse", "HEAD~1").stdout.strip()
    _real_run = subprocess.run

    def _timeout_only_for_gate(args, **kwargs):
        if args and args[0] == "gate":
            raise subprocess.TimeoutExpired(cmd=args, timeout=1)
        return _real_run(args, **kwargs)

    with patch("general_ludd.git_automation.repo.subprocess.run", side_effect=_timeout_only_for_gate):
        result = git._run_gate_cmd(["gate"], rollback_sha=pre_sha)
    assert isinstance(result, GatedCommitResult)
    assert result.success is False
    assert result.gate_returncode == 124
    assert "timed out" in result.message
    assert "(rolled back)" in result.message
    assert git.get_current_commit() == pre_sha
    assert git.get_current_commit() != after_sha
