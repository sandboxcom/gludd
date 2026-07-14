#!/usr/bin/env python3
"""Poll CI for a branch until terminal state (green or red) with heartbeat.

Replaces the anti-pattern of ``sleep 60 && make ci-verdict`` loops in shell.
The script is the subprocess — it polls, it sleeps internally with timestamped
heartbeats, and it returns a clean exit code the orchestrator can act on.

Usage::

    make ci-await BRANCH=master
    make ci-await BRANCH=development TIMEOUT=900

Exit codes: 0 = GREEN, 1 = RED, 2 = TIMEOUT (no terminal before timeout).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.environ.get(
    "GLUDD_PROJECT_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

POLL_INTERVAL = int(os.environ.get("CI_AWAIT_INTERVAL", "60"))
DEFAULT_TIMEOUT = int(os.environ.get("CI_AWAIT_TIMEOUT", "1800"))


def _last_commit_on_branch(branch: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"origin/{branch}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        result = subprocess.run(
            ["git", "rev-parse", branch],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _fetch_ci_verdict(branch: str) -> tuple[str, str, str]:
    """Poll gh for the latest CI run on a branch.

    Returns (verdict, status_str, run_id) where verdict is GREEN/RED/PENDING/UNKNOWN.
    """
    try:
        result = subprocess.run(
            [
                "gh", "run", "list",
                "--branch", branch,
                "--json", "databaseId,conclusion,headSha,status",
                "--jq", ".[0]",
                "-R", "sandboxcom/gludd",
                "-L", "1",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ("RED", "no run found", "")
        import json
        run = json.loads(result.stdout)
        conclusion = run.get("conclusion") or ""
        status = run.get("status") or ""
        run_id = str(run.get("databaseId", ""))
        if conclusion == "success":
            return ("GREEN", f"run {run_id} conclusion=success", run_id)
        if status in ("pending", "in_progress", "queued"):
            return ("PENDING", f"run {run_id} status={status}", run_id)
        if conclusion in ("failure", "cancelled", "timed_out", "skipped"):
            return ("RED", f"run {run_id} conclusion={conclusion}", run_id)
        return ("PENDING", f"run {run_id} status={status}", run_id)
    except Exception as exc:
        return ("UNKNOWN", f"gh error: {exc}", "")


def _format_heartbeat(elapsed: int, verdict: str, detail: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    mins = elapsed // 60
    secs = elapsed % 60
    return f"{ts} [heartbeat +{mins}m{secs:02d}s] CI {verdict}: {detail}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Poll CI for a branch until terminal state (green or red)."
    )
    parser.add_argument("branch", help="Git branch to monitor (required)")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Max seconds to wait (default: {DEFAULT_TIMEOUT})",
    )
    args = parser.parse_args()

    branch = args.branch
    timeout = args.timeout
    elapsed = 0

    head_sha = _last_commit_on_branch(branch)
    sha_slug = head_sha[:12] if head_sha else "unknown"

    print(f"=== CI-AWAIT: polling {branch} ({sha_slug}) every {POLL_INTERVAL}s "
          f"(timeout {timeout}s) ===")

    while elapsed < timeout:
        verdict, detail, _run_id = _fetch_ci_verdict(branch)
        print(_format_heartbeat(elapsed, verdict, detail))

        if verdict == "GREEN":
            print(f"=== CI GREEN after {elapsed}s ===")
            return 0
        if verdict == "RED":
            print(f"=== CI RED after {elapsed}s ===")
            return 1

        next_sleep = min(POLL_INTERVAL, timeout - elapsed)
        if next_sleep <= 0:
            break
        time.sleep(next_sleep)
        elapsed += next_sleep

    print(f"=== CI-AWAIT: timed out after {timeout}s ===")
    return 2


if __name__ == "__main__":
    sys.exit(main())
