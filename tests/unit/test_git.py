"""Unit tests for git automation."""

import pytest

from agentic_harness.git_automation.repo import GitAutomation


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
