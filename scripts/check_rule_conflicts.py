"""check_rule_conflicts.py — AA089 enforcement.

Scan AGENTS.md for contradictory rules (e.g. "never push while CI running" vs
"push after every fix"). Exit 0 on clean; exit 1 on potential conflicts.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS_FILE = ROOT / "AGENTS.md"


PATTERNS = [
    (r"never\s+push\s+while", r"push\s+(?:after|every|immediately)"),
    (r"commit\s+green|commit\s+after\s+test", r"commit\s+immediately"),
    (r"do not\s+wait|never\s+wait", r"wait\s+for\s+ci"),
    (r"max\s+10\s+subagents?", r"dispatch\s+more\s+than\s+10"),
    (r"never\s+rebase\s+shared", r"rebase\s+(?:master|development|main)"),
    (r"do not\s+push\s+feature", r"push\s+to\s+master"),
]


def main() -> int:
    if not AGENTS_FILE.exists():
        print(f"ERROR: {AGENTS_FILE} not found")
        return 1
    text = AGENTS_FILE.read_text().lower()
    conflicts: list[tuple[str, str]] = []
    for a, b in PATTERNS:
        has_a = bool(re.search(a, text))
        has_b = bool(re.search(b, text))
        if has_a and has_b:
            conflicts.append((a, b))
    if not conflicts:
        print("No rule conflicts detected.")
        return 0
    print(f"{len(conflicts)} potential rule conflict(s) detected:")
    for a, b in conflicts:
        print(f"  RULE A ({a}) vs RULE B ({b})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
