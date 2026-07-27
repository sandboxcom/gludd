#!/usr/bin/env python3
"""
worktree_merge_all.py — bulk merge all worktree branches into development,
clean up merged worktrees, and report conflicts.

Replaces the inline shell script in the Makefile target `worktree-merge-all`
that failed with `/bin/sh: \\: command not found` due to Makefile escaping issues.
"""

from __future__ import annotations

import os
import subprocess
import sys

MAIN_CHECKOUT = "/Users/shawnwilson/gludd"
WORKTREE_ROOT = "/tmp/gludd-worktrees"


def run(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=60)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 2, "", str(e)


def get_worktrees() -> list[dict[str, str]]:
    """Parse `git worktree list --porcelain`, exclude main checkout."""
    exit_code, stdout, _stderr = run(["git", "worktree", "list", "--porcelain"], cwd=MAIN_CHECKOUT)
    if exit_code != 0:
        print(f"ERROR: git worktree list failed (exit {exit_code})", file=sys.stderr)
        return []

    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in stdout.split("\n"):
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"worktree": line[len("worktree ") :]}
        elif line.startswith("HEAD ") and current:
            current["head"] = line[len("HEAD ") :]
        elif line.startswith("branch ") and current:
            current["branch"] = line[len("branch ") :]
    if current:
        entries.append(current)

    return [e for e in entries if e.get("worktree", "") != MAIN_CHECKOUT and "worktree" in e]


def is_merged(branch: str, target: str = "development") -> bool:
    exit_code, _stdout, _stderr = run(
        ["git", "merge-base", "--is-ancestor", branch, target],
        cwd=MAIN_CHECKOUT,
    )
    return exit_code == 0


def merge_branch(branch: str) -> bool:
    """Attempt to merge branch into development --no-ff. Returns True on success."""
    exit_code, stdout, stderr = run(
        ["git", "merge", "--no-ff", branch, "-m", f"merge: {branch} worktree work into development"],
        cwd=MAIN_CHECKOUT,
    )
    if exit_code == 0:
        return True
    # Abort on failure
    run(["git", "merge", "--abort"], cwd=MAIN_CHECKOUT)
    return False


def cleanup_branch(branch: str) -> bool:
    """Run `make agent-cleanup BRANCH=<name>`. Returns True on success."""
    exit_code, stdout, stderr = run(
        ["make", "agent-cleanup", f"BRANCH={branch}"],
        cwd=MAIN_CHECKOUT,
    )
    return exit_code == 0


def prune_worktrees() -> None:
    """Remove stale worktree metadata."""
    run(["git", "worktree", "prune"], cwd=MAIN_CHECKOUT)


def main() -> int:
    print("=== Bulk merging worktrees into development ===")
    worktrees = get_worktrees()

    if not worktrees:
        print("No worktrees to merge.")
        prune_worktrees()
        print("\n=== Worktree merge complete: 0 total, 0 merged, 0 conflicts ===")
        return 0

    print(f"Found {len(worktrees)} worktree(s) to process.\n")

    total = len(worktrees)
    merged_count = 0
    conflict_count = 0

    for i, wt in enumerate(worktrees, 1):
        path = wt.get("worktree", "?")
        branch = wt.get("branch", "").removeprefix("refs/heads/")
        head = wt.get("head", "?")[:8]

        if not branch:
            print(f"--- [{i}/{total}] {path} (no branch, skipping) ---")
            continue

        print(f"--- [{i}/{total}] {branch} ({path}) ---")

        if is_merged(branch):
            print(f"  Already merged into development — cleaning up")
            if cleanup_branch(branch):
                merged_count += 1
                print(f"  Cleaned up")
            else:
                print(f"  WARNING: cleanup failed for {branch}")
        else:
            print(f"  Not merged — attempting --no-ff merge into development")
            if merge_branch(branch):
                print(f"  Merged {branch} into development — cleaning up")
                if cleanup_branch(branch):
                    merged_count += 1
                    print(f"  Cleaned up")
                else:
                    print(f"  WARNING: merge succeeded but cleanup failed for {branch}")
            else:
                print(f"  CONFLICT: {branch} — manual resolution required")
                conflict_count += 1

    prune_worktrees()

    print()
    print(f"=== Worktree merge complete: {total} total, {merged_count} merged/cleaned, {conflict_count} conflicts ===")

    if conflict_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
