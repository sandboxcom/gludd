#!/usr/bin/env python3
"""
task_ttl_check.py — detect stale / frozen subagent tasks.

Each task/agent/workflow dispatch is recorded by `.opencode/plugin/enforce-deadline.ts`
into a JSON state file (default `/tmp/gludd-task-deadlines.json`) shaped as:

    { "<task_id>": <dispatch epoch ms>, ... }

This script reads that file, compares each task's age against the TTL, and reports
any task whose elapsed wall-clock exceeds the limit. Stale tasks should be
re-dispatched, re-split, or abandoned by the orchestrator.

Usage:
    python3 scripts/task_ttl_check.py [--timeout 300] [--state /path/to/state.json]

Exit codes:
    0  — all tracked tasks are fresh (or no tasks tracked)
    1  — at least one task exceeds the TTL (stale)
    2  — internal error (fail-open; treat as "no verdict")
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from scripts import gludd_env_defaults as gludd_env_defaults
except ModuleNotFoundError:  # pragma: no cover - direct launch from scripts/
    import gludd_env_defaults

DEFAULT_TIMEOUT_S = int(os.environ.get("GLUDD_TASK_TIMEOUT_MS", gludd_env_defaults.TASK_TIMEOUT_MS_DEFAULT)) // 1000
DEFAULT_STATE = os.environ.get(
    "GLUDD_TASK_DEADLINE_STATE", "/tmp/gludd-task-deadlines.json"
 )


def load_deadlines(path: str | Path) -> dict[str, float]:
    """Load the deadline state file. Returns {} on missing/invalid (fail-open)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in data.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def find_stale(
    deadlines: dict[str, float], timeout_s: float, now: float | None = None
 ) -> list[tuple[str, float]]:
    """Return list of (task_id, elapsed_seconds) for tasks past the TTL."""
    if now is None:
        now = time.time() * 1000.0
    stale: list[tuple[str, float]] = []
    timeout_ms = timeout_s * 1000.0
    for tid, start_ms in deadlines.items():
        if start_ms <= 0:
            continue
        elapsed_ms = now - start_ms
        if elapsed_ms > timeout_ms:
            stale.append((tid, elapsed_ms / 1000.0))
    return stale


def main(argv: list[str] | None = None, *, now_ms: float | None = None) -> int:
    """Run the TTL check.

    ``now_ms`` is an injection point for tests so they can pin "now" to a
    synthetic value instead of wall-clock time. Production callers omit it.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(DEFAULT_TIMEOUT_S),
        help="TTL in seconds (default: $GLUDD_TASK_TIMEOUT_MS / 1000 or 300)",
    )
    parser.add_argument(
        "--state",
        default=DEFAULT_STATE,
        help="Path to deadline state JSON (default: $GLUDD_TASK_DEADLINE_STATE "
        "or /tmp/gludd-task-deadlines.json)",
    )
    args = parser.parse_args(argv)

    deadlines = load_deadlines(args.state)

    if not deadlines:
        print(f"OK: no tracked tasks in {args.state}")
        return 0

    # Convert to ms for find_stale; inject real time only if caller didn't.
    now_for_find = now_ms if now_ms is not None else None
    stale = find_stale(deadlines, args.timeout, now=now_for_find)
    total = len(deadlines)

    if not stale:
        print(
            f"OK: {total} tracked task(s), all within TTL ({args.timeout:.0f}s)"
        )
        return 0

    print(
        f"STALE TASKS DETECTED: {len(stale)} of {total} tracked task(s) "
        f"exceed TTL of {args.timeout:.0f}s:"
    )
    for tid, elapsed in sorted(stale, key=lambda x: -x[1]):
        print(
            f"  STALE  task {tid}  elapsed={elapsed:.1f}s  "
            f"over={elapsed - args.timeout:.1f}s"
        )
    print(
        "WARNING: stale tasks detected — orchestrator should re-dispatch, "
        "re-split, or abandon them."
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover - fail-open path
        print(f"ERROR: task_ttl_check failed: {exc}", file=sys.stderr)
        sys.exit(2)
