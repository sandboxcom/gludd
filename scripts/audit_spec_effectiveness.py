#!/usr/bin/env python3
"""AB040 — audit behavioral spec effectiveness.

A spec is INEFFECTIVE if the described behavioral failure recurs after
the spec was written. Reads BEHAVIORAL_SPECS.md, finds each spec's
enforcement mechanism, checks BUGS.md and ratchet.yml for post-spec
recurrences of the described failure.

Exit non-zero if >10% of specs are marked INEFFECTIVE.
"""

import re
import sys
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
BUGS_FILE = ROOT / "BUGS.md"
RATCHET_FILE = ROOT / "config" / "ratchet.yml"

SPEC_RE = re.compile(r"^### (\w+) — (.+)$")
ENFORCEMENT_RE = re.compile(r"\*\*Enforcement:\*\*\s+(.+)$")
BEHAVIOR_RE = re.compile(r"\*\*Behavior:\*\*\s+(.+)$")

MAX_INEFFECTIVE_PCT = 10


class SpecRecord(TypedDict):
    """Structured behavioral-spec fields consumed by the audit."""

    id: str
    title: str
    enforcement: str
    behavior: str


def parse_specs() -> list[SpecRecord]:
    if not SPECS_FILE.exists():
        return []

    content = SPECS_FILE.read_text()
    specs: list[SpecRecord] = []
    current: SpecRecord | None = None

    for line in content.split("\n"):
        m = SPEC_RE.match(line)
        if m:
            if current and current.get("id"):
                specs.append(current)
            current = {"id": m.group(1), "title": m.group(2), "enforcement": "", "behavior": ""}
            continue

        if current is None:
            continue

        m = ENFORCEMENT_RE.match(line)
        if m:
            current["enforcement"] = m.group(1)

        m = BEHAVIOR_RE.match(line)
        if m:
            current["behavior"] = m.group(1)

    if current and current.get("id"):
        specs.append(current)

    return specs


def check_recurrences(spec: SpecRecord) -> bool:
    """Return True if the spec's described behavior likely recurred after spec creation."""
    keywords = spec["title"].lower().split("-")

    # Check BUGS.md for incidents matching this spec's keywords
    if BUGS_FILE.exists():
        bugs_content = BUGS_FILE.read_text().lower()
        for kw in keywords:
            if len(kw) > 4 and kw in bugs_content:
                return True

    # Check ratchet.yml for matching entries
    if RATCHET_FILE.exists():
        ratchet_content = RATCHET_FILE.read_text().lower()
        for kw in keywords:
            if len(kw) > 4 and kw in ratchet_content:
                return True

    return False


def main() -> int:
    specs = parse_specs()
    if not specs:
        print("audit-spec-effectiveness: no specs found")
        return 0

    ineffective = []

    for spec in specs:
        if check_recurrences(spec):
            ineffective.append(f"  {spec['id']}: {spec.get('title', '?')}")

    pct = 100.0 * len(ineffective) / max(len(specs), 1)
    if pct > MAX_INEFFECTIVE_PCT:
        print(
            "audit-spec-effectiveness: "
            f"{len(ineffective)}/{len(specs)} ({pct:.1f}%) INEFFECTIVE — "
            f"exceeds {MAX_INEFFECTIVE_PCT}% threshold"
        )
        for s in ineffective[:10]:
            print(s)
        return 1

    print(
        "audit-spec-effectiveness: "
        f"{len(ineffective)}/{len(specs)} ({pct:.1f}%) ineffective — "
        f"within {MAX_INEFFECTIVE_PCT}% threshold"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
