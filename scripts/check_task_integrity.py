#!/usr/bin/env python3
"""check_task_integrity.py — validate TASKS.md structural integrity.

Checks:
  1. Every checked item (- [x]) has a non-empty `| evidence:` field with
     actual content (not just a wave label like "Wave 34").
  2. Every item (checked or not) has all required fields:
     priority, effort, status.
  3. No duplicate item IDs.
  4. Valid priority values (high, medium, low).
  5. Valid status values (pending, in_progress, completed, blocked, cancelled).
  6. Valid effort values (XS, S, M, L, XL).

Exits 0 on clean, 1 on violations with specific line numbers.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_PATH = ROOT / "TASKS.md"

VALID_PRIORITIES = {"high", "medium", "low"}
VALID_STATUSES = {"pending", "in_progress", "completed", "blocked", "cancelled"}
VALID_EFFORTS = {"XS", "S", "M", "L", "XL"}


def main() -> int:
    if not TASKS_PATH.exists():
        print(f"ERROR: {TASKS_PATH} not found")
        return 1

    content = TASKS_PATH.read_text()
    lines = content.split("\n")
    violations: list[str] = []

    # Find task items: lines matching `- [ ] ...` or `- [x] ...`
    task_re = re.compile(r"^[ \t]*[-*]\s*\[([ xX])\]\s+(.+)$")
    items: list[tuple[int, bool, str]] = []
    for i, line in enumerate(lines, start=1):
        m = task_re.match(line)
        if m:
            checked = m.group(1).lower() == "x"
            body = m.group(2).strip()
            items.append((i, checked, body))

    # Check 1: every checked item must have non-empty evidence
    for lineno, checked, body in items:
        if not checked:
            continue
        if "| evidence:" not in body:
            violations.append(
                f"line {lineno}: checked item lacks | evidence: field"
            )
            continue
        evidence_match = re.search(r"\|\s*evidence\s*:\s*(.*?)(?:\s*\|.*|\s*)$", body)
        if not evidence_match:
            violations.append(
                f"line {lineno}: checked item has malformed | evidence: field"
            )
            continue
        evidence_val = evidence_match.group(1).strip()
        if not evidence_val:
            violations.append(
                f"line {lineno}: checked item has empty | evidence: value"
            )
            continue
        wave_label_re = re.compile(
            r"^(Wave\s*\d+|Waves?\s*\d+([-\u2013]\d+)?|wave\s*\d+|Session\s*\d+|"
            r"\d{4}-\d{2}-\d{2}\s+(?:waves?\s*\d+|session\s*\d+)|"
            r"session\s*\d+.*closure|wave\s*closure)$",
            re.IGNORECASE,
        )
        if wave_label_re.match(evidence_val):
            violations.append(
                f"line {lineno}: evidence value '{evidence_val}' is a wave/session label, "
                "not measurable evidence (commit hash, test count, CI run id, gate output)"
            )

    # Check 2: every item must have required fields
    for lineno, _checked, body in items:
        has_priority = re.search(r"\|\s*priority\s*:", body)
        has_effort = re.search(r"\|\s*effort\s*:", body)
        has_status = re.search(r"\|\s*status\s*:", body)
        missing: list[str] = []
        if not has_priority:
            missing.append("priority")
        if not has_effort:
            missing.append("effort")
        if not has_status:
            missing.append("status")
        if missing:
            violations.append(
                f"line {lineno}: item missing required field(s): {', '.join(missing)}"
            )

    # Check 3: validate field values
    for lineno, _checked, body in items:
        pm = re.search(r"\|\s*priority\s*:\s*(\S+)", body)
        if pm:
            val = pm.group(1).rstrip("|").strip()
            if val not in VALID_PRIORITIES:
                violations.append(
                    f"line {lineno}: invalid priority '{val}' "
                    f"(valid: {', '.join(sorted(VALID_PRIORITIES))})"
                )
        sm = re.search(r"\|\s*status\s*:\s*(\S+)", body)
        if sm:
            val = sm.group(1).rstrip("|").strip()
            if val not in VALID_STATUSES:
                violations.append(
                    f"line {lineno}: invalid status '{val}' "
                    f"(valid: {', '.join(sorted(VALID_STATUSES))})"
                )
        em = re.search(r"\|\s*effort\s*:\s*(\S+)", body)
        if em:
            val = em.group(1).rstrip("|").strip()
            if val not in VALID_EFFORTS:
                violations.append(
                    f"line {lineno}: invalid effort '{val}' "
                    f"(valid: {', '.join(sorted(VALID_EFFORTS))})"
                )

    # Check 4: no duplicate item IDs
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

    if violations:
        print(f"TASKS.md integrity check FAILED ({len(violations)} violation(s)):")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(f"TASKS.md integrity check PASSED ({len(items)} items, 0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
