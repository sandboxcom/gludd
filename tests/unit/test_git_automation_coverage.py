from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import (
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)


def _ok(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _fail(
    stderr: str = "error", returncode: int = 1
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout="", stderr=stderr
    )


class TestGitAutomationInit:
    @patch("general_ludd.git_automation.repo.subprocess.run")
    @patch("general_ludd.git_automation.repo.os.path.isdir", return_value=False)
    def test_init_stores_repo_path(self, mock_isdir: MagicMock, mock_run: MagicMock):
        auto = GitAutomation(repo_path="/tmp/repo")
        result = auto.init_repo()
        assert result.path == "/tmp/repo"
        assert result.created is True
        assert "initialized" in result.message

    @patch("general_ludd.git_automation.repo.subprocess.run")
    @patch("general_ludd.git_automation.repo.os.path.isdir", return_value=True)
    def test_init_existing_repo(self, mock_isdir: MagicMock, mock_run: MagicMock):
        auto = GitAutomation(repo_path="/tmp/repo")
        result = auto.init_repo()
        assert result.created is False
        assert "already exists" in result.message

    @patch("general_ludd.git_automation.repo.subprocess.run")
    @patch("general_ludd.git_automation.repo.os.path.isdir", return_value=False)
    def test_init_with_explicit_path(self, mock_isdir: MagicMock, mock_run: MagicMock):
        auto = GitAutomation(repo_path="/default")
        result = auto.init_repo(path="/explicit")
        assert result.path == "/explicit"


class TestIsRepo:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_is_repo_true(self, mock_run: MagicMock):
        assert GitAutomation(".").is_repo() is True

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "git"),
    )
    def test_is_repo_false_called_process_error(self, mock_run: MagicMock):
        assert GitAutomation(".").is_repo() is False

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=FileNotFoundError,
    )
    def test_is_repo_false_file_not_found(self, mock_run: MagicMock):
        assert GitAutomation(".").is_repo() is False

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=OSError,
    )
    def test_is_repo_false_os_error(self, mock_run: MagicMock):
        assert GitAutomation(".").is_repo() is False


class TestCreateBranch:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_create_branch(self, mock_run: MagicMock):
        result = GitAutomation(".").create_branch("feature-x")
        assert result == "feature-x"
        mock_run.assert_called_once_with(
            ["git", "checkout", "-b", "feature-x"],
            cwd=".",
            capture_output=True,
            text=True,
            check=True,
        )


class TestCommit:
    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=[_ok(), _ok(), _ok(stdout="abc123def456\n")],
    )
    def test_commit_returns_sha(self, mock_run: MagicMock):
        result = GitAutomation(".").commit("my message")
        assert result == "abc123def456"


class TestTagRelease:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_tag_release(self, mock_run: MagicMock):
        result = GitAutomation(".").tag_release("v1.0")
        assert result == "v1.0"
        mock_run.assert_called_once_with(
            ["git", "tag", "-a", "v1.0", "-m", "Release v1.0"],
            cwd=".",
            capture_output=True,
            text=True,
            check=True,
        )


class TestTagCheckpoint:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_tag_checkpoint(self, mock_run: MagicMock):
        result = GitAutomation(".").tag_checkpoint("checkpoint-1")
        assert result == "checkpoint-1"
        mock_run.assert_called_once_with(
            ["git", "tag", "checkpoint-1"],
            cwd=".",
            capture_output=True,
            text=True,
            check=True,
        )


class TestPush:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_push_success(self, mock_run: MagicMock):
        assert GitAutomation(".").push("origin", "main") is True

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "git"),
    )
    def test_push_failure(self, mock_run: MagicMock):
        assert GitAutomation(".").push("origin", "main") is False


class TestRejectForcePush:
    def test_reject_force_push_always_false(self):
        assert GitAutomation(".").reject_force_push() is False


class TestGetCurrentCommit:
    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        return_value=_ok(stdout="deadbeef1234\n"),
    )
    def test_get_current_commit(self, mock_run: MagicMock):
        assert GitAutomation(".").get_current_commit() == "deadbeef1234"


class TestCreateWorktree:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_create_worktree_success(self, mock_run: MagicMock):
        result = GitAutomation(".").create_worktree("/repo", "feat", "/wt")
        assert result == WorktreeResult(path="/wt", branch="feat", success=True)

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=subprocess.CalledProcessError(
            1, "git", stderr="already exists"
        ),
    )
    def test_create_worktree_failure(self, mock_run: MagicMock):
        result = GitAutomation(".").create_worktree("/repo", "feat", "/wt")
        assert result.success is False
        assert "already exists" in result.message

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "git", stderr=""),
    )
    def test_create_worktree_failure_no_stderr(self, mock_run: MagicMock):
        result = GitAutomation(".").create_worktree("/repo", "feat", "/wt")
        assert result.success is False
        assert result.message


class TestRemoveWorktree:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_remove_worktree_success(self, mock_run: MagicMock):
        assert GitAutomation(".").remove_worktree("/repo", "/wt") is True

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "git"),
    )
    def test_remove_worktree_failure(self, mock_run: MagicMock):
        assert GitAutomation(".").remove_worktree("/repo", "/wt") is False


class TestListWorktrees:
    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        return_value=_ok(
            stdout=(
                "worktree /repo\n"
                "HEAD abc123\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /repo-wt\n"
                "HEAD def456\n"
                "branch refs/heads/feat\n"
            )
        ),
    )
    def test_list_worktrees_multi_entry_trailing_no_blank(self, mock_run: MagicMock):
        result = GitAutomation(".").list_worktrees("/repo")
        assert len(result) == 2
        assert result[0] == WorktreeInfo(
            path="/repo", branch="refs/heads/main", commit="abc123"
        )
        assert result[1] == WorktreeInfo(
            path="/repo-wt", branch="refs/heads/feat", commit="def456"
        )

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        return_value=_ok(
            stdout=(
                "worktree /repo\n"
                "HEAD abc123\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /repo-wt\n"
                "HEAD def456\n"
                "branch refs/heads/feat\n"
                "\n"
            )
        ),
    )
    def test_list_worktrees_with_trailing_blank(self, mock_run: MagicMock):
        result = GitAutomation(".").list_worktrees("/repo")
        assert len(result) == 2

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        return_value=_ok(stdout=""),
    )
    def test_list_worktrees_empty(self, mock_run: MagicMock):
        result = GitAutomation(".").list_worktrees("/repo")
        assert result == []


class TestMergeBranch:
    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=[
            _ok(),
            _ok(stdout="Merge made by the 'ort' strategy."),
        ],
    )
    def test_merge_ff(self, mock_run: MagicMock):
        result = GitAutomation(".").merge_branch("/repo", "feat", "main", "ff")
        assert result == MergeResult(
            success=True, strategy="ff", message="Merge made by the 'ort' strategy."
        )

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=[
            _ok(),
            _ok(stdout="Merge made by the 'ort' strategy."),
        ],
    )
    def test_merge_no_ff(self, mock_run: MagicMock):
        result = GitAutomation(".").merge_branch("/repo", "feat", "main", "no-ff")
        assert result.success is True
        assert result.strategy == "no-ff"

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=[
            _ok(),
            _ok(stdout="Squash commit -- not updating HEAD"),
            _ok(),
        ],
    )
    def test_merge_squash(self, mock_run: MagicMock):
        result = GitAutomation(".").merge_branch("/repo", "feat", "main", "squash")
        assert result.success is True
        assert result.strategy == "squash"
        assert mock_run.call_count == 3

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=[
            _ok(),
            _fail(stderr="CONFLICT (content): Merge conflict in file.txt", returncode=1),
        ],
    )
    def test_merge_conflict(self, mock_run: MagicMock):
        result = GitAutomation(".").merge_branch("/repo", "feat", "main", "ff")
        assert result.success is False
        assert result.conflicts == ["feat"]
        assert "CONFLICT" in result.message

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=[
            _ok(),
            _fail(stderr="merge failed", returncode=1),
        ],
    )
    def test_merge_failure_no_conflict(self, mock_run: MagicMock):
        result = GitAutomation(".").merge_branch("/repo", "feat", "main", "ff")
        assert result.success is False
        assert result.conflicts == []


class TestCreateReleaseTag:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_create_release_tag(self, mock_run: MagicMock):
        result = GitAutomation(".").create_release_tag("/repo")
        assert len(result) == 14
        assert result.isdigit()


class TestCreateCheckpointTag:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_create_checkpoint_tag(self, mock_run: MagicMock):
        result = GitAutomation(".").create_checkpoint_tag("/repo", "42", "abcdef1234567890")
        assert result.startswith("agent/42/")
        assert "/abcdef1" in result


class TestPushToRemote:
    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        return_value=_ok(stderr="Everything up-to-date"),
    )
    def test_push_to_remote_success(self, mock_run: MagicMock):
        result = GitAutomation(".").push_to_remote("/repo", "origin", "main")
        assert result == PushResult(
            success=True, remote="origin", branch="main", message="Everything up-to-date"
        )

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        return_value=_fail(stderr="remote rejected"),
    )
    def test_push_to_remote_failure(self, mock_run: MagicMock):
        result = GitAutomation(".").push_to_remote("/repo", "origin", "main")
        assert result.success is False
        assert "remote rejected" in result.message

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        return_value=_ok(stdout="pushed"),
    )
    def test_push_to_remote_no_branch(self, mock_run: MagicMock):
        result = GitAutomation(".").push_to_remote("/repo", "origin")
        assert result.branch == ""
        assert result.message == "pushed"


class TestCreateLocalBareMirror:
    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_create_local_bare_mirror(self, mock_run: MagicMock):
        result = GitAutomation(".").create_local_bare_mirror("/repo", "/mirror")
        assert result == "/mirror"
        mock_run.assert_called_once_with(
            ["git", "clone", "--bare", "/repo", "/mirror"],
            capture_output=True,
            text=True,
            check=True,
        )


class TestIsForcePush:
    def test_force_short_flag(self):
        assert GitAutomation.is_force_push("git push -f origin main") is True

    def test_force_long_flag(self):
        assert GitAutomation.is_force_push("git push --force origin main") is True

    def test_force_with_lease(self):
        assert GitAutomation.is_force_push("git push --force-with-lease origin main") is True

    def test_normal_push(self):
        assert GitAutomation.is_force_push("git push origin main") is False

    def test_empty_string(self):
        assert GitAutomation.is_force_push("") is False

    def test_force_in_middle(self):
        assert GitAutomation.is_force_push("git push -f origin main --verbose") is True


class TestGenerateBranchName:
    def test_generate_branch_name(self):
        result = GitAutomation.generate_branch_name("99", "fix-bug")
        assert result.startswith("agent/TODO-99/fix-bug-")
        ts_part = result.split("-")[-1]
        assert len(ts_part) == 14
        assert ts_part.isdigit()
