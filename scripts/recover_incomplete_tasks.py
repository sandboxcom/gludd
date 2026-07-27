#!/usr/bin/env python3
"""AB039 — recover incomplete tasks from prior sessions.

Reads the prior SESSION.md (from git log), compares unchecked TASKS.md items
against current TASKS.md. Items absent (dropped) or still unchecked (abandoned)
from prior session are reported. >3 unrecovered items exits non-zero.

Uses git to find the prior SESSION.md content, then compares task items.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_UNRECOVERED = 3

UNCHECKED_RE = re.compile(r"^\s*- \[ \].*")
TASK_ID_RE = re.compile(r"\b([A-Z]+-\d+|[A-Z]+\d+)\b")


def get_prior_session_tasks() -> set[str]:
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--diff-filter=M", "-1", "SESSION.md"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=10,
        )
        if not result.stdout.strip():
            return set()

        result = subprocess.run(
            ["git", "show", f"HEAD~1:SESSION.md"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=10,
        )
        if result.returncode != 0:
            return set()

        tasks: set[str] = set()
        for line in result.stdout.split("\n"):
            if UNCHECKED_RE.match(line):
                tasks.add(line.strip())
        return tasks
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return set()


def get_current_tasks() -> set[str]:
    tasks_md = ROOT / "TASKS.md"
    if not tasks_md.exists():
        return set()
    tasks: set[str] = set()
    for line in tasks_md.read_text().split("\n"):
        if UNCHECKED_RE.match(line):
            tasks.add(line.strip())
    return tasks


def main() -> int:
    prior = get_prior_session_tasks()
    current = get_current_tasks()

    if not prior:
        print("recover-incomplete-tasks: no prior session tasks found for comparison")
        return 0

    dropped = prior - current
    abandoned = prior & current

    if dropped:
        print(f"recover-incomplete-tasks: {len(dropped)} dropped task(s) from prior session:")
        for d in sorted(dropped):
            print(f"  DROPPED: {d[:100]}")
    if abandoned:
        print(f"recover-incomplete-tasks: {len(abandoned)} abandoned task(s) still unchecked:")
        for a in sorted(abandoned):
            print(f"  ABANDONED: {a[:100]}")

    total = len(dropped) + len(abandoned)
    if total > MAX_UNRECOVERED:
        return 1

    print(f"recover-incomplete-tasks: {total} unrecovered tasks — within threshold of {MAX_UNRECOVERED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
