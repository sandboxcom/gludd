#!/usr/bin/env python3
"""
check_duplicate_targets.py

Scans the Makefile for duplicate target declarations at column 0.
A target declared more than once (common when two branches independently add
the same target) is a hard error — it causes merge conflicts and silent
makefile breakage.

Usage:
    python3 scripts/check_duplicate_targets.py [MAKEFILE]

Exit codes:
    0   Clean — no duplicate targets found.
    1   Duplicate targets detected — see stderr details.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

TARGET_PATTERN = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_.-]*):")
TARGET_VAR_ASSIGN_PATTERN = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_.-]*:\s*[A-Za-z_][A-Za-z0-9_.-]*\s*(\?=|:=|\+=|=)"
)


def extract_targets(makefile_path: Path) -> Counter[str]:
    targets: Counter[str] = Counter()
    for line in makefile_path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("."):
            continue
        if TARGET_VAR_ASSIGN_PATTERN.match(stripped):
            continue
        m = TARGET_PATTERN.match(stripped)
        if m:
            targets[m.group(1)] += 1
    return targets


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    makefile_path = repo_root / "Makefile"
    if len(sys.argv) > 1:
        makefile_path = Path(sys.argv[1])
    if not makefile_path.exists():
        print(
            f"check-duplicate-targets: Makefile not found at {makefile_path}",
            file=sys.stderr,
        )
        return 1

    targets = extract_targets(makefile_path)
    duplicates = {t: c for t, c in targets.items() if c > 1}

    if duplicates:
        print(
            "check-duplicate-targets: DUPLICATE TARGETS detected — "
            "each target must be declared exactly once. "
            "If two branches independently added the same target, "
            "land it on development first, then merge to master.",
            file=sys.stderr,
        )
        for t, c in sorted(duplicates.items()):
            print(f"  {t}: declared {c} times", file=sys.stderr)
        return 1

    print(
        f"check-duplicate-targets: OK — {len(targets)} targets, "
        f"0 duplicates"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
