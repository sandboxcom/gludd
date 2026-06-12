"""Rewrite the <!-- gate:begin -->..<!-- gate:end --> block in SESSION.md.

Reads .gate-status, formats it, and replaces the block in-place.
Stdlib only — no project imports.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_MD = REPO_ROOT / "SESSION.md"
GATE_STATUS = REPO_ROOT / ".gate-status"
BEGIN_MARKER = "<!-- gate:begin -->"
END_MARKER = "<!-- gate:end -->"


def read_gate_status() -> list[str]:
    if not GATE_STATUS.exists():
        return ["- No .gate-status file. Run 'make gate' first."]
    lines: list[str] = []
    for line in GATE_STATUS.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("===") or stripped.startswith("---") or not stripped:
            continue
        if stripped.startswith("epoch"):
            continue
        lines.append(f"- {stripped}")
    return lines


def build_block() -> str:
    date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    header = f"## Current Gate Status ({date_str})"
    gate_lines = read_gate_status()
    parts = [header, BEGIN_MARKER, *gate_lines, "", END_MARKER]
    return "\n".join(parts)


def rewrite_session() -> None:
    if not SESSION_MD.exists():
        print("SESSION.md not found", file=sys.stderr)
        sys.exit(1)
    content = SESSION_MD.read_text()
    begin_idx = content.find(BEGIN_MARKER)
    end_idx = content.find(END_MARKER)
    if begin_idx == -1 or end_idx == -1:
        print(f"Markers not found in SESSION.md", file=sys.stderr)
        sys.exit(1)
    header_end = content.rfind("\n", 0, begin_idx)
    if header_end == -1:
        header_end = 0
    else:
        header_end += 1
    new_block = build_block()
    new_content = content[:header_end] + new_block + "\n" + content[end_idx + len(END_MARKER) + 1:]
    SESSION_MD.write_text(new_content)
    print(f"Updated SESSION.md gate block ({len(gate_lines := read_gate_status())} lines)")


if __name__ == "__main__":
    rewrite_session()
