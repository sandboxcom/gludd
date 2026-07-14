#!/usr/bin/env python3
"""
auto_update_task_ledger.py

Cross-references recent git commits against TASKS.md:
  - Finds unchecked items (`- [ ] `) with recognizable task IDs.
  - Searches `make git-log` output for commit messages referencing those IDs.
  - Auto-marks matching items as `[x]` completed with commit hash as evidence.

Usage:
    python3 scripts/auto_update_task_ledger.py [--dry-run]

Exit codes:
    0   Changes applied (or dry-run completed).
    1   Error (missing file, etc.).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ID_PATTERN = re.compile(r"\b([A-Z]{1,3}\d*(?:\.\d+(?:\.\d+)*|-\d+))\b")
COMMIT_LINE_RE = re.compile(
    r"^([a-f0-9]{8})\s+(.*)$"
)


def parse_git_log() -> list[tuple[str, str]]:
    """Return [(sha8, message), ...] from `git log --oneline -50`."""
    result = subprocess.run(
        ["git", "log", "--oneline", "-50"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    if result.returncode != 0:
        print(f"auto-update-ledger: git log failed: {result.stderr.strip()}",
              file=sys.stderr)
        return []

    entries: list[tuple[str, str]] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        m = COMMIT_LINE_RE.match(line)
        if m:
            entries.append((m.group(1), m.group(2)))
    return entries


def commit_references_id(commits: list[tuple[str, str]], task_id: str) -> tuple[str, str] | None:
    """Return (sha8, commit_message_subject) if any commit message contains task_id."""
    num = task_id.split("-")[-1] if "-" in task_id else task_id
    for sha, msg in commits:
        if task_id in msg or num in msg:
            return sha, msg
    return None


def build_new_line(line: str, sha: str) -> str:
    """Transform an unchecked line into a checked line with commit evidence."""
    line = re.sub(r"^- \[ \]", "- [x]", line, count=1)
    if "| status:" in line:
        line = re.sub(r"\| status:\s*\S+", f"| status: completed | evidence: {sha}", line)
    elif "| evidence:" in line:
        line = re.sub(r"\| evidence:\s*\S+", f"| evidence: {sha}", line)
    else:
        if line.rstrip().endswith("|"):
            line = line.rstrip()[:-1].rstrip() + f" | evidence: {sha}"
        else:
            line = line.rstrip() + f" | evidence: {sha}"
    return line


def auto_update(tasks_path: Path, dry_run: bool = False) -> int:
    text = tasks_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    commits = parse_git_log()
    if not commits:
        print("auto-update-ledger: no commits found — nothing to do")
        return 0

    changes = 0
    new_lines: list[str] = []

    # Use the ID_PATTERN from validate_task_ledger for consistency
    local_id_re = ID_PATTERN

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- [ ]"):
            new_lines.append(line)
            continue

        ids = local_id_re.findall(stripped)
        if not ids:
            new_lines.append(line)
            continue

        matched = None
        for tid in ids:
            match = commit_references_id(commits, tid)
            if match:
                matched = match
                break

        if matched:
            sha, _msg = matched
            new_line = build_new_line(line.rstrip("\n"), sha) + "\n"
            new_lines.append(new_line)
            changes += 1
            print(f"  [auto-complete] {ids[0]} -> {sha}")
        else:
            new_lines.append(line)

    if changes == 0:
        print("auto-update-ledger: no changes — all unchecked items remain")
        return 0

    if dry_run:
        print(f"auto-update-ledger: dry-run — {changes} item(s) would be marked complete")
        return 0

    new_text = "".join(new_lines)
    tasks_path.write_text(new_text, encoding="utf-8")
    print(f"auto-update-ledger: {changes} item(s) auto-marked complete")
    return 0


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    repo_root = Path(__file__).resolve().parent.parent
    tasks_path = repo_root / "TASKS.md"

    if not tasks_path.exists():
        print(f"ERROR: TASKS.md not found at {tasks_path}", file=sys.stderr)
        return 1

    return auto_update(tasks_path, dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main())
