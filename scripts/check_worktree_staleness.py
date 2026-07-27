#!/usr/bin/env python3
"""AB027 — flag git worktrees older than 24 hours.

Checks `git worktree list` output. Any worktree (excluding the main checkout)
with an mtime older than 24 hours is flagged. Stale worktrees consume disk
and represent abandoned features.

Exit non-zero on violation.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_MAX_AGE_S = 86400  # 24 hours
MAIN_CHECKOUT = str(Path(__file__).resolve().parent.parent)


def get_worktrees() -> list[tuple[str, str]]:
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=MAIN_CHECKOUT,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    worktrees: list[tuple[str, str]] = []
    current_path = ""
    current_head = ""

    for line in result.stdout.split("\n"):
        if line.startswith("worktree "):
            current_path = line[len("worktree ") :]
        elif line.startswith("HEAD "):
            current_head = line[len("HEAD ") :]
        elif line == "" and current_path:
            worktrees.append((current_path, current_head))
            current_path = ""
            current_head = ""

    if current_path:
        worktrees.append((current_path, current_head))

    return worktrees


def check_staleness(max_age_s: int = DEFAULT_MAX_AGE_S) -> list[str]:
    worktrees = get_worktrees()
    now = int(time.time())
    stale: list[str] = []

    for wt_path, head in worktrees:
        # Skip the main checkout
        if os.path.realpath(wt_path) == os.path.realpath(MAIN_CHECKOUT):
            continue

        try:
            wt_stat = os.stat(wt_path)
        except OSError:
            continue

        # Check for recent git activity on the branch
        wt_mtime = wt_stat.st_mtime
        # Also check the .git file mtime inside the worktree
        git_file = os.path.join(wt_path, ".git")
        try:
            git_mtime = os.stat(git_file).st_mtime
            wt_mtime = max(wt_mtime, git_mtime)
        except OSError:
            pass

        if now - wt_mtime > max_age_s:
            age_h = (now - wt_mtime) / 3600
            branch_name = Path(wt_path).name
            stale.append(f"  {branch_name} ({wt_path}, age: {age_h:.1f}h, head: {head[:10]})")

    return stale


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check for stale git worktrees")
    parser.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_S, help="Max age in seconds")
    args = parser.parse_args()

    stale = check_staleness(args.max_age)
    if stale:
        print(f"check-worktree-staleness: {len(stale)} stale worktree(s) older than {args.max_age // 3600}h:")
        for s in stale:
            print(s)
        return 1

    print("check-worktree-staleness: all worktrees within age threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
