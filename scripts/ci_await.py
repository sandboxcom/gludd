#!/usr/bin/env python3
"""Poll CI for a branch until it reaches a terminal state.

Unlike the inline ``ci-wait`` Makefile target (which only exits on GREEN and
hardcodes BRANCH=master), this script:

- Accepts a BRANCH parameter (default: master)
- Polls ``gh run list --branch <b> --json conclusion,status,databaseId,headSha,createdAt``
- Picks the LATEST run by createdAt when multiple runs exist
- Exits 0 on terminal SUCCESS, exits 1 on terminal FAILURE/cancelled/skipped/etc
- Has a configurable TIMEOUT (default 3600s = 60 min) to prevent infinite loops
- Emits heartbeat timestamps every 60s per the "No Unseen Events" invariant
- Exits 2 if still PENDING at timeout

Usage::

    make ci-await BRANCH=development TIMEOUT=120
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any


DEFAULT_TIMEOUT = int(os.environ.get("CI_AWAIT_TIMEOUT", "3600"))
POLL_INTERVAL = 60

TERMINAL_SUCCESS = {"success"}
TERMINAL_FAILURE = {"failure", "cancelled", "skipped", "stale", "timed_out", "action_required", "neutral", "startup_failure"}
NON_TERMINAL = {"queued", "in_progress", "pending", "waiting", "requested"}


def get_latest_run(branch: str) -> dict[str, Any] | None:
    try:
        result = subprocess.check_output(
            [
                "gh", "run", "list",
                "-R", "sandboxcom/gludd",
                "--branch", branch,
                "-L", "5",
                "--json", "conclusion,status,databaseId,headSha,createdAt",
            ],
            text=True,
            stderr=subprocess.PIPE,
        )
        runs: list[dict[str, Any]] = json.loads(result)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None

    if not runs:
        return None

    runs.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
    return runs[0]


def ci_await(branch: str, timeout: int) -> int:
    started = time.time()
    print(f"=== CI-AWAIT: polling branch={branch} every {POLL_INTERVAL}s (timeout={timeout}s) ===")

    last_heartbeat = started

    while True:
        elapsed = int(time.time() - started)

        run = get_latest_run(branch)
        if run is None:
            print(f"[{elapsed}s] CI-AWAIT: no runs found for branch={branch}")
        else:
            run_id = run.get("databaseId", "?")
            head_sha = (run.get("headSha", "") or "")[:12]
            status = run.get("status", "unknown")
            conclusion = run.get("conclusion", "")

            if conclusion in TERMINAL_SUCCESS:
                print(f"[{elapsed}s] CI-AWAIT: TERMINAL SUCCESS run={run_id} sha={head_sha} conclusion={conclusion}")
                return 0

            if conclusion in TERMINAL_FAILURE:
                print(f"[{elapsed}s] CI-AWAIT: TERMINAL FAILURE run={run_id} sha={head_sha} conclusion={conclusion}")
                return 1

            if status in NON_TERMINAL:
                print(f"[{elapsed}s] CI-AWAIT: status={status} run={run_id} sha={head_sha} conclusion={conclusion or 'none'}")
            else:
                print(f"[{elapsed}s] CI-AWAIT: status={status} conclusion={conclusion or 'none'} run={run_id} sha={head_sha}")

        now = time.time()
        if now - last_heartbeat >= POLL_INTERVAL:
            last_heartbeat = now

        if elapsed >= timeout:
            print(f"=== CI-AWAIT: TIMEOUT after {elapsed}s (still pending) ===")
            return 2

        time.sleep(POLL_INTERVAL)


def main() -> int:
    branch = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BRANCH", "master")
    try:
        timeout = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TIMEOUT
    except ValueError:
        timeout = DEFAULT_TIMEOUT

    return ci_await(branch, timeout)


if __name__ == "__main__":
    sys.exit(main())
