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

import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ID_PATTERN = re.compile(r"\b([A-Z]{1,3}\d*(?:\.\d+(?:\.\d+)*|-\d+))\b")
PRIMARY_ID_PATTERN = re.compile(r"^-\s*\[[ x]\]\s+([A-Z]{1,3}\d*(?:\.\d+(?:\.\d+)*|-\d+))\b")
EPOCH_PATTERN = re.compile(r"epoch\s+(\d{10,})")
STALE_SECONDS = 24 * 3600


def _primary_ids(stripped: str) -> list[str]:
    """Extract the primary task ID(s) from a checkbox line.

    Returns contiguous ID matches immediately after the checkbox marker as
    primary IDs.
    Secondary IDs in evidence text are NOT extracted to avoid false
    re-dispatch/duplicate flags when items reference each other.
    """
    marker = re.match(r"^-\s*\[[ x]\]\s+(?P<body>.*)$", stripped)
    if marker is None:
        return []
    body = marker.group("body")
    ids: list[str] = []
    pos = 0
    while pos < len(body):
        while pos < len(body) and body[pos].isspace():
            pos += 1
        match = ID_PATTERN.match(body, pos)
        if match is None:
            break
        ids.append(match.group(1))
        pos = match.end()
    return ids


def _all_ids(stripped: str) -> list[str]:
    """Extract all IDs for MISSING-ID detection."""
    return ID_PATTERN.findall(stripped)


def extract_tasks(tasks_path: Path) -> tuple[list[dict], list[dict]]:
    """Parse TASKS.md, return (checked, unchecked) lists of task dicts."""
    text = tasks_path.read_text(encoding="utf-8")
    checked: list[dict] = []
    unchecked: list[dict] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("- [x]", "- [ ]")):
            continue

        is_checked = stripped.startswith("- [x]")
        all_ids = _all_ids(stripped)

        # Primary IDs used for dedup/re-dispatch — only the ID immediately
        # after the checkbox marker. Secondary IDs in evidence text are
        # informational cross-references, not task declarations.
        primary = _primary_ids(stripped)

        # Extract epoch timestamp if present
        epoch_match = re.search(r"(?:epoch|ts)\s+(\d{10,})", stripped)
        epoch = int(epoch_match.group(1)) if epoch_match else None

        status_match = re.search(r"status:\s*(\S+)", stripped)
        status = status_match.group(1) if status_match else None

        task: dict = {
            "line": stripped,
            "ids": primary,
            "all_ids": all_ids,
            "status": status,
            "epoch": epoch,
        }
        if is_checked:
            checked.append(task)
        else:
            unchecked.append(task)

    return checked, unchecked


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    tasks_path = repo_root / "TASKS.md"

    if not tasks_path.exists():
        print(f"ERROR: TASKS.md not found at {tasks_path}", file=sys.stderr)
        return 1

    checked, unchecked = extract_tasks(tasks_path)
    issues: list[str] = []
    now = int(time.time())

    # Build ID → task mappings
    checked_ids: dict[str, list[dict]] = defaultdict(list)
    unchecked_ids: dict[str, list[dict]] = defaultdict(list)
    global_ids: dict[str, list[dict]] = defaultdict(list)

    for task in checked:
        for tid in task["ids"]:
            checked_ids[tid].append(task)
            global_ids[tid].append(task)

    for task in unchecked:
        for tid in task["ids"]:
            unchecked_ids[tid].append(task)
            global_ids[tid].append(task)

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
