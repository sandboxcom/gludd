"""Batch push — port of Makefile batch-push target into Python.

Counts unpushed commits, enforces a commit-count threshold, checks for
in-flight CI on the target branch, pushes via git, and verifies the remote
tip matches local HEAD after push.

The Makefile analogue spans three targets:
  batch-push (lines 1867-1880) — commit-threshold + force-override
  _push-rate-guard (lines 1766-1800) — CI-in-flight + cooldown + thrash
  verify-remote (lines 1933-1937) — post-push remote verification
"""

from __future__ import annotations

import json
import logging
import subprocess

from general_ludd.git_automation.types import BatchPushResult

logger = logging.getLogger(__name__)


def _count_unpushed(repo_path: str, remote: str, branch: str) -> int:
    """Count commits on HEAD not yet on the remote tracking branch.

    Uses ``git rev-list @{u}..HEAD``. Returns 0 on any failure.
    """
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"@{remote}/{branch}..HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return 0
        return int(result.stdout.strip() or "0")
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return 0


def _ci_in_flight(branch: str) -> bool:
    """Check if CI has an in-flight run for ``branch`` on sandboxcom.

    Queries ``gh run list`` for active (non-completed) runs. Returns True if
    any run is pending, in_progress, queued, or waiting. False otherwise
    (completed, no runs, or gh unavailable).
    """
    try:
        result = subprocess.run(
            [
                "gh", "run", "list",
                "--repo", "sandboxcom/gludd",
                "--branch", branch,
                "--json", "status,conclusion",
                "--limit", "5",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return False
        runs = json.loads(result.stdout)
        active_statuses = {"pending", "in_progress", "queued", "waiting", "requested"}
        return any(
            run.get("status") in active_statuses
            for run in runs
        )
    except (
        subprocess.CalledProcessError, json.JSONDecodeError,
        FileNotFoundError, OSError, subprocess.TimeoutExpired,
    ):
        return False


def _do_push(repo_path: str, remote: str, branch: str) -> bool:
    """Execute ``git push <remote> <branch>``. Returns True on success."""
    try:
        subprocess.run(
            ["git", "push", remote, branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        logger.error("git push %s/%s failed", remote, branch, exc_info=True)
        return False


def _verify_remote(repo_path: str, remote: str, branch: str) -> str:
    """Return the remote tip SHA if it matches local HEAD, else empty string.

    Runs ``git rev-parse HEAD`` (local) and ``git ls-remote <remote>
    refs/heads/<branch>`` (remote). Returns the SHA only when they match.
    Empty string on mismatch or any failure.
    """
    try:
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        local_sha = local.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return ""

    try:
        remote_proc = subprocess.run(
            ["git", "ls-remote", remote, f"refs/heads/{branch}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        remote_line = remote_proc.stdout.strip()
        if not remote_line:
            return ""
        remote_sha = remote_line.split()[0]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return ""

    if remote_sha == local_sha:
        return local_sha
    logger.warning(
        "remote %s/%s is %s, local HEAD is %s — mismatch",
        remote, branch, remote_sha, local_sha,
    )
    return ""


def batch_push(
    repo_path: str,
    remote: str = "sandboxcom",
    branch: str = "master",
    threshold: int = 5,
    force: bool = False,
    *,
    check_ci: bool = True,
) -> BatchPushResult:
    """Push to remote only if the unpushed commit count meets ``threshold``.

    Mirrors the ``batch-push`` and ``_push-rate-guard`` Makefile targets:

    - Counts unpushed commits via ``git rev-list @{u}..HEAD``.
    - If ``force`` is False and count < threshold, blocks with reason
      ``below_threshold``.
    - If ``check_ci`` is True and a CI run is active on the branch, blocks
      with reason ``ci_in_flight`` (force=True bypasses this check).
    - Pushes via ``git push <remote> <branch>``.
    - Verifies the remote tip matches local HEAD after push.

    Args:
        repo_path: Path to the git repository.
        remote: Remote name (default ``sandboxcom``).
        branch: Branch to push (default ``master``).
        threshold: Minimum unpushed commits before pushing (default 5).
        force: If True, bypasses the threshold and CI checks.
        check_ci: If True, checks for in-flight CI before pushing.

    Returns:
        BatchPushResult with pushed status, reason, counts, and remote SHA.
    """
    unpushed = _count_unpushed(repo_path, remote, branch)

    if force:
        pass
    elif unpushed < threshold:
        return BatchPushResult(
            pushed=False,
            unpushed_count=unpushed,
            threshold=threshold,
            reason="below_threshold",
        )

    if check_ci and not force and _ci_in_flight(branch):
        return BatchPushResult(
            pushed=False,
            unpushed_count=unpushed,
            threshold=threshold,
            reason="ci_in_flight",
        )

    push_ok = _do_push(repo_path, remote, branch)
    if not push_ok:
        return BatchPushResult(
            pushed=False,
            unpushed_count=unpushed,
            threshold=threshold,
            reason="push_failed",
        )

    remote_sha = _verify_remote(repo_path, remote, branch)
    verified = bool(remote_sha)

    reason = "force_override" if force else "threshold_met"
    if unpushed < threshold and force:
        reason = "force_override"

    return BatchPushResult(
        pushed=True,
        unpushed_count=unpushed,
        threshold=threshold,
        reason=reason,
        remote_sha=remote_sha,
        verified=verified,
    )
