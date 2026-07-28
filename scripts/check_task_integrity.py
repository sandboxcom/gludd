#!/usr/bin/env python3
"""check_task_integrity.py — validate TASKS.md structural integrity.

Checks live (non-archived) ledger entries:
  1. Every checked item (- [x]) has a non-empty `| evidence:` field with
     actual content (not just a wave label like "Wave 34").
  2. Every unchecked item has the current required fields:
     priority, effort, status.
  3. No duplicate live item IDs.
  4. Field values use the project's accepted vocabulary.

Historical session snapshots below ``## Archived`` headings are intentionally
preserved and are not revalidated against schema rules introduced later.  A
subsequent level-two heading (for example ``## Session 54 — Active``) returns
the audit to live-entry mode.

Exits 0 on clean, 1 on violations with specific line numbers.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_PATH = ROOT / "TASKS.md"

VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_STATUSES = {"pending", "in_progress", "completed", "blocked", "cancelled"}
VALID_EFFORTS = {
    "XS",
    "S",
    "M",
    "L",
    "XL",
    "small",
    "medium",
    "large",
}
TASK_RE = re.compile(r"^[ \t]*[-*]\s*\[([ xX])\]\s+(.+)$")
ARCHIVED_HEADING_RE = re.compile(r"^##\s+Archived\b", re.IGNORECASE)
ACTIVE_HEADING_RE = re.compile(r"^##\s+.*\bActive\b", re.IGNORECASE)
LEVEL_TWO_HEADING_RE = re.compile(r"^##\s+")
WAVE_LABEL_RE = re.compile(
    r"^(Wave\s*\d+|Waves?\s*\d+(?:-\d+)?|wave\s*\d+|Session\s*\d+|"
    r"\d{4}-\d{2}-\d{2}\s+(?:waves?\s*\d+|session\s*\d+)|"
    r"session\s*\d+.*closure|wave\s*closure)$",
    re.IGNORECASE,
)


def _field_value(body: str, field: str) -> str | None:
    match = re.search(
        rf"(?:^|\|)\s*{re.escape(field)}\s*:\s*([^|]*?)\s*(?=\||$)",
        body,
    )
    if not match:
        return None
    return match.group(1).strip()


def _live_items(content: str) -> tuple[list[str], list[tuple[int, bool, str]]]:
    lines = content.split("\n")
    items: list[tuple[int, bool, str]] = []
    in_archived_section = False

    for i, line in enumerate(lines, start=1):
        if ARCHIVED_HEADING_RE.match(line):
            in_archived_section = True
        elif (
            in_archived_section
            and LEVEL_TWO_HEADING_RE.match(line)
            and ACTIVE_HEADING_RE.match(line)
        ):
            in_archived_section = False
        if in_archived_section:
            continue
        m = TASK_RE.match(line)
        if m:
            checked = m.group(1).lower() == "x"
            body = m.group(2).strip()
            items.append((i, checked, body))
    return lines, items


def audit_content(content: str) -> tuple[list[str], int]:
    lines, items = _live_items(content)
    violations: list[str] = []

    # Checked live entries must provide measurable evidence.
    for lineno, checked, body in items:
        if not checked:
            continue
        evidence_val = _field_value(body, "evidence")
        if evidence_val is None:
            violations.append(
                f"line {lineno}: checked item lacks | evidence: field"
            )
            continue
        if not evidence_val:
            violations.append(
                f"line {lineno}: checked item has empty | evidence: value"
            )
            continue
        if WAVE_LABEL_RE.match(evidence_val):
            violations.append(
                f"line {lineno}: evidence value '{evidence_val}' is a wave/session label, "
                "not measurable evidence (commit hash, test count, CI run id, gate output)"
            )

    # Only pending work is migrated to the current metadata schema.  Completed
    # rows retain their historical formatting while still requiring evidence.
    for lineno, checked, body in items:
        if checked:
            continue
        missing: list[str] = []
        for field in ("priority", "effort", "status"):
            if _field_value(body, field) is None:
                missing.append(field)
        if missing:
            violations.append(
                f"line {lineno}: item missing required field(s): {', '.join(missing)}"
            )

    # Validate current metadata for pending work.  Completed rows retain their
    # historical field vocabulary while evidence remains mandatory above.
    for lineno, checked, body in items:
        if checked:
            continue
        val = _field_value(body, "priority")
        if val is not None and val not in VALID_PRIORITIES:
            violations.append(
                f"line {lineno}: invalid priority '{val}' "
                f"(valid: {', '.join(sorted(VALID_PRIORITIES))})"
            )
        val = _field_value(body, "status")
        if val is not None and val not in VALID_STATUSES:
            violations.append(
                f"line {lineno}: invalid status '{val}' "
                f"(valid: {', '.join(sorted(VALID_STATUSES))})"
            )
        val = _field_value(body, "effort")
        if val is not None and val not in VALID_EFFORTS:
            violations.append(
                f"line {lineno}: invalid effort '{val}' "
                f"(valid: {', '.join(sorted(VALID_EFFORTS))})"
            )

    # No duplicate IDs among live entries.  Archived session snapshots
    # deliberately repeat IDs and have already been excluded above.
    id_re = re.compile(r"^[ \t]*[-*]\s*\[[ xX]\]\s+(\S+)")
    seen_ids: dict[str, int] = {}
    for lineno, _checked, _body in items:
        m = id_re.match(lines[lineno - 1])
        if m:
            item_id = m.group(1)
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
        for v in violations:
            print(f"  - {v}")
        return 1

    print(f"TASKS.md integrity check PASSED ({item_count} live items, 0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
