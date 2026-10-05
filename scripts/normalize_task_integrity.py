#!/usr/bin/env python3
"""Normalize legacy TASKS.md records without fabricating completion evidence.

The integrity checker is intentionally strict for checked records.  Older
records predate that schema, so this one-shot migration adds the required
metadata, reopens checked records that lack measurable evidence, and keeps
the original evidence text as a ``prior-evidence`` annotation.
"""
from __future__ import annotations

import re
from pathlib import Path

from general_ludd.quality.preflight import TASK_TICK_FORBIDDEN_WORDS

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "TASKS.md"
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_STATUSES = {"pending", "in_progress", "completed", "blocked", "cancelled"}
EFFORT_MAP = {
    "extra-small": "XS",
    "xs": "XS",
    "small": "S",
    "s": "S",
    "medium": "M",
    "m": "M",
    "large": "L",
    "l": "L",
    "extra-large": "XL",
    "xl": "XL",
}
WAVE_EVIDENCE = re.compile(
    r"^(Wave\s*\d+|Waves?\s*\d+([\-\u2013]\d+)?|wave\s*\d+|Session\s*\d+|"
    r"\d{4}-\d{2}-\d{2}\s+(?:waves?\s*\d+|session\s*\d+)|"
    r"session\s*\d+.*closure|wave\s*closure)$",
    re.IGNORECASE,
)
ITEM = re.compile(r"^(?P<prefix>\s*[-*]\s*\[)(?P<mark>[ xX])(?P<close>\]\s+)(?P<body>.+)$")


def field(body: str, name: str) -> str | None:
    """Return one pipe-delimited metadata field from a task row."""
    match = re.search(rf"\|\s*{name}\s*:\s*([^|]+)", body, re.IGNORECASE)
    return match.group(1).strip() if match else None


def remove_field(body: str, name: str) -> str:
    """Remove one pipe-delimited metadata field from a task row."""
    return re.sub(rf"\s*\|\s*{name}\s*:\s*[^|]*", "", body, flags=re.IGNORECASE).rstrip()


def add_field(body: str, name: str, value: str) -> str:
    """Append one canonical metadata field to a task row."""
    return f"{body.rstrip()} | {name}: {value}"


def normalize() -> tuple[int, int, int]:
    """Normalize the configured task ledger and report changed row counts."""
    lines = TASKS.read_text().splitlines()
    seen: dict[str, int] = {}
    changed = reopened = renamed = 0
    output: list[str] = []

    for line in lines:
        match = ITEM.match(line)
        if not match:
            output.append(line)
            continue

        body = match.group("body").strip()
        checked = match.group("mark").lower() == "x"
        original_body = body
        item_id_match = re.match(r"(\S+)(.*)$", body)
        if item_id_match:
            item_id, remainder = item_id_match.groups()
            count = seen.get(item_id, 0) + 1
            seen[item_id] = count
            if count > 1:
                item_id = f"{item_id}-legacy-{count}"
                body = item_id + remainder
                renamed += 1

        priority = (field(body, "priority") or "medium").lower()
        if priority == "critical":
            priority = "high"
        if priority not in VALID_PRIORITIES:
            priority = "medium"
        body = remove_field(body, "priority")
        body = add_field(body, "priority", priority)

        effort = (field(body, "effort") or "M").lower()
        effort = EFFORT_MAP.get(effort, "M")
        body = remove_field(body, "effort")
        body = add_field(body, "effort", effort)

        status = (field(body, "status") or ("completed" if checked else "pending")).lower()
        if status == "done":
            status = "completed"
        if status not in VALID_STATUSES:
            status = "completed" if checked else "pending"

        evidence = field(body, "evidence")
        evidence_has_incomplete_marker = bool(
            evidence
            and any(
                re.search(r"(?<![-])\b" + word + r"\b", evidence, re.IGNORECASE)
                for word in TASK_TICK_FORBIDDEN_WORDS
            )
        )
        invalid_completion = checked and (
            not evidence or bool(WAVE_EVIDENCE.match(evidence)) or evidence_has_incomplete_marker
        )
        if invalid_completion:
            # Keep the original claim visible, but do not represent it as done.
            checked = False
            status = "pending"
            reopened += 1

        body = remove_field(body, "status")
        body = add_field(body, "status", status)

        if invalid_completion and evidence:
            body = remove_field(body, "evidence")
            body = add_field(body, "prior-evidence", evidence)
        elif invalid_completion and not evidence:
            body = remove_field(body, "evidence")

        if body != original_body or checked != (match.group("mark").lower() == "x"):
            changed += 1
        mark = "x" if checked else " "
        output.append(f"{match.group('prefix')}{mark}{match.group('close')}{body}")

    TASKS.write_text("\n".join(output) + "\n")
    return changed, reopened, renamed


if __name__ == "__main__":
    changed, reopened, renamed = normalize()
    print(f"normalized={changed} reopened={reopened} duplicate_ids_renamed={renamed}")
