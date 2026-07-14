"""Structural tests for git_automation/types.py — result dataclasses."""

from __future__ import annotations

from general_ludd.git_automation.types import (
    CloneResult,
    GatedCommitResult,
    InitResult,
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)


class TestInitResult:
    def test_defaults(self):
        r = InitResult(path="/tmp/repo", created=True)
        assert r.path == "/tmp/repo"
        assert r.created is True
        assert r.message == ""

    def test_with_message(self):
        r = InitResult(path="/tmp/repo", created=False, message="already exists")
        assert r.message == "already exists"


class TestWorktreeResult:
    def test_success(self):
        r = WorktreeResult(path="/tmp/wt", branch="feature/x", success=True)
        assert r.path == "/tmp/wt"
        assert r.branch == "feature/x"
        assert r.success is True

    def test_failure_with_message(self):
        r = WorktreeResult(path="", branch="", success=False, message="disk full")
        assert r.success is False
        assert r.message == "disk full"


class TestWorktreeInfo:
    def test_defaults(self):
        w = WorktreeInfo(path="/tmp/wt", branch="agent-fix")
        assert w.is_main is False
        assert w.commit == ""

    def test_main_worktree(self):
        w = WorktreeInfo(path="/main", branch="master", is_main=True, commit="abc123")
        assert w.is_main is True
        assert w.commit == "abc123"


class TestMergeResult:
    def test_success_defaults(self):
        r = MergeResult(success=True)
        assert r.strategy == "ff"
        assert r.conflicts == []

    def test_failure_with_conflicts(self):
        r = MergeResult(success=False, strategy="ort", message="CONFLICT", conflicts=["a.py", "b.py"])
        assert r.success is False
        assert r.conflicts == ["a.py", "b.py"]


class TestPushResult:
    def test_success(self):
        r = PushResult(success=True, remote="origin", branch="master")
        assert r.remote == "origin"
        assert r.branch == "master"

    def test_default_message(self):
        r = PushResult(success=False)
        assert r.message == ""


class TestCloneResult:
    def test_fresh_clone(self):
        r = CloneResult(path="/tmp/r", url="https://x.git", success=True)
        assert r.already_present is False

    def test_clone_already_present(self):
        r = CloneResult(path="/tmp/r", url="https://x.git", success=True, already_present=True)
        assert r.already_present is True


class TestGatedCommitResult:
    def test_success_with_sha(self):
        r = GatedCommitResult(success=True, commit_sha="abc123")
        assert r.commit_sha == "abc123"
        assert r.gate_returncode == 0

    def test_failure_no_sha(self):
        r = GatedCommitResult(success=False, gate_returncode=1, message="gate failed")
        assert r.commit_sha is None
        assert r.gate_returncode == 1
        assert r.message == "gate failed"
