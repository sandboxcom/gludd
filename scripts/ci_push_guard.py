#!/usr/bin/env python3
"""Block pushes when CI is already running on the target branch.

Prevents the "push cancels running CI → zero validation" anti-pattern.

Usage::

    python3 scripts/ci_push_guard.py <branch>     # check CI busy, exit 1 if active
    FORCE=1 python3 scripts/ci_push_guard.py <branch>  # bypass check (discouraged)

Exit codes:
    0 = no active CI runs, safe to push
    1 = CI is busy (active run exists)
    2 = script error / gh unavailable
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_ACTIVE_STATUSES = ("in_progress", "queued", "waiting")


def _gh_run_list(branch: str) -> list[dict]:
    runs_by_id: dict[str, dict] = {}
    for status in _ACTIVE_STATUSES:
        cmd = [
            "gh", "run", "list",
            "--workflow", "Build and Release",
            "--branch", branch,
            "--status", status,
            "--limit", "20",
            "--json", "status,conclusion,databaseId,headSha,createdAt",
            "-R", "sandboxcom/gludd",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            print(
                "CI-BUSY-CHECK: gh CLI not available - fail-open (allow push)",
                file=sys.stderr,
            )
            return []
        except (subprocess.TimeoutExpired, OSError) as exc:
            print(
                f"CI-BUSY-CHECK: error while checking {status}: {exc} "
                "- fail-open (allow push)",
                file=sys.stderr,
            )
            return []

        if result.returncode != 0:
            print(
                f"CI-BUSY-CHECK: gh error while checking {status}: "
                f"{result.stderr.strip()}",
                file=sys.stderr,
            )
            continue
        try:
            runs = json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError as exc:
            print(
                f"CI-BUSY-CHECK: bad gh JSON while checking {status}: {exc} "
                "- fail-open (allow push)",
                file=sys.stderr,
            )
            return []
        for run in runs:
            run_id = str(run.get("databaseId") or f"{status}:{len(runs_by_id)}")
            runs_by_id[run_id] = run

    return sorted(
        runs_by_id.values(),
        key=lambda run: run.get("createdAt") or "",
        reverse=True,
    )


def ci_busy_check(branch: str, force: bool = False) -> int:
    runs = _gh_run_list(branch)
    if not runs:
        print(f"CI-IDLE: no active CI runs on {branch}. Safe to push.")
        return 0

    run = runs[0]
    run_id = run.get("databaseId", "?")
    status = run.get("status", "?")

    if force:
        print(f"CI-BUSY-FORCED: run {run_id} is {status} on {branch}. "
              f"FORCE=1 bypass — pushing anyway.")
        return 0

    print(f"CI BUSY: run {run_id} is {status} on {branch}. "
          f"Wait for it to complete before pushing. "
          f"Use FORCE=1 to bypass (hotfixes only, discouraged).")
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ci_push_guard.py <branch>", file=sys.stderr)
        return 2
    branch = sys.argv[1]
    force = os.environ.get("FORCE", "") == "1"
    return ci_busy_check(branch, force)


if __name__ == "__main__":
    sys.exit(main())
