"""Git automation module for repository management."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


class GitAutomation:
    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = repo_path

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=check,
        )

    def init_repo(self) -> str:
        result = self._run_git("init")
        return result.stdout.strip()

    def is_repo(self) -> bool:
        try:
            self._run_git("rev-parse", "--git-dir")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return False

    def create_branch(self, name: str) -> str:
        self._run_git("checkout", "-b", name)
        return name

    def create_worktree(self, path: str, branch: str) -> str:
        self._run_git("worktree", "add", path, branch)
        return path

    def commit(self, message: str) -> str:
        self._run_git("add", "-A")
        self._run_git("commit", "-m", message)
        result = self._run_git("rev-parse", "HEAD")
        return result.stdout.strip()

    def tag_release(self, tag: str) -> str:
        self._run_git("tag", "-a", tag, "-m", f"Release {tag}")
        return tag

    def tag_checkpoint(self, tag: str) -> str:
        self._run_git("tag", tag)
        return tag

    def push(self, remote: str = "origin", branch: str = "main") -> bool:
        try:
            self._run_git("push", remote, branch)
            return True
        except subprocess.CalledProcessError:
            logger.error("Push failed")
            return False

    def reject_force_push(self) -> bool:
        return False

    def get_current_commit(self) -> str:
        result = self._run_git("rev-parse", "HEAD")
        return result.stdout.strip()
