"""Tests for GitAutomation.gated_commit / gated_merge — the gated git primitive.

The gate command actually runs and the commit/merge is fail-closed: gate exit 0
applies the change (HEAD advances); a non-zero gate leaves the tree unchanged,
including rolling back a completed fast-forward merge.
"""
from __future__ import annotations

import sys

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
