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

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, TypeGuard

ROOT = Path(__file__).resolve().parent.parent
DISPATCH_STATE_FILE = os.environ.get(
    "GLUDD_DISPATCH_DEDUP_STATE",
    str(ROOT / ".gludd" / "dispatch-ledger.json"),
)
ID_PATTERN = re.compile(r"\b([A-Z]{1,3}\d*\.\d+(?:\.\d+)*)\b")
TASK_ID_PATTERN = re.compile(r"[A-Z]{1,3}\d*\.\d+(?:\.\d+)*\Z")
LEGACY_TASK_ID_PATTERN = re.compile(r"[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)+\Z")
FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
VALID_STATUSES = {"in_progress", "completed", "failed", "cancelled"}
VALID_TOOLS = {"task", "agent", "workflow"}


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


def _is_non_negative_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_common_entry(
    key: str,
    raw_entry: dict[str, Any],
    errors: list[str],
    task_id_pattern: re.Pattern[str] = TASK_ID_PATTERN,
) -> tuple[str | None, int]:
    if raw_entry.get("fingerprint") != key:
        errors.append(f"{key}: embedded fingerprint mismatch")
    status = raw_entry.get("status")
    if status not in VALID_STATUSES:
        errors.append(f"{key}: invalid status {status!r}")
        status = None
    attempts = raw_entry.get("attempts")
    duplicates = raw_entry.get("denied_duplicates")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        errors.append(f"{key}: attempts must be a positive integer")
    if not _is_non_negative_integer(duplicates):
        errors.append(f"{key}: denied_duplicates must be a non-negative integer")
        duplicate_count = 0
    else:
        duplicate_count = duplicates
    task_ids = raw_entry.get("task_ids")
    if not isinstance(task_ids, list) or any(
        not isinstance(task_id, str) for task_id in task_ids
    ):
        errors.append(f"{key}: task_ids must be a string array")
    elif task_ids != sorted(set(task_ids)):
        errors.append(f"{key}: task_ids must be sorted and unique")
    elif any(task_id_pattern.fullmatch(task_id) is None for task_id in task_ids):
        errors.append(f"{key}: task_ids contains an invalid tracked ID")
    return status, duplicate_count


def _validate_v2_entry(
    key: str,
    raw_entry: dict[str, Any],
    errors: list[str],
) -> None:
    normalized_spec = raw_entry.get("normalized_spec")
    if not isinstance(normalized_spec, str):
        errors.append(f"{key}: normalized_spec must be a string")
        return
    expected = hashlib.sha256(normalized_spec.encode()).hexdigest()
    if expected != key:
        errors.append(f"{key}: normalized_spec digest mismatch")


def _validate_v3_entry(
    key: str,
    raw_entry: dict[str, Any],
    status: str | None,
    errors: list[str],
) -> None:
    digests = [
        raw_entry.get("project_digest"),
        raw_entry.get("scope_digest"),
        raw_entry.get("spec_digest"),
    ]
    if any(
        not isinstance(value, str) or FINGERPRINT_PATTERN.fullmatch(value) is None
        for value in digests
    ):
        errors.append(f"{key}: project, scope, and spec digests must be SHA-256")
    tool = raw_entry.get("tool")
    if tool not in VALID_TOOLS:
        errors.append(f"{key}: invalid tool {tool!r}")
    if all(isinstance(value, str) for value in digests) and isinstance(tool, str):
        canonical = "\n".join(
            ["dispatch-ledger-v3", tool, *(str(value) for value in digests)]
        )
        expected = hashlib.sha256(canonical.encode()).hexdigest()
        if expected != key:
            errors.append(f"{key}: scoped identity digest mismatch")
    stale_recoveries = raw_entry.get("stale_recoveries")
    if not _is_non_negative_integer(stale_recoveries):
        errors.append(f"{key}: stale_recoveries must be a non-negative integer")
    elif isinstance(raw_entry.get("attempts"), int) and stale_recoveries >= int(
        raw_entry["attempts"]
    ):
        errors.append(f"{key}: stale_recoveries must be lower than attempts")
    for timestamp in ("first_dispatched_at", "updated_at"):
        if not _is_non_negative_integer(raw_entry.get(timestamp)):
            errors.append(f"{key}: {timestamp} must be a non-negative integer")
    owner = raw_entry.get("owner")
    if not isinstance(owner, dict):
        errors.append(f"{key}: owner must be an object")
    else:
        owner_id = owner.get("owner_id")
        if not isinstance(owner_id, str) or FINGERPRINT_PATTERN.fullmatch(owner_id) is None:
            errors.append(f"{key}: owner_id must be a SHA-256 digest")
        for field in ("pid", "process_started_at_ms", "claimed_at"):
            if not _is_non_negative_integer(owner.get(field)):
                errors.append(f"{key}: owner.{field} must be a non-negative integer")
    terminal_reason = raw_entry.get("terminal_reason")
    expected_reasons = {
        "in_progress": {None},
        "completed": {"success"},
        "failed": {"error"},
        "cancelled": {"cancelled", "stale_owner"},
    }
    if status in expected_reasons and terminal_reason not in expected_reasons[status]:
        errors.append(f"{key}: terminal_reason does not match status")
    first_dispatched_at = raw_entry.get("first_dispatched_at")
    updated_at = raw_entry.get("updated_at")
    if (
        _is_non_negative_integer(first_dispatched_at)
        and _is_non_negative_integer(updated_at)
        and first_dispatched_at > updated_at
    ):
        errors.append(f"{key}: first_dispatched_at exceeds updated_at")
    if isinstance(owner, dict):
        claimed_at = owner.get("claimed_at")
        if (
            _is_non_negative_integer(claimed_at)
            and _is_non_negative_integer(updated_at)
            and claimed_at > updated_at
        ):
            errors.append(f"{key}: owner.claimed_at exceeds updated_at")


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

    version = state.get("version")
    if version not in {2, 3}:
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
        task_id_pattern = (
            LEGACY_TASK_ID_PATTERN if version == 2 else TASK_ID_PATTERN
        )
        status, duplicates = _validate_common_entry(
            key, raw_entry, errors, task_id_pattern
        )
        if status is not None:
            status_counts[status] += 1
        denied += duplicates
        if version == 2:
            _validate_v2_entry(key, raw_entry, errors)
        else:
            _validate_v3_entry(key, raw_entry, status, errors)

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
        f"cancelled={status_counts['cancelled']} "
        f"duplicates_blocked={denied} completed_task_ids={len(completed_ids)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
