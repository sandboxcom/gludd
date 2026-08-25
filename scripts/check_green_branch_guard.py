#!/usr/bin/env python3
"""Protect immutable CI-green release branches before push.

Policy: once a release branch's remote tip is CI-GREEN, no new commits may
be pushed onto it.  Future work must go on a NEW branch cut via
`make release-branch-new NAME=...`.

Exit codes:
  0  — push allowed (remote tip not green, branch new, or HEAD adds no new commits)
  1  — push BLOCKED (remote tip is CI-green AND HEAD adds commits beyond it)
  2  — inconclusive (git/gh unavailable) — fails OPEN so tooling errors never block

Override env vars (for testing — each bypasses the corresponding subprocess call):
  GLUDD_GUARD_REMOTE_SHA_OVERRIDE  — synthetic remote tip SHA; empty string = no remote branch
  GLUDD_GUARD_CI_VERDICT_OVERRIDE  — 'GREEN' | 'RED' | 'PENDING' | 'NONE'
  GLUDD_GUARD_HEAD_SHA_OVERRIDE    — synthetic local HEAD SHA
  GLUDD_GUARD_AHEAD_OVERRIDE       — int: commits HEAD is ahead of remote tip
"""
from __future__ import annotations

import os
import subprocess
import sys


def _run(cmd: list[str]) -> tuple[int, str, str]:
    """Run a subprocess; return (returncode, stdout, stderr). Never raises."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 2, "", str(exc)


def _remote_tip(branch: str, remote: str = "sandboxcom") -> str | None:
    """Return the remote branch tip, or ``None`` for a new branch."""
    override = os.environ.get("GLUDD_GUARD_REMOTE_SHA_OVERRIDE")
    if override is not None:
        return override if override else None  # empty string -> no remote branch

    ssh = "ssh -i sandboxcom_github_rsa -o StrictHostKeyChecking=accept-new"
    rc, out, _ = _run(
        ["env", f"GIT_SSH_COMMAND={ssh}", "git", "ls-remote", remote, f"refs/heads/{branch}"]
    )
    if rc != 0 or not out:
        return None
    return out.split()[0]


def _ci_verdict(sha: str) -> str:
    """Return the stable hosted-CI verdict for one commit SHA."""
    override = os.environ.get("GLUDD_GUARD_CI_VERDICT_OVERRIDE")
    if override is not None:
        return override

    # Locate require_ci_green.py relative to this script (scripts/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    checker = os.path.join(script_dir, "require_ci_green.py")
    rc, _, _ = _run(["python3", checker, sha])
    if rc == 0:
        return "GREEN"
    if rc == 2:
        return "PENDING"
    return "RED"


def _local_head() -> str:
    """Return the current local HEAD SHA."""
    override = os.environ.get("GLUDD_GUARD_HEAD_SHA_OVERRIDE")
    if override:
        return override
    _, out, _ = _run(["git", "rev-parse", "HEAD"])
    return out


def _commits_ahead(remote_sha: str, local_head: str) -> int:
    """Return commits added beyond the remote, or zero when inconclusive."""
    override = os.environ.get("GLUDD_GUARD_AHEAD_OVERRIDE")
    if override is not None:
        try:
            return int(override)
        except ValueError:
            return 0

    if not remote_sha or not local_head:
        return 0
    # Treat full-prefix match as "same commit" (short SHA vs full SHA)
    if remote_sha.startswith(local_head) or local_head.startswith(remote_sha):
        return 0
    rc, out, _ = _run(["git", "rev-list", "--count", f"{remote_sha}..{local_head}"])
    if rc != 0:
        return 0
    try:
        return int(out)
    except ValueError:
        return 0


def main(argv: list[str]) -> int:
    """Validate one branch push against the immutable-green policy."""
    branch: str | None = None
    i = 1
    while i < len(argv):
        if argv[i] == "--branch" and i + 1 < len(argv):
            branch = argv[i + 1]
            i += 2
        else:
            i += 1

    if not branch:
        print("ERROR: --branch <name> required", file=sys.stderr)
        return 2

    remote_sha = _remote_tip(branch)
    if not remote_sha:
        print(f"[push-guard] branch '{branch}' has no remote tip — new branch, push allowed.")
        return 0

    verdict = _ci_verdict(remote_sha)
    if verdict != "GREEN":
        short = remote_sha[:9]
        print(f"[push-guard] remote tip {short} of '{branch}' is {verdict} — push allowed.")
        return 0

    local_head = _local_head()
    ahead = _commits_ahead(remote_sha, local_head)
    if ahead == 0:
        short = remote_sha[:9]
        print(
            f"[push-guard] remote tip {short} of '{branch}' is GREEN "
            f"but HEAD adds no new commits — push allowed (no-op or same SHA)."
        )
        return 0

    # BLOCKED
    short_remote = remote_sha[:9]
    short_local = local_head[:9] if local_head else "unknown"
    print(
        f"\nERROR: RELEASE BRANCH PROTECTED — cannot push new commits onto a CI-green branch.\n"
        f"\n"
        f"  Branch    : {branch}\n"
        f"  Remote tip: {short_remote}  (CI-GREEN — immutable)\n"
        f"  Local HEAD: {short_local}  (+{ahead} commit(s) beyond the green remote tip)\n"
        f"\n"
        f"Policy: a CI-green release branch is frozen.  New work goes on a NEW branch:\n"
        f"\n"
        f"  make release-branch-new NAME=release/<new-version>   # cut from the green base\n"
        f"  make release-promote TAG=<tag>                        # tag + ff-only merge to master\n"
        f"\n"
        f"To fix-forward on THIS branch (e.g. a CI regression that went red AFTER the green\n"
        f"run), push only when the remote tip is no longer CI-GREEN (the branch is not frozen).\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
