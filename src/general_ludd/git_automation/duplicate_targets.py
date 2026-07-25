"""Makefile duplicate target scanner.

Parses a Makefile for target declarations matching ``^[a-zA-Z_-]+:`` at
column 0 and reports any target declared more than once.

Ported from ``scripts/check_duplicate_targets.py`` into the git_automation
collection so the check is callable from ansible roles and the daemon.
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

SKIP_PREFIXES = (".", "#")


class DuplicateTarget:
    __slots__ = ("count", "lines", "target")

    def __init__(self, target: str, count: int, lines: list[int]) -> None:
        self.target = target
        self.count = count
        self.lines = lines

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DuplicateTarget):
            return NotImplemented
        return (
            self.target == other.target
            and self.count == other.count
            and self.lines == other.lines
        )

    def __repr__(self) -> str:
        return (
            f"DuplicateTarget(target={self.target!r}, "
            f"count={self.count}, lines={self.lines!r})"
        )


def extract_targets(makefile_path: Path) -> list[tuple[str, int]]:
    """Return every Makefile target and its 1-indexed line number.

    Skips commented lines (``#``), special targets (``.PHONY`` et al.),
    and variable assignments (``VAR := val``).
    """
    targets: list[tuple[str, int]] = []
    if not makefile_path.exists():
        return targets

    for lineno, line in enumerate(
        makefile_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.lstrip()
        if not stripped:
            continue
        if stripped.startswith(SKIP_PREFIXES):
            continue
        if TARGET_VAR_ASSIGN_PATTERN.match(stripped):
            continue
        m = TARGET_PATTERN.match(stripped)
        if m:
            targets.append((m.group(1), lineno))
    return targets


def check_duplicate_targets(makefile_path: Path) -> list[DuplicateTarget]:
    """Return every target declared more than once, with line numbers."""
    all_targets = extract_targets(makefile_path)
    counter: Counter[str] = Counter()

    target_lines: dict[str, list[int]] = {}
    for name, lineno in all_targets:
        counter[name] += 1
        target_lines.setdefault(name, []).append(lineno)

    return [
        DuplicateTarget(target=name, count=cnt, lines=target_lines[name])
        for name, cnt in counter.items()
        if cnt > 1
    ]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    makefile_path = repo_root / "Makefile"
    if len(sys.argv) > 1:
        makefile_path = Path(sys.argv[1])
    if not makefile_path.exists():
        print(
            f"check-duplicate-targets: Makefile not found at {makefile_path}",
            file=sys.stderr,
        )
        return 1

    duplicates = check_duplicate_targets(makefile_path)
    if duplicates:
        print(
            "check-duplicate-targets: DUPLICATE TARGETS detected — "
            "each target must be declared exactly once. "
            "If two branches independently added the same target, "
            "land it on development first, then merge to master.",
            file=sys.stderr,
        )
        for d in sorted(duplicates, key=lambda d: d.target):
            print(f"  {d.target}: declared {d.count} times", file=sys.stderr)
        return 1

    all_targets = extract_targets(makefile_path)
    print(
        f"check-duplicate-targets: OK — {len(all_targets)} targets, "
        f"0 duplicates"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
