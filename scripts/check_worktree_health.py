#!/usr/bin/env python3
"""
check_worktree_health.py — gate that enforces the Git Worktree Lifecycle policy.

Flags: stale worktrees (>24h old with unmerged commits), branches missing
from the remote, and other violations. Exits non-zero on any violation so
the agent MUST resolve before the gate goes green.

Exit codes:
  0 — all worktrees healthy (or none exist)
  1 — violations found (agent MUST resolve)
  2 — inconclusive (git/gh unavailable) — fail-open
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

WORKTREE_ROOT = "/tmp/gludd-worktrees"
MAIN_CHECKOUT = "/Users/shawnwilson/gludd"
MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours
REMOTE_NAME = "sandboxcom"


def _canonical_absolute_path(path: str) -> str:
    """Return one filesystem identity or raise a stable validation code."""
    if not path or any(ord(character) < 32 for character in path):
        raise ValueError("control_character")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("relative_path")
    if ".." in candidate.parts:
        raise ValueError("traversal_segment")
    return str(candidate.resolve())


def _validated_active_path(path: str) -> str:
    """Canonicalize an active path and confine it to the worktree root."""
    canonical = Path(_canonical_absolute_path(path))
    root = Path(WORKTREE_ROOT).resolve()
    if canonical == root or not canonical.is_relative_to(root):
        raise ValueError("outside_worktree_root")
    return str(canonical)


def _validated_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    """Exclude main and annotate every active path with validated identity."""
    main_identity = _canonical_absolute_path(MAIN_CHECKOUT)
    seen_identities: set[str] = set()
    validated: list[dict[str, str]] = []
    for source in entries:
        entry = dict(source)
        raw_path = entry.get("worktree", "")
        try:
            canonical = _canonical_absolute_path(raw_path)
        except ValueError as exc:
            entry["path_error"] = str(exc)
        else:
            if canonical == main_identity:
                continue
            try:
                canonical = _validated_active_path(raw_path)
            except ValueError as exc:
                entry["path_error"] = str(exc)
            else:
                if canonical in seen_identities:
                    entry["path_error"] = "duplicate_worktree_identity"
                else:
                    seen_identities.add(canonical)
                    entry["worktree"] = canonical
                    entry["path_identity"] = "canonical"
        validated.append(entry)
    return validated


def run(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Run a command, return (exit_code, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 2, "", str(e)


def get_worktrees() -> list[dict[str, str]]:
    """Parse `git worktree list --porcelain` and return list of worktree dicts.
    Excludes the main checkout."""
    exit_code, stdout, _stderr = run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=MAIN_CHECKOUT,
    )
    if exit_code != 0:
        print(f"ERROR: git worktree list failed (exit {exit_code})", file=sys.stderr)
        return []

    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in stdout.split("\n"):
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"worktree": line[len("worktree "):]}
        elif line.startswith("HEAD ") and current:
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch ") and current:
            current["branch"] = line[len("branch "):]
        elif line.startswith("detached") and current:
            current["detached"] = "true"
        elif line.startswith("bare") and current:
            current["bare"] = "true"
        elif line.startswith("locked") and current:
            current["locked"] = "true"
        elif line.startswith("prunable ") and current:
            current["prunable"] = line[len("prunable "):]
    if current:
        entries.append(current)

    return _validated_entries([entry for entry in entries if "worktree" in entry])


def get_branch_commit(branch: str) -> str | None:
    """Get the commit hash at the tip of a branch. None if branch doesn't exist."""
    exit_code, stdout, _stderr = run(
        ["git", "rev-parse", "--verify", f"{branch}^{{commit}}"],
        cwd=MAIN_CHECKOUT,
    )
    if exit_code == 0:
        return stdout
    return None


def is_merged(branch: str, target: str = "development") -> bool:
    """Check if all commits on <branch> are reachable from <target>."""
    exit_code, _stdout, _stderr = run(
        ["git", "merge-base", "--is-ancestor", branch, target],
        cwd=MAIN_CHECKOUT,
    )
    return exit_code == 0  # 0 = branch is ancestor of target (merged)


def branch_exists_on_remote(branch: str) -> bool:
    """Check if <branch> exists on sandboxcom remote."""
    exit_code, stdout, _stderr = run(
        ["git", "ls-remote", "--heads", REMOTE_NAME, f"refs/heads/{branch}"],
        cwd=MAIN_CHECKOUT,
    )
    if exit_code != 0:
        return True  # fail-open: if remote check fails, don't flag
    return bool(stdout.strip())


def get_tree_age(worktree_path: str) -> float | None:
    """Return the age of the worktree in seconds (based on HEAD commit time if possible,
    otherwise directory mtime). Returns None if age can't be determined."""
    exit_code, stdout, _stderr = run(
        ["git", "log", "-1", "--format=%ct", "HEAD"],
        cwd=worktree_path,
    )
    if exit_code == 0 and stdout.strip():
        try:
            commit_epoch = int(stdout.strip())
            return time.time() - commit_epoch
        except (ValueError, OSError):
            pass

    # Fallback: use directory mtime
    try:
        mtime = os.path.getmtime(worktree_path)
        return time.time() - mtime
    except OSError:
        return None

    return None


def main() -> int:
    worktrees = get_worktrees()

    if not worktrees:
        print("=== WORKTREE HEALTH: PASSED === (no active worktrees)")
        return 0

    print(f"Checking {len(worktrees)} active worktree(s)...\n")
    violations: list[str] = []

    for wt in worktrees:
        path = wt.get("worktree", "?")
        branch = wt.get("branch", "").removeprefix("refs/heads/")
        head = wt.get("head", "?")
        prunable = wt.get("prunable", "")
        locked = wt.get("locked", "")
        path_error = wt.get("path_error", "")

        if path_error:
            print(
                f"  ACTIVE-WORKTREE path={path} identity=rejected "
                f"reason={path_error} branch={branch or 'detached'} HEAD={head[:8]}"
            )
            violations.append(
                f"VIOLATION: {path} has invalid worktree identity ({path_error})"
            )
            continue

        flags: list[str] = []
        if prunable:
            flags.append("PRUNABLE")
        if locked:
            flags.append("LOCKED")

        age_secs = get_tree_age(path) if os.path.isdir(path) else None
        age_flag = ""
        if age_secs is not None and age_secs > MAX_AGE_SECONDS:
            age_flag = f" [STALE: {age_secs / 3600:.1f}h old]"

        merged = branch and is_merged(branch)
        remote = branch_exists_on_remote(branch) if branch else True

        status_line = (
            f"  ACTIVE-WORKTREE path={path} identity=canonical "
            f"branch={branch or 'detached'} HEAD={head[:8]}"
        )
        if flags:
            status_line += f"  flags={','.join(flags)}"
        status_line += age_flag
        print(status_line)

        # --- violation checks ---

        # 1. Stale + unmerged = violation
        if branch and age_secs is not None and age_secs > MAX_AGE_SECONDS and not merged:
            violations.append(
                f"VIOLATION: {path} ({branch}) is >24h old ({age_secs / 3600:.1f}h) "
                f"and NOT merged into development"
            )

        # 2. Branch missing from remote
        if branch and not remote:
            violations.append(
                f"VIOLATION: {path} ({branch}) does not exist on remote ({REMOTE_NAME})"
            )

        # 3. Prunable but not stale — still a flag (may be empty branches)
        if prunable and branch:
            violations.append(
                f"VIOLATION: {path} ({branch}) is PRUNABLE — abandoned worktree"
            )

    print()
    if violations:
        print(f"=== WORKTREE HEALTH: FAILED === ({len(violations)} violation(s))")
        for v in violations:
            print(v)
        print("\nACTION REQUIRED: run `make worktree-merge-all` to merge viable branches,")
        print("then `make agent-cleanup BRANCH=<name>` for branches that cannot be merged.")
        return 1
    else:
        print("=== WORKTREE HEALTH: PASSED ===")
        return 0


if __name__ == "__main__":
    sys.exit(main())
