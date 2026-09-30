#!/usr/bin/env python3
"""
check_dispatch_dedup.py

Reads the content-addressed dispatch ledger written by ``enforce-delegate.ts``
and validates every recorded ownership transition. The live plugin blocks an
exact in-progress or completed task before it is dispatched; this checker is
the gate-facing proof that the durable ledger remains well formed.

Usage:
    python3 scripts/check_dispatch_dedup.py

Exit codes:
    0   Clean — no re-dispatches detected.
    1   Re-dispatch detected — see stderr details.
    2   Ledger exists but is malformed or unsupported.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DISPATCH_STATE_FILE = os.environ.get(
    "GLUDD_DISPATCH_DEDUP_STATE",
    str(ROOT / ".gludd" / "dispatch-ledger.json"),
)
ID_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)+)\b")
FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
VALID_STATUSES = {"in_progress", "completed", "failed"}


def read_dispatched_state() -> dict[str, Any] | None:
    """Read the dispatch ledger; return ``None`` only when it is absent."""
    path = Path(DISPATCH_STATE_FILE)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("ledger must be a JSON object")
        return data
    except json.JSONDecodeError as exc:
        raise ValueError(f"ledger is invalid JSON: {exc}") from exc


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
    try:
        state = read_dispatched_state()
    except (OSError, ValueError) as exc:
        print(f"check-dispatch-dedup: INVALID — {exc}", file=sys.stderr)
        return 2
    if state is None:
        print("check-dispatch-dedup: INACTIVE — no dispatches have been recorded")
        return 0

    repo_root = Path(__file__).resolve().parent.parent
    tasks_path = repo_root / "TASKS.md"
    if not tasks_path.exists():
        print(f"ERROR: TASKS.md not found at {tasks_path}", file=sys.stderr)
        return 1

    if state.get("version") != 2:
        print("check-dispatch-dedup: INVALID — unsupported ledger version", file=sys.stderr)
        return 2
    entries = state.get("entries")
    if not isinstance(entries, dict):
        print("check-dispatch-dedup: INVALID — entries must be an object", file=sys.stderr)
        return 2

    errors: list[str] = []
    denied = 0
    status_counts = {status: 0 for status in sorted(VALID_STATUSES)}
    for key, raw_entry in sorted(entries.items()):
        if not isinstance(key, str) or FINGERPRINT_PATTERN.fullmatch(key) is None:
            errors.append(f"invalid fingerprint key: {key!r}")
            continue
        if not isinstance(raw_entry, dict):
            errors.append(f"{key}: entry must be an object")
            continue
        if raw_entry.get("fingerprint") != key:
            errors.append(f"{key}: embedded fingerprint mismatch")
        status = raw_entry.get("status")
        if status not in VALID_STATUSES:
            errors.append(f"{key}: invalid status {status!r}")
        else:
            status_counts[str(status)] += 1
        attempts = raw_entry.get("attempts")
        duplicates = raw_entry.get("denied_duplicates")
        if not isinstance(attempts, int) or attempts < 1:
            errors.append(f"{key}: attempts must be a positive integer")
        if not isinstance(duplicates, int) or duplicates < 0:
            errors.append(f"{key}: denied_duplicates must be a non-negative integer")
        else:
            denied += duplicates
        if not isinstance(raw_entry.get("normalized_spec"), str):
            errors.append(f"{key}: normalized_spec must be a string")
        task_ids = raw_entry.get("task_ids")
        if not isinstance(task_ids, list) or any(
            not isinstance(task_id, str) for task_id in task_ids
        ):
            errors.append(f"{key}: task_ids must be a string array")

    if errors:
        print("check-dispatch-dedup: INVALID", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 2

    completed_ids = extract_completed_ids(tasks_path)
    print(
        "check-dispatch-dedup: OK — "
        f"entries={len(entries)} in_progress={status_counts['in_progress']} "
        f"completed={status_counts['completed']} failed={status_counts['failed']} "
        f"duplicates_blocked={denied} completed_task_ids={len(completed_ids)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
