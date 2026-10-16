#!/usr/bin/env python3
"""Clean up fully-merged gludd worktrees while protecting active workstreams.

For every worktree under /tmp/gludd-worktrees/ except the protected branch
``agent-uv-boundary-fix``:

* skip if the branch is not an ancestor of ``development``
* skip if the worktree has uncommitted changes
* otherwise remove the worktree, delete the branch, and unregister the workstream

Exit codes:
  0 — completed (some worktrees may have been skipped)
  1 — unexpected failure
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from check_worktree_health import get_worktrees

MAIN_CHECKOUT = Path("/Users/shawnwilson/gludd")
PROTECTED_BRANCH = "agent-uv-boundary-fix"
TARGET_REF = "development"


def run(cmd: list[str], cwd: Path | str | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        check=check,
        timeout=60,
    )


def is_ancestor(branch: str, target: str = TARGET_REF) -> bool:
    return run(["git", "merge-base", "--is-ancestor", branch, target], cwd=MAIN_CHECKOUT).returncode == 0


def has_uncommitted_changes(worktree_path: Path) -> bool:
    result = run(["git", "status", "--porcelain"], cwd=worktree_path, check=True)
    return bool(result.stdout.strip())


def unregister_workstream(branch: str) -> None:
    run(["make", "workstream-unregister", f"BRANCH={branch}"], cwd=MAIN_CHECKOUT)


def cleanup_worktree(path: Path, branch: str, head: str) -> tuple[str, str]:
    if not branch:
        return "skip", "detached HEAD (no branch)"

    if branch == PROTECTED_BRANCH:
        return "protect", "protected branch"

    if not is_ancestor(branch):
        return "skip", f"not merged into {TARGET_REF}"

    if not path.is_dir():
        return "skip", "worktree directory missing"

    try:
        if has_uncommitted_changes(path):
            return "skip", "uncommitted changes"
    except subprocess.CalledProcessError as exc:
        return "skip", f"could not read status: {exc.stderr.strip() or exc}"

    # Remove the worktree first, then delete the branch.
    remove_result = run(["git", "worktree", "remove", "--force", str(path)], cwd=MAIN_CHECKOUT)
    if remove_result.returncode != 0:
        return "skip", f"worktree remove failed: {remove_result.stderr.strip()}"

    delete_result = run(["git", "branch", "-d", branch], cwd=MAIN_CHECKOUT)
    if delete_result.returncode != 0:
        # Already removed the worktree; report branch deletion failure but count as removed.
        return "removed", f"worktree removed; branch delete failed: {delete_result.stderr.strip()}"

    unregister_workstream(branch)
    return "removed", f"HEAD {head}"


def main() -> int:
    entries = get_worktrees()
    if entries is None:
        print("ERROR: failed to list worktrees", file=sys.stderr)
        return 1
    main_identity = MAIN_CHECKOUT.resolve()

    removed = 0
    skipped = 0
    protected = 0
    skip_reasons: list[str] = []

    for entry in entries:
        path = Path(entry["worktree"]).resolve()
        branch = entry.get("branch", "").removeprefix("refs/heads/")
        action, reason = cleanup_worktree(path, branch, entry.get("head", "")[:8])

        if action == "removed":
            removed += 1
            print(f"  removed: {path} ({branch}) {reason}")
        elif action == "protect":
            protected += 1
            print(f"  protected: {path} ({branch}) {reason}")
        else:
            skipped += 1
            skip_reasons.append(f"  skipped: {path} ({branch or 'detached'}) {reason}")
            print(skip_reasons[-1])

    print(f"worktree-cleanup-merged done: removed={removed} skipped={skipped} protected={protected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
