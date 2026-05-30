"""Unit tests for git automation."""

import os
import re

import pytest

from agentic_harness.git_automation.repo import GitAutomation
from agentic_harness.git_automation.types import (
    InitResult,
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)


class TestGitAutomation:
    @pytest.fixture
    def git_repo(self, tmp_path):
        git = GitAutomation(repo_path=str(tmp_path))
        git._run_git("init")
        git._run_git("config", "user.email", "test@harness.local")
        git._run_git("config", "user.name", "Test Agent")
        dummy = tmp_path / "README.md"
        dummy.write_text("# test")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")
        return git

    def test_is_repo(self, git_repo):
        assert git_repo.is_repo() is True

    def test_is_not_repo(self, tmp_path):
        git = GitAutomation(repo_path=str(tmp_path / "nonexistent"))
        assert git.is_repo() is False

    def test_create_branch(self, git_repo):
        git_repo.create_branch("feature-test")
        result = git_repo._run_git("branch", "--show-current")
        assert result.stdout.strip() == "feature-test"

    def test_commit(self, git_repo, tmp_path):
        (tmp_path / "test.txt").write_text("hello")
        sha = git_repo.commit("test commit")
        assert len(sha) >= 7

    def test_tag_release(self, git_repo):
        tag = git_repo.tag_release("20260530120000")
        assert tag == "20260530120000"

    def test_tag_checkpoint(self, git_repo):
        tag = git_repo.tag_checkpoint("agent/TODO-001/20260530120000/abc1234")
        assert "agent/TODO-001" in tag

    def test_reject_force_push(self, git_repo):
        assert git_repo.reject_force_push() is False

    def test_get_current_commit(self, git_repo):
        sha = git_repo.get_current_commit()
        assert len(sha) >= 7


class TestInitRepo:
    def test_init_repo_creates_git_dir(self, tmp_path):
        repo_path = str(tmp_path / "new-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        result = git.init_repo(path=repo_path)
        assert isinstance(result, InitResult)
        assert result.created is True
        assert os.path.isdir(os.path.join(repo_path, ".git"))

    def test_init_repo_idempotent(self, tmp_path):
        repo_path = str(tmp_path / "existing-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        first = git.init_repo(path=repo_path)
        assert first.created is True
        second = git.init_repo(path=repo_path)
        assert isinstance(second, InitResult)
        assert second.created is False


class TestWorktrees:
    @pytest.fixture
    def repo_with_commit(self, tmp_path):
        repo_path = str(tmp_path / "main-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        dummy = tmp_path / "main-repo" / "README.md"
        dummy.write_text("# test")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")
        return git, repo_path, tmp_path

    def test_create_worktree(self, repo_with_commit):
        git, repo_path, tmp_path = repo_with_commit
        worktree_path = str(tmp_path / "wt-feature")
        result = git.create_worktree(
            repo_path=repo_path, branch_name="feature-x", worktree_path=worktree_path
        )
        assert isinstance(result, WorktreeResult)
        assert result.success is True
        assert os.path.isdir(worktree_path)

    def test_remove_worktree(self, repo_with_commit):
        git, repo_path, tmp_path = repo_with_commit
        worktree_path = str(tmp_path / "wt-remove")
        git.create_worktree(
            repo_path=repo_path, branch_name="feature-rm", worktree_path=worktree_path
        )
        assert git.remove_worktree(repo_path=repo_path, worktree_path=worktree_path) is True
        assert not os.path.isdir(worktree_path)

    def test_list_worktrees(self, repo_with_commit):
        git, repo_path, tmp_path = repo_with_commit
        worktree_path = str(tmp_path / "wt-list")
        git.create_worktree(
            repo_path=repo_path, branch_name="feature-ls", worktree_path=worktree_path
        )
        trees = git.list_worktrees(repo_path=repo_path)
        assert isinstance(trees, list)
        assert len(trees) >= 2
        assert all(isinstance(t, WorktreeInfo) for t in trees)


class TestMergeBranch:
    @pytest.fixture
    def repo_with_branch(self, tmp_path):
        repo_path = str(tmp_path / "merge-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "merge-repo" / "README.md").write_text("# base")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")
        git._run_git("checkout", "-b", "feature-merge")
        (tmp_path / "merge-repo" / "feature.txt").write_text("feature work")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "feature commit")
        git._run_git("checkout", "master")
        return git, repo_path

    def test_merge_branch_ff(self, repo_with_branch):
        git, repo_path = repo_with_branch
        result = git.merge_branch(
            repo_path=repo_path, source="feature-merge", target="master"
        )
        assert isinstance(result, MergeResult)
        assert result.success is True
        assert result.strategy == "ff"


class TestTags:
    def test_create_release_tag_format(self, tmp_path):
        repo_path = str(tmp_path / "tag-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "tag-repo" / "README.md").write_text("# tags")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")
        tag = git.create_release_tag(repo_path=repo_path)
        assert re.match(r"^\d{14}$", tag), f"Expected YYYYMMDDHHMMSS, got: {tag}"

    def test_create_checkpoint_tag_format(self, tmp_path):
        repo_path = str(tmp_path / "ckpt-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "ckpt-repo" / "README.md").write_text("# ckpt")
        git._run_git("add", "-A")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")
        sha = git.get_current_commit()
        tag = git.create_checkpoint_tag(
            repo_path=repo_path, todo_id="TODO-000123", sha=sha
        )
        assert tag.startswith("agent/TODO-000123/")
        assert sha[:7] in tag


class TestPush:
    def test_push_to_remote(self, tmp_path):
        repo_path = str(tmp_path / "push-repo")
        bare_path = str(tmp_path / "bare.git")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "push-repo" / "README.md").write_text("# push")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")
        git._run_git("init", "--bare", bare_path)
        git._run_git("remote", "add", "origin", bare_path)
        result = git.push_to_remote(repo_path=repo_path, remote="origin", branch="master")
        assert isinstance(result, PushResult)
        assert result.success is True


class TestBareMirror:
    def test_create_local_bare_mirror(self, tmp_path):
        repo_path = str(tmp_path / "mirror-repo")
        mirror_path = str(tmp_path / "mirror.git")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "mirror-repo" / "README.md").write_text("# mirror")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")
        result = git.create_local_bare_mirror(repo_path=repo_path, mirror_path=mirror_path)
        assert os.path.isdir(result)
        assert os.path.isfile(os.path.join(result, "HEAD"))


class TestForcePush:
    def test_force_push_detection(self):
        git = GitAutomation()
        assert git.is_force_push("git push --force origin main") is True
        assert git.is_force_push("git push -f origin main") is True
        assert git.is_force_push("git push --force-with-lease origin main") is True
        assert git.is_force_push("git push origin main") is False
        assert git.is_force_push("git push") is False


class TestBranchNaming:
    def test_branch_naming_convention(self):
        git = GitAutomation()
        name = git.generate_branch_name(todo_id="000001", slug="fix-auth")
        assert name.startswith("agent/TODO-000001/fix-auth-")
        ts_part = name.split("-")[-1]
        assert len(ts_part) == 14
        assert ts_part.isdigit()


class TestPlaybooks:
    @pytest.fixture
    def project_root(self):
        return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    def test_git_playbooks_exist(self, project_root):
        playbooks_dir = os.path.join(project_root, "playbooks")
        assert os.path.isfile(os.path.join(playbooks_dir, "git_repo_init.yml"))
        assert os.path.isfile(os.path.join(playbooks_dir, "git_manage_worktree.yml"))
        assert os.path.isfile(os.path.join(playbooks_dir, "git_automate_change.yml"))
