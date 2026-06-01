from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

from general_ludd.git_automation.repo import GitAutomation


@pytest.fixture()
def git_env():
    with tempfile.TemporaryDirectory() as repo_dir:
        subprocess.run(
            ["git", "init"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "agent@harness.local"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["git", "config", "user.name", "Agentic Harness Agent"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "base"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        git = GitAutomation(repo_path=repo_dir)
        yield git, repo_dir


class TestGitAutonomyE2E:
    def test_init_repo_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            git = GitAutomation(repo_path=d)
            r1 = git.init_repo()
            r2 = git.init_repo()
            assert r1.created
            assert not r2.created

    def test_branch_commit_merge_tag_push_lifecycle(self, git_env):
        git, repo_dir = git_env

        _write_dummy_file(repo_dir)
        commit1 = git.commit(message="initial commit")
        assert commit1

        branch = git.create_branch("feature/test-e2e")
        assert branch

        _write_dummy_file(repo_dir, name="feature.txt")
        commit2 = git.commit(message="feature work")
        assert commit2

        merge_result = git.merge_branch(repo_dir, "feature/test-e2e", "master", strategy="no-ff")
        assert merge_result.success

        release_tag = git.create_release_tag(repo_dir)
        assert release_tag
        assert len(release_tag) == 14

        checkpoint_tag = git.create_checkpoint_tag(
            repo_dir, todo_id="TODO-001", sha="abcd1234567"
        )
        assert "agent/TODO-001/" in checkpoint_tag

    def test_worktree_lifecycle(self, git_env):
        git, repo_dir = git_env
        _write_dummy_file(repo_dir)
        git.commit(message="base")

        wt_path = os.path.join(tempfile.gettempdir(), "gludd-e2e-wt")
        wt = git.create_worktree(repo_dir, "wt-e2e-branch", wt_path)
        assert wt.success
        assert os.path.isdir(wt.path)

        worktrees = git.list_worktrees(repo_dir)
        assert len(worktrees) >= 2

        git.remove_worktree(repo_dir, wt.path)
        assert not os.path.isdir(wt.path)

    def test_force_push_rejected(self, git_env):
        git, _repo_dir = git_env
        assert git.is_force_push("git push --force origin main")
        assert not git.is_force_push("git push origin main")

    def test_branch_naming_convention(self, git_env):
        git, _repo_dir = git_env
        name = git.generate_branch_name(todo_id="000042", slug="add-e2e-tests")
        assert "TODO-000042" in name
        assert "add-e2e-tests" in name

    def test_local_bare_mirror(self, git_env):
        git, repo_dir = git_env
        mirror_path = os.path.join(tempfile.gettempdir(), "gludd-e2e-mirror.git")
        result = git.create_local_bare_mirror(repo_dir, mirror_path)
        assert result == mirror_path
        assert os.path.isdir(mirror_path)
        import shutil
        shutil.rmtree(mirror_path, ignore_errors=True)

    def test_git_playbooks_exist(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        for pb in [
            "git_repo_init.yml",
            "git_manage_worktree.yml",
            "git_automate_change.yml",
        ]:
            assert os.path.exists(os.path.join(repo_root, "playbooks", pb))


def _write_dummy_file(repo_dir: str, name: str = "dummy.txt") -> None:
    with open(os.path.join(repo_dir, name), "w") as f:
        f.write("e2e")
