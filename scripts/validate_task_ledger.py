#!/usr/bin/env python3
"""
validate_task_ledger.py

Validates TASKS.md for self-tracking integrity:
  - Duplicate IDs
  - Re-dispatched completed items (unchecked item shares ID with completed item)
  - Stale in_progress items (older than 24h epoch timestamp, not checked)
  - Missing IDs (items without recognizable ID pattern)

Usage:
    python3 scripts/validate_task_ledger.py

Exit codes:
    0   Clean — no issues found.
    1   Issues found — see stderr summary.
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from general_ludd.validation.backlog_sources import ID_PATTERN as ID_PATTERN  # noqa: E402
from general_ludd.validation.backlog_sources import (  # noqa: E402
    TaskRecord,
    extract_tasks,
    task_is_effectively_complete,
)

STALE_SECONDS = 24 * 3600


def main() -> int:
    tasks_path = _REPO_ROOT / "TASKS.md"

    if not tasks_path.exists():
        print(f"ERROR: TASKS.md not found at {tasks_path}", file=sys.stderr)
        return 1

    checked, unchecked = extract_tasks(tasks_path)
    issues: list[str] = []
    now = int(time.time())

    # Build ID → task mappings
    checked_ids: dict[str, list[TaskRecord]] = defaultdict(list)
    unchecked_ids: dict[str, list[TaskRecord]] = defaultdict(list)
    global_ids: dict[str, list[TaskRecord]] = defaultdict(list)

    for task in checked:
        for tid in task["ids"]:
            checked_ids[tid].append(task)
            global_ids[tid].append(task)

    for task in unchecked:
        for tid in task["ids"]:
            unchecked_ids[tid].append(task)
            global_ids[tid].append(task)

    # A checkbox and an explicit status are independent completion evidence.
    # Contradictions fail closed instead of allowing either signal to hide
    # unfinished release work.
    for task in checked:
        if task["status"] is not None and not task_is_effectively_complete(
            task, checked=True
        ):
            issues.append(
                "STATUS-MISMATCH: checked item has non-complete status "
                f"{task['status']!r}: IDs={task['ids']}"
            )
    # 1. Duplicate IDs
    for tid, tasks in global_ids.items():
        if len(tasks) > 1:
            checked_count = sum(1 for t in tasks if t["line"].startswith("- [x]"))
            unchecked_count = sum(1 for t in tasks if t["line"].startswith("- [ ]"))
            if checked_count > 0 and unchecked_count > 0:
                issues.append(
                    f"RE-DISPATCH: ID {tid} exists in BOTH checked and unchecked items "
                    f"({checked_count} checked, {unchecked_count} unchecked)"
                )
            elif unchecked_count > 1:
                issues.append(
                    f"DUPLICATE: ID {tid} appears in {unchecked_count} unchecked items"
                )

    # 2. Stale in_progress items
    for task in unchecked:
        if task["status"] == "in_progress" and task["epoch"]:
            age = now - task["epoch"]
            if age > STALE_SECONDS:
                hours = age / 3600
                issues.append(
                    f"STALE: in_progress item older than 24h ({hours:.1f}h): "
                    f"IDs={task['ids']}"
                )

    # 3. Missing IDs (unchecked items without any ID)
    missing_count = 0
    for task in unchecked:
        if not task["ids"]:
            missing_count += 1
    if missing_count > 0:
        issues.append(
            f"MISSING-ID: {missing_count} unchecked item(s) lack a recognizable "
            f"task ID (expected pattern like W.1, A.2, G.5, H.16, FIX-3)"
        )

    # 4. Summary
    print(f"validate-task-ledger: TASKS.md parsed — "
          f"{len(checked)} checked, {len(unchecked)} unchecked items")

    if issues:
        print(f"validate-task-ledger: {len(issues)} issue(s) found:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    else:
        print("validate-task-ledger: OK — no issues detected")
        return 0


if __name__ == "__main__":
    sys.exit(main())
