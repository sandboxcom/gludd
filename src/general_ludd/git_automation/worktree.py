"""Worktree lifecycle operations ported from Makefile agent-worktree targets.

Each subagent that mutates files works in an isolated git worktree on its own
branch, so concurrent edits cannot interleave on the shared checkout. The
lifecycle is:

  worktree_create → subagent edits+commits → worktree_merge → worktree_cleanup

Read-only research tasks skip this entirely — they do not touch the working tree.

Policy (from docs/ORCHESTRATION.md):
  - One worktree per file-editing subagent, one branch per worktree
  - Merge on the main checkout, never from inside a worktree
  - Cap concurrent worktree agents at 5-6
  - Clean up after every merge; never leave worktrees lingering past the session
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from general_ludd.git_automation.locking import git_repo_lock
from general_ludd.git_automation.types import MergeResult, WorktreeInfo, WorktreeResult
from general_ludd.security.state import project_state, secure_directory

logger = logging.getLogger(__name__)

DEFAULT_WORKTREE_ROOT: str | None = None
DEFAULT_MAX_AGE_HOURS = 24
DEFAULT_REMOTE_NAME = "sandboxcom"
DEFAULT_TARGET_BRANCH = "development"
_MAIN_CHECKOUT = "/Users/shawnwilson/gludd"


def _is_main_checkout(path: str, repo_path: str) -> bool:
    """True when a porcelain ``worktree`` path is the main checkout.

    Compares against the resolved repo_path first (portable across
    machines), falling back to the historical constant so pre-existing
    porcelain fixtures and callers that pass the legacy path keep working.
    """
    return path in (_MAIN_CHECKOUT, str(Path(repo_path).resolve()))


class WorktreeHealthViolation:
    """A single worktree health violation."""

    def __init__(
        self,
        worktree_path: str,
        branch: str,
        reason: str,
        severity: str = "error",
    ) -> None:
        """Record a worktree health violation found by the health check."""
        self.worktree_path = worktree_path
        self.branch = branch
        self.reason = reason
        self.severity = severity

    def __repr__(self) -> str:
        """Human-readable rendering of this violation for logs and reports."""
        return (
            f"WorktreeHealthViolation(path={self.worktree_path!r}, "
            f"branch={self.branch!r}, reason={self.reason!r}, "
            f"severity={self.severity!r})"
        )


def _reject_leading_dash(value: str, kind: str) -> str:
    if value.startswith("-"):
        raise ValueError(
            f"refusing {kind} that begins with '-' (would be parsed as a git option, not a ref/path): {value!r}"
        )
    return value


def _run_git(
    *args: str,
    cwd: str,
    check: bool = True,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}
    with git_repo_lock(cwd):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
            env=env,
        )


def worktree_create(
    repo_path: str,
    branch: str,
    base_branch: str | None = None,
    worktree_root: str | None = DEFAULT_WORKTREE_ROOT,
) -> WorktreeResult:
    """Create an isolated git worktree for a subagent.

    If the branch already exists (re-dispatch / resume), the worktree is
    attached to the existing branch instead of being created from scratch.

    Args:
        repo_path: Path to the main git repository checkout.
        branch: Name for the worktree branch (e.g. ``agent-fix-slurm``).
        base_branch: Optional base ref to branch from (default: HEAD on main).
        worktree_root: Directory where worktrees are placed.

    Returns:
        WorktreeResult with path, branch, and success status.
    """
    try:
        _reject_leading_dash(branch, "branch name")
    except ValueError as exc:
        return WorktreeResult(path="", branch=branch, success=False, message=str(exc))

    root = (
        project_state(project_root=repo_path).directory("worktrees")
        if worktree_root is None
        else secure_directory(worktree_root)
    )
    branch_path = Path(branch)
    if branch_path.is_absolute() or ".." in branch_path.parts:
        return WorktreeResult(
            path="",
            branch=branch,
            success=False,
            message=f"refusing branch path that escapes worktree root: {branch!r}",
        )
    worktree_path = str(root.joinpath(*branch_path.parts))
    try:
        _reject_leading_dash(worktree_path, "worktree path")
    except ValueError as exc:
        return WorktreeResult(path="", branch=branch, success=False, message=str(exc))

    secure_directory(Path(worktree_path).parent)

    try:
        cmd = ["worktree", "add", worktree_path, "-b", branch]
        if base_branch:
            cmd.append(base_branch)
        result = _run_git(*cmd, cwd=repo_path, check=False)
        if result.returncode == 0:
            return WorktreeResult(
                path=worktree_path,
                branch=branch,
                success=True,
                message=f"created at {worktree_path}" + (f" from base {base_branch}" if base_branch else ""),
            )
        try:
            cmd2 = ["worktree", "add", worktree_path, branch]
            result2 = _run_git(*cmd2, cwd=repo_path, check=False)
            if result2.returncode == 0:
                return WorktreeResult(
                    path=worktree_path,
                    branch=branch,
                    success=True,
                    message=f"attached to existing branch {branch} at {worktree_path}",
                )
            return WorktreeResult(
                path=worktree_path,
                branch=branch,
                success=False,
                message=result2.stderr.strip() or result.stderr.strip() or "worktree create failed",
            )
        except subprocess.TimeoutExpired:
            return WorktreeResult(
                path=worktree_path,
                branch=branch,
                success=False,
                message="worktree create timed out",
            )
    except subprocess.TimeoutExpired:
        return WorktreeResult(
            path=worktree_path,
            branch=branch,
            success=False,
            message="worktree create timed out",
        )
    except subprocess.CalledProcessError as exc:
        return WorktreeResult(
            path=worktree_path,
            branch=branch,
            success=False,
            message=exc.stderr.strip() if exc.stderr else str(exc),
        )


def worktree_merge(
    repo_path: str,
    branch: str,
    target_branch: str = DEFAULT_TARGET_BRANCH,
) -> MergeResult:
    """Merge a worktree branch into a target branch with --no-ff.

    Must be run on the MAIN checkout — never from inside a worktree.

    Args:
        repo_path: Path to the main git repository checkout.
        branch: The worktree branch to merge.
        target_branch: The branch to merge into (default: development).

    Returns:
        MergeResult with success, strategy, and any conflicts.
    """
    try:
        _reject_leading_dash(branch, "merge source branch")
        _reject_leading_dash(target_branch, "merge target branch")
    except ValueError as exc:
        return MergeResult(success=False, strategy="no-ff", message=str(exc))

    try:
        prev_branch = _run_git(
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            cwd=repo_path,
        ).stdout.strip()

        _run_git("checkout", target_branch, "--", cwd=repo_path)

        merge_msg = f"merge: {branch} worktree work into {target_branch}"
        result = _run_git(
            "merge",
            "--no-ff",
            branch,
            "-m",
            merge_msg,
            cwd=repo_path,
            check=False,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            conflicts: list[str] = []
            if "CONFLICT" in (stderr or result.stdout):
                conflicts = [branch]
                _run_git("merge", "--abort", cwd=repo_path, check=False)
            return MergeResult(
                success=False,
                strategy="no-ff",
                message=stderr or result.stdout.strip() or "merge failed",
                conflicts=conflicts,
            )

        return MergeResult(success=True, strategy="no-ff", message=merge_msg)

    except subprocess.CalledProcessError as exc:
        _run_git("merge", "--abort", cwd=repo_path, check=False)
        return MergeResult(
            success=False,
            strategy="no-ff",
            message=exc.stderr.strip() if exc.stderr else str(exc),
        )
    finally:
        try:
            if prev_branch and prev_branch != target_branch:
                _run_git("checkout", prev_branch, "--", cwd=repo_path, check=False)
        except Exception:
            pass


def worktree_cleanup(
    repo_path: str,
    branch: str,
    worktree_root: str | None = DEFAULT_WORKTREE_ROOT,
) -> dict[str, Any]:
    """Remove a worktree directory and its branch after merge.

    Safe to run even if the worktree was already removed manually.

    Args:
        repo_path: Path to the main git repository checkout.
        branch: The branch name (used for both branch deletion and path computation).
        worktree_root: Directory where worktrees live.

    Returns:
        Dict with ``success``, ``branch``, ``branch_removed``, ``cleaned`` keys.
    """
    try:
        _reject_leading_dash(branch, "branch name")
    except ValueError as exc:
        return {"success": False, "branch": branch, "branch_removed": False, "cleaned": False, "error": str(exc)}

    root = (
        project_state(project_root=repo_path).directory("worktrees")
        if worktree_root is None
        else secure_directory(worktree_root)
    )
    branch_path = Path(branch)
    if branch_path.is_absolute() or ".." in branch_path.parts:
        return {
            "success": False,
            "branch": branch,
            "branch_removed": False,
            "cleaned": False,
            "error": f"refusing branch path that escapes worktree root: {branch!r}",
        }
    worktree_path = str(root.joinpath(*branch_path.parts))
    cleaned = False

    try:
        result = _run_git(
            "worktree",
            "remove",
            worktree_path,
            "--force",
            cwd=repo_path,
            check=False,
        )
        cleaned = result.returncode == 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        cleaned = False

    if not cleaned and os.path.isdir(worktree_path):
        with contextlib.suppress(Exception):
            _run_git(
                "worktree",
                "unlock",
                worktree_path,
                cwd=repo_path,
                check=False,
            )

    with contextlib.suppress(Exception):
        _run_git("worktree", "prune", cwd=repo_path, check=False)

    branch_removed = False
    try:
        result = _run_git("branch", "-d", branch, "--", cwd=repo_path, check=False)
        branch_removed = result.returncode == 0
    except Exception:
        branch_removed = False

    return {
        "success": True,
        "branch": branch,
        "branch_removed": branch_removed,
        "cleaned": cleaned,
        "worktree_path": worktree_path,
    }


def worktree_list(repo_path: str) -> list[WorktreeInfo]:
    """List all active git worktrees.

    Args:
        repo_path: Path to the main git repository checkout.

    Returns:
        List of WorktreeInfo, one per worktree (main checkout included).
    """
    result = _run_git("worktree", "list", "--porcelain", cwd=repo_path)
    worktrees: list[WorktreeInfo] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current["path"] = line[len("worktree ") :]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch ") :]
        elif line.startswith("HEAD "):
            current["commit"] = line[len("HEAD ") :]
        elif line == "":
            if "path" in current:
                is_main = _is_main_checkout(current.get("path", ""), repo_path)
                worktrees.append(
                    WorktreeInfo(
                        path=current.get("path", ""),
                        branch=current.get("branch", ""),
                        is_main=is_main,
                        commit=current.get("commit", ""),
                    )
                )
            current = {}
    if "path" in current:
        is_main = _is_main_checkout(current.get("path", ""), repo_path)
        worktrees.append(
            WorktreeInfo(
                path=current.get("path", ""),
                branch=current.get("branch", ""),
                is_main=is_main,
                commit=current.get("commit", ""),
            )
        )
    return worktrees


def _get_tree_age_seconds(worktree_path: str) -> float | None:
    try:
        result = _run_git(
            "log",
            "-1",
            "--format=%ct",
            "HEAD",
            cwd=worktree_path,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            commit_epoch = int(result.stdout.strip())
            return time.time() - commit_epoch
    except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    try:
        mtime = os.path.getmtime(worktree_path)
        return time.time() - mtime
    except OSError:
        return None
    return None


def _branch_is_merged(repo_path: str, branch: str, target: str) -> bool:
    result = _run_git(
        "merge-base",
        "--is-ancestor",
        branch,
        target,
        cwd=repo_path,
        check=False,
    )
    return result.returncode == 0


def _branch_on_remote(repo_path: str, branch: str, remote: str) -> bool:
    result = _run_git(
        "ls-remote",
        "--heads",
        remote,
        f"refs/heads/{branch}",
        cwd=repo_path,
        check=False,
    )
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def worktree_health_check(
    repo_path: str,
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
    remote_name: str = DEFAULT_REMOTE_NAME,
    target_branch: str = DEFAULT_TARGET_BRANCH,
) -> list[WorktreeHealthViolation]:
    """Scan all worktrees for policy violations.

    Checks: stale >max_age_hours + unmerged, branch missing from remote,
    prunable flags.

    Args:
        repo_path: Path to the main git repository checkout.
        max_age_hours: Max age in hours before a worktree is considered stale.
        remote_name: Name of the remote to check branch existence against.
        target_branch: Branch to check merge status against.

    Returns:
        List of WorktreeHealthViolation. Empty list means all healthy.
    """
    max_age_seconds = max_age_hours * 3600
    violations: list[WorktreeHealthViolation] = []

    worktrees = worktree_list(repo_path)
    for wt in worktrees:
        if wt.is_main:
            continue
        path = wt.path
        branch = wt.branch.removeprefix("refs/heads/")

        age_secs = _get_tree_age_seconds(path)
        merged = _branch_is_merged(repo_path, branch, target_branch) if branch else True
        remote_ok = _branch_on_remote(repo_path, branch, remote_name) if branch else True

        if branch and age_secs is not None and age_secs > max_age_seconds and not merged:
            violations.append(
                WorktreeHealthViolation(
                    worktree_path=path,
                    branch=branch,
                    reason=(f"Stale >{max_age_hours}h ({age_secs / 3600:.1f}h) and NOT merged into {target_branch}"),
                    severity="error",
                )
            )

        if branch and not remote_ok:
            violations.append(
                WorktreeHealthViolation(
                    worktree_path=path,
                    branch=branch,
                    reason=f"Branch does not exist on remote ({remote_name})",
                    severity="warning",
                )
            )

        if branch and age_secs is not None and age_secs > max_age_seconds and merged:
            violations.append(
                WorktreeHealthViolation(
                    worktree_path=path,
                    branch=branch,
                    reason=(f"Stale >{max_age_hours}h ({age_secs / 3600:.1f}h) and already merged — cleanup needed"),
                    severity="warning",
                )
            )

    return violations


def worktree_merge_all(
    repo_path: str,
    target_branch: str = DEFAULT_TARGET_BRANCH,
    worktree_root: str | None = DEFAULT_WORKTREE_ROOT,
) -> dict[str, Any]:
    """Bulk merge all worktree branches into target_branch and cleanup.

    Iterates all active worktrees, merges each branch into target_branch
    via --no-ff, reports conflicts, and cleans up successfully merged
    worktrees.

    Args:
        repo_path: Path to the main git repository checkout.
        target_branch: The branch to merge all worktrees into.
        worktree_root: Directory where worktrees live.

    Returns:
        Dict with ``total``, ``merged``, ``conflicts``, ``skipped``, ``errors``.
    """
    worktrees = worktree_list(repo_path)
    agent_worktrees = [w for w in worktrees if not w.is_main and w.branch]

    total = len(agent_worktrees)
    merged_count = 0
    conflict_count = 0
    skipped_count = 0
    errors: list[str] = []

    for wt in agent_worktrees:
        branch = wt.branch.removeprefix("refs/heads/")
        logger.info("Processing worktree %s (branch=%s)", wt.path, branch)

        if _branch_is_merged(repo_path, branch, target_branch):
            logger.info("  %s already merged into %s — cleaning up", branch, target_branch)
            cleanup_result = worktree_cleanup(repo_path, branch, worktree_root)
            if cleanup_result["success"]:
                skipped_count += 1
            else:
                errors.append(f"cleanup failed for {branch}: {cleanup_result.get('error', 'unknown')}")
            continue

        merge_result = worktree_merge(repo_path, branch, target_branch)
        if merge_result.success:
            logger.info("  Merged %s into %s", branch, target_branch)
            cleanup_result = worktree_cleanup(repo_path, branch, worktree_root)
            if cleanup_result["success"]:
                merged_count += 1
            else:
                errors.append(f"cleanup failed after merge for {branch}: {cleanup_result.get('error', 'unknown')}")
        elif merge_result.conflicts:
            logger.warning("  CONFLICT: %s — manual resolution required", branch)
            conflict_count += 1
        else:
            logger.error("  ERROR merging %s: %s", branch, merge_result.message)
            errors.append(f"merge failed for {branch}: {merge_result.message}")

    return {
        "total": total,
        "merged": merged_count,
        "conflicts": conflict_count,
        "skipped": skipped_count,
        "errors": errors,
    }
