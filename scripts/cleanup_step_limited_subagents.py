#!/usr/bin/env python3
"""AB036 — clean up worktree state left by step-limited subagents.

When subagents hit step limits mid-edit, they leave uncommitted changes
in their worktree. This script scans agent worktrees for dirty state and
either commits the partial work or reverts it.

State file: /tmp/gludd-subagent-worktrees.json
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKTREE_DIR = Path("/tmp/gludd-worktrees")
STATE_FILE = Path("/tmp/gludd-subagent-worktrees.json")


def get_worktrees() -> list[str]:
    if not WORKTREE_DIR.exists():
        return []
    return [str(wt) for wt in WORKTREE_DIR.iterdir() if wt.is_dir() and (wt / ".git").exists()]


def is_dirty(wt_path: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=wt_path,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_branch(wt_path: str) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            cwd=wt_path,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Clean up step-limited subagent worktrees")
    parser.add_argument("--commit", action="store_true", help="Auto-commit partial work")
    parser.add_argument("--revert", action="store_true", help="Revert dirty changes")
    parser.add_argument("--check-only", action="store_true", help="Report dirty worktrees without acting")
    args = parser.parse_args()

    worktrees = get_worktrees()
    dirty = []

    for wt in worktrees:
        if is_dirty(wt):
            branch = get_branch(wt)
            dirty.append((wt, branch))

    if not dirty:
        print("cleanup-step-limited-subagents: no dirty worktrees found")
        return 0

    print(f"cleanup-step-limited-subagents: {len(dirty)} dirty worktree(s):")
    for wt, branch in dirty:
        print(f"  {branch}: {wt}")

    if args.check_only:
        return 1

    if args.revert:
        for wt, _ in dirty:
            try:
                subprocess.run(["git", "checkout", "--", "."], cwd=wt, capture_output=True, timeout=10)
                print(f"  REVERTED: {Path(wt).name}")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        return 0

    if args.commit:
        for wt, branch in dirty:
            try:
                subprocess.run(
                    ["git", "add", "-A"],
                    capture_output=True,
                    cwd=wt,
                    timeout=10,
                )
                subprocess.run(
                    ["git", "commit", "-m", "auto: partial work from step-limited subagent"],
                    capture_output=True,
                    cwd=wt,
                    timeout=10,
                )
                print(f"  COMMITTED: {Path(wt).name} on {branch}")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
