#!/usr/bin/env python3
"""Validate the structural integrity of active TASKS.md entries.

Archived task snapshots predate the current evidence-ledger schema, so the audit
deliberately ignores them while continuing to validate later active sections.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_PATH = ROOT / "TASKS.md"

VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_STATUSES = {"pending", "in_progress", "completed", "blocked", "cancelled"}
VALID_EFFORTS = {"XS", "S", "M", "L", "XL", "small", "medium", "large"}

_TASK_RE = re.compile(r"^[ \t]*[-*]\s*\[([ xX])\]\s+(.+)$")
_HEADING_RE = re.compile(r"^[ \t]*(#{1,6})\s+(.+?)\s*$")
_ARCHIVE_RE = re.compile(r"\barchive(?:d)?\b", re.IGNORECASE)
_ID_RE = re.compile(r"^(\S+)")
_EMBEDDED_TASK_RE = re.compile(r"(?:^|\s)[-*]\s*\[[ xX]\]\s+")
_WAVE_LABEL_RE = re.compile(
    r"^(Wave\s*\d+|Waves?\s*\d+([-\u2013]\d+)?|wave\s*\d+|Session\s*\d+|"
    r"\d{4}-\d{2}-\d{2}\s+(?:waves?\s*\d+|session\s*\d+)|"
    r"session\s*\d+.*closure|wave\s*closure)$",
    re.IGNORECASE,
)


def _active_items(content: str) -> list[tuple[int, bool, str]]:
    """Return checklist entries outside archived Markdown sections."""

    items: list[tuple[int, bool, str]] = []
    archived_level: int | None = None

    for lineno, line in enumerate(content.splitlines(), start=1):
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            if archived_level is not None and level <= archived_level:
                archived_level = None
            if archived_level is None and _ARCHIVE_RE.search(heading.group(2)):
                archived_level = level
            continue

        if archived_level is not None:
            continue
        task = _TASK_RE.match(line)
        if task:
            items.append(
                (
                    lineno,
                    task.group(1).lower() == "x",
                    task.group(2).strip(),
                )
            )

    return items


def audit_content(content: str) -> tuple[list[str], int]:
    """Audit TASKS Markdown and return violations plus active item count."""

    violations: list[str] = []
    items = _active_items(content)

    for lineno, _checked, body in items:
        if _EMBEDDED_TASK_RE.search(body):
            violations.append(
                f"line {lineno}: multiple checklist items must use separate physical lines"
            )

    for lineno, checked, body in items:
        if not checked:
            continue
        if "| evidence:" not in body:
            violations.append(f"line {lineno}: checked item lacks | evidence: field")
            continue
        evidence_match = re.search(
            r"\|\s*evidence\s*:\s*(.*?)(?:\s*\|.*|\s*)$",
            body,
        )
        if not evidence_match:
            violations.append(
                f"line {lineno}: checked item has malformed | evidence: field"
            )
            continue
        evidence_value = evidence_match.group(1).strip()
        if not evidence_value:
            violations.append(f"line {lineno}: checked item has empty | evidence: value")
        elif _WAVE_LABEL_RE.match(evidence_value):
            violations.append(
                f"line {lineno}: evidence value '{evidence_value}' is a wave/session "
                "label, not measurable evidence (commit hash, test count, CI run id, "
                "gate output)"
            )

    for lineno, _checked, body in items:
        missing = [
            field
            for field in ("priority", "effort", "status")
            if re.search(rf"\|\s*{field}\s*:", body) is None
        ]
        if missing:
            violations.append(
                f"line {lineno}: item missing required field(s): {', '.join(missing)}"
            )

    field_contracts = (
        ("priority", VALID_PRIORITIES),
        ("status", VALID_STATUSES),
        ("effort", VALID_EFFORTS),
    )
    for lineno, _checked, body in items:
        for field, valid_values in field_contracts:
            match = re.search(rf"\|\s*{field}\s*:\s*(\S+)", body)
            if match is None:
                continue
            value = match.group(1).rstrip("|").strip()
            if value not in valid_values:
                violations.append(
                    f"line {lineno}: invalid {field} '{value}' "
                    f"(valid: {', '.join(sorted(valid_values))})"
                )

    seen_ids: dict[str, int] = {}
    for lineno, _checked, body in items:
        item_match = _ID_RE.match(body)
        if item_match is None:
            continue
        item_id = item_match.group(1)
        if item_id in seen_ids:
            violations.append(
                f"line {lineno}: duplicate item ID '{item_id}' "
                f"(first seen at line {seen_ids[item_id]})"
            )
        else:
            seen_ids[item_id] = lineno

    return violations, len(items)


def main() -> int:
    if not TASKS_PATH.exists():
        print(f"ERROR: {TASKS_PATH} not found")
        return 1

    violations, item_count = audit_content(TASKS_PATH.read_text())
    if violations:
        print(f"TASKS.md integrity check FAILED ({len(violations)} violation(s)):")
        for violation in violations:
            print(f"  - {violation}")
        return 1

    print(f"TASKS.md integrity check PASSED ({item_count} items, 0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
