#!/usr/bin/env python3
"""
check_dispatch_dedup.py

Reads /tmp/gludd-dispatched-tasks.json (a dispatch state file recording task IDs
dispatched with timestamps) and cross-references against TASKS.md completed items
to flag potential re-dispatches of already-completed work.

Usage:
    python3 scripts/check_dispatch_dedup.py

Exit codes:
    0   Clean — no re-dispatches detected.
    1   Re-dispatch detected — see stderr details.
    2   State file absent or empty — advisory only (not yet a hard failure).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DISPATCH_STATE_FILE = "/tmp/gludd-dispatched-tasks.json"
ID_PATTERN = re.compile(r"(?:^|\s)([A-Z]{1,3}\d*\.\d+(?:\.\d+)*)(?:\s|$|\.)")


def read_dispatched_state() -> dict | None:
    """Read dispatch state file. Return None if absent/empty."""
    path = Path(DISPATCH_STATE_FILE)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data or not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, ValueError):
        return None


def extract_completed_ids(tasks_path: Path) -> set[str]:
    """Extract all task IDs from checked items in TASKS.md."""
    text = tasks_path.read_text(encoding="utf-8")
    completed: set[str] = set()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- [x]"):
            continue
        for tid in ID_PATTERN.findall(stripped):
            completed.add(tid)

    return completed


def main() -> int:
    state = read_dispatched_state()
    if state is None:
        print("check-dispatch-dedup: no dispatch state file — assuming clean")
        return 0

    repo_root = Path(__file__).resolve().parent.parent
    tasks_path = repo_root / "TASKS.md"
    if not tasks_path.exists():
        print(f"ERROR: TASKS.md not found at {tasks_path}", file=sys.stderr)
        return 1

    completed_ids = extract_completed_ids(tasks_path)
    re_dispatches: list[str] = []

    dispatched = state.get("dispatched", [])
    if not isinstance(dispatched, list):
        dispatched = []

    for entry in dispatched:
        if not isinstance(entry, dict):
            continue
        tid = entry.get("id", "")
        if not tid:
            continue
        if tid in completed_ids:
            timestamp = entry.get("ts", "unknown")
            re_dispatches.append(f"  {tid} (dispatched at {timestamp})")

    if re_dispatches:
        print(
            f"check-dispatch-dedup: RE-DISPATCH detected — "
            f"{len(re_dispatches)} completed task(s) re-dispatched:",
            file=sys.stderr,
        )
        for item in re_dispatches:
            print(item, file=sys.stderr)
        return 1

    print(f"check-dispatch-dedup: OK — {len(dispatched)} dispatched, "
          f"0 re-dispatches against {len(completed_ids)} completed items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
