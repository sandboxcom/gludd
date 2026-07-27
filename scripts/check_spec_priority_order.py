"""check_spec_priority_order.py — AB014 enforcement.

Enforces P0/P1 spec priority: blocks commits where lower-priority specs
(P3/P4) outnumber unwritten P0/P1 specs. Extended from check_spec_priority.py.

Exit 0 if priority order is correct; exit 1 if low-priority inflation detected.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"

SPEC_RE = re.compile(r"^### (A[AB]\d{3}) — (.+)$", re.MULTILINE)


def parse_specs(text: str) -> list[tuple[str, str, str]]:
    specs = []
    for m in SPEC_RE.finditer(text):
        spec_id = m.group(1)
        title = m.group(2)
        start = m.end()
        next_m = SPEC_RE.search(text, start)
        end = next_m.start() if next_m else len(text)
        body = text[start:end].strip()
        specs.append((spec_id, title, body))
    return specs


def classify_spec(spec_id: str, body: str) -> int:
    """P0=CI failure, P1=release blockage, P2=frustration, P3=quality, P4=aspirational."""
    blob = (spec_id + " " + body).lower()

    if any(
        w in blob
        for w in [
            "ci failure",
            "ci red",
            "ci is blocked",
            "test failure",
            "compulsive ci",
            "ci-check-freq",
            "push cancels ci",
        ]
    ):
        return 0
    if any(
        w in blob
        for w in [
            "release",
            "deploy",
            "artifact",
            "ship",
            "beta.",
            "error code",
            "merge conflict",
            "merge abort",
            "merge recovery",
        ]
    ):
        return 1
    if any(
        w in blob
        for w in [
            "frustration",
            "user frustrat",
            "ignored",
            "repeated",
            "delayed reaction",
            "user correct",
        ]
    ):
        return 2
    if any(
        w in blob
        for w in [
            "gate",
            "quality",
            "test integrity",
            "lint",
            "typecheck",
            "dead code",
            "coverage",
            "dedup",
            "priority",
        ]
    ):
        return 3
    return 4


def main() -> int:
    if not SPECS_FILE.exists():
        return 0

    text = SPECS_FILE.read_text(encoding="utf-8")
    specs = parse_specs(text)

    counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    for spec_id, _title, body in specs:
        counts[classify_spec(spec_id, body)] += 1

    p01_count = counts[0] + counts[1]
    p34_count = counts[3] + counts[4]

    if p34_count > p01_count and p01_count < 10:
        print(
            f"AB014 SPEC-PRIORITY-ORDER: {p34_count} P3/P4 specs vs {p01_count} P0/P1 specs. "
            f"Write P0/P1 specs before aspirational P3/P4 specs."
        )
        return 1

    print(f"AB014: P0={counts[0]} P1={counts[1]} P2={counts[2]} P3={counts[3]} P4={counts[4]}. Priority order PASS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
