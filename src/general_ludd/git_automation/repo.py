"""Git automation module for repository management."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import UTC, datetime

from general_ludd.git_automation.types import (
    InitResult,
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)

logger = logging.getLogger(__name__)

_FORCE_PUSH_PATTERN = re.compile(
    r"\s+(-f\s+|--force\b|--force-with-lease\b)"
)


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

    def init_repo(self, path: str | None = None) -> InitResult:
        target = path or self.repo_path
        git_dir = os.path.join(target, ".git")
        created = not os.path.isdir(git_dir)
        subprocess.run(
            ["git", "init"],
            cwd=target,
            capture_output=True,
            text=True,
            check=True,
        )
        for cmd in (
            ["git", "config", "user.email", "agent@harness.local"],
            ["git", "config", "user.name", "Agentic Harness Agent"],
        ):
            subprocess.run(cmd, cwd=target, capture_output=True, text=True, check=False)
        return InitResult(path=target, created=created, message="initialized" if created else "already exists")

    def is_repo(self) -> bool:
        try:
            self._run_git("rev-parse", "--git-dir")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return False

    def create_branch(self, name: str) -> str:
        self._run_git("checkout", "-b", name)
        return name

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

    def create_worktree(self, repo_path: str, branch_name: str, worktree_path: str) -> WorktreeResult:
        try:
            subprocess.run(
                ["git", "worktree", "add", worktree_path, "-b", branch_name, "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return WorktreeResult(path=worktree_path, branch=branch_name, success=True)
        except subprocess.CalledProcessError as exc:
            return WorktreeResult(
                path=worktree_path,
                branch=branch_name,
                success=False,
                message=exc.stderr.strip() if exc.stderr else str(exc),
            )

    def remove_worktree(self, repo_path: str, worktree_path: str) -> bool:
        try:
            subprocess.run(
                ["git", "worktree", "remove", worktree_path],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def list_worktrees(self, repo_path: str) -> list[WorktreeInfo]:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        worktrees: list[WorktreeInfo] = []
        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current["path"] = line[len("worktree "):]
            elif line.startswith("branch "):
                current["branch"] = line[len("branch "):]
            elif line.startswith("HEAD "):
                current["commit"] = line[len("HEAD "):]
            elif line == "":
                if "path" in current:
                    worktrees.append(
                        WorktreeInfo(
                            path=current.get("path", ""),
                            branch=current.get("branch", ""),
                            commit=current.get("commit", ""),
                        )
                    )
                current = {}
        if "path" in current:
            worktrees.append(
                WorktreeInfo(
                    path=current.get("path", ""),
                    branch=current.get("branch", ""),
                    commit=current.get("commit", ""),
                )
            )
        return worktrees

    def merge_branch(self, repo_path: str, source: str, target: str, strategy: str = "ff") -> MergeResult:
        subprocess.run(
            ["git", "checkout", target],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        merge_args = ["git", "merge", source]
        if strategy == "ff":
            merge_args.append("--ff-only")
        elif strategy == "no-ff":
            merge_args.extend(["--no-ff", "-m", f"Merge {source} into {target}"])
        elif strategy == "squash":
            merge_args.append("--squash")
        result = subprocess.run(
            merge_args,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            conflicts = []
            if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                conflicts = [source]
            return MergeResult(success=False, strategy=strategy, message=result.stderr.strip(), conflicts=conflicts)
        if strategy == "squash":
            subprocess.run(
                ["git", "commit", "-m", f"Merge {source} into {target} (squash)"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
        return MergeResult(success=True, strategy=strategy, message=result.stdout.strip())

    def create_release_tag(self, repo_path: str, fmt: str = "YYYYMMDDHHMMSS") -> str:
        now = datetime.now(tz=UTC)
        tag = now.strftime("%Y%m%d%H%M%S")
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return tag

    def create_checkpoint_tag(self, repo_path: str, todo_id: str, sha: str) -> str:
        ts = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
        short_sha = sha[:7]
        tag = f"agent/{todo_id}/{ts}/{short_sha}"
        subprocess.run(
            ["git", "tag", tag],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return tag

    def push_to_remote(self, repo_path: str, remote: str = "origin", branch: str | None = None) -> PushResult:
        args = ["git", "push", remote]
        if branch:
            args.append(branch)
        result = subprocess.run(
            args,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        return PushResult(
            success=result.returncode == 0,
            remote=remote,
            branch=branch or "",
            message=result.stderr.strip() if result.stderr else result.stdout.strip(),
        )

    def create_local_bare_mirror(self, repo_path: str, mirror_path: str) -> str:
        subprocess.run(
            ["git", "clone", "--bare", repo_path, mirror_path],
            capture_output=True,
            text=True,
            check=True,
        )
        return mirror_path

    @staticmethod
    def is_force_push(command: str) -> bool:
        return bool(_FORCE_PUSH_PATTERN.search(command))

    @staticmethod
    def generate_branch_name(todo_id: str, slug: str) -> str:
        ts = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
        return f"agent/TODO-{todo_id}/{slug}-{ts}"
