#!/usr/bin/env python3
"""Compact CI run dashboard — no polling, one-shot `gh run list --json`.

Prints one line per run: STATUS, CONCLUSION, BRANCH, AGE, SHA. Supports
--limit N (default 5) and --branch BRANCH filters. Exits 0 on success,
non-zero on gh unavailable (fail-open — a missing gh CLI is not a CI failure).

Usage::

    python3 scripts/ci_dashboard.py [--limit N] [--branch BRANCH]

    make ci-dashboard
    make ci-dashboard LIMIT=10
    make ci-dashboard BRANCH=development
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO = "sandboxcom/gludd"
FIELDS = [
    "databaseId",
    "status",
    "conclusion",
    "headBranch",
    "headSha",
    "createdAt",
    "event",
]


def _now_epoch() -> float:
    return time.time()


def _fmt_ago(ts_str: str) -> str:
    if not ts_str:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        ts = dt.timestamp()
    except (ValueError, OSError):
        return ts_str[:16]
    delta = _now_epoch() - ts
    if delta < 0:
        return "future"
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta / 60)}m"
    if delta < 86400:
        return f"{delta / 3600:.1f}h"
    return f"{delta / 86400:.1f}d"


def _status_icon(status: str) -> str:
    s = (status or "").upper()
    if s == "COMPLETED":
        return "✓"
    if s in ("IN_PROGRESS", "QUEUED", "PENDING", "WAITING"):
        return "●"
    return "?"


def _conclusion_label(conclusion: str) -> str:
    c = (conclusion or "").upper()
    if c == "SUCCESS":
        return "GREEN"
    if c in ("FAILURE", "CANCELLED", "TIMED_OUT"):
        return "RED"
    if c:
        return c
    return "—"


def fetch_runs(limit: int, branch: str | None) -> list[dict]:
    args = [
        "gh", "run", "list", "-R", REPO,
        "--json", ",".join(FIELDS),
        "-L", str(max(limit, 1)),
    ]
    if branch:
        args.extend(["-b", branch])

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        print("gh CLI not found — install with: make ci-install-gh", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("gh run list timed out", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f"gh run list failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print("gh run list returned invalid JSON", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compact CI run dashboard")
    parser.add_argument("--limit", type=int, default=5, help="Max runs to show (default: 5)")
    parser.add_argument("--branch", type=str, default=None, help="Filter by branch (default: all)")
    args = parser.parse_args()

    runs = fetch_runs(args.limit, args.branch)

    if not runs:
        print("No CI runs found.")
        return 0

    print(
        f"=== CI DASHBOARD — {REPO} — "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} "
        f"(last {len(runs)} runs) ==="
    )
    print(f"{'STATUS':>8}  {'CONCLUSION':>10}  {'BRANCH':<20}  {'AGE':>7}  SHA")
    print("-" * 80)

    for run in runs:
        status = run.get("status", "?") or "?"
        conclusion = run.get("conclusion") or "—"
        branch = run.get("headBranch", "—") or "—"
        sha = (run.get("headSha") or "—")[:8]
        created = run.get("createdAt", "")
        icon = _status_icon(status)
        label = _conclusion_label(conclusion)
        ago = _fmt_ago(created)
        event = run.get("event", "")

        event_tag = ""
        if event == "schedule":
            event_tag = " [cron]"
        elif event:
            event_tag = f" [{event}]"

        print(
            f"{icon}{status:<7}  {label:>10}  "
            f"{branch:<20}  {ago:>7}  {sha}{event_tag}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
