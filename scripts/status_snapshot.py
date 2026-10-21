"""Rewrite the <!-- gate:begin -->..<!-- gate:end --> block in SESSION.md.

Reads .gate-status, formats it, and replaces the block in-place.
Stdlib only — no project imports.
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_MD = REPO_ROOT / "SESSION.md"
GATE_STATUS = REPO_ROOT / ".gate-status"
BEGIN_MARKER = "<!-- gate:begin -->"
END_MARKER = "<!-- gate:end -->"


def read_gate_status() -> list[str]:
    """Read the actual retained gate status without its control lines."""
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
    """Format the retained observation inside one dated native marker pair."""
    date_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    header = f"## Current Gate Status ({date_str})"
    gate_lines = read_gate_status()
    parts = [header, BEGIN_MARKER, *gate_lines, "", END_MARKER]
    return "\n".join(parts)


def render_session() -> str:
    """Validate the handoff structure and render exactly one native gate block."""
    if not SESSION_MD.exists():
        print("SESSION.md not found", file=sys.stderr)
        sys.exit(1)
    content = SESSION_MD.read_text()
    begin_idx = content.find(BEGIN_MARKER)
    end_idx = content.find(END_MARKER)
    marker_lines = content.splitlines()
    if (content.count(BEGIN_MARKER) != 1 or content.count(END_MARKER) != 1
            or end_idx < begin_idx
            or BEGIN_MARKER not in marker_lines or END_MARKER not in marker_lines):
        print("Markers not found in SESSION.md or duplicated/out of order/inline", file=sys.stderr)
        sys.exit(1)
    header_start = content.rfind("\n", 0, begin_idx) + 1
    if header_start:
        previous_start = content.rfind("\n", 0, header_start - 1) + 1
        previous_line = content[previous_start:header_start].rstrip("\r\n")
        if re.fullmatch(r"## Current Gate Status(?: \(\d{4}-\d{2}-\d{2}\))?", previous_line):
            header_start = previous_start
    new_block = build_block()
    return content[:header_start] + new_block + "\n" + content[end_idx + len(END_MARKER) + 1:]


def rewrite_session() -> None:
    """Write the validated gate region while preserving the surrounding handoff."""
    SESSION_MD.write_text(render_session())
    print(f"Updated SESSION.md gate block ({len(read_gate_status())} lines)")


def main(argv: list[str] | None = None) -> int:
    """Validate or rewrite the tracked session gate-status block."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="build the gate block without modifying SESSION.md",
    )
    args = parser.parse_args(argv)
    if args.validate_only:
        render_session()
        print("status-snapshot validation: PASS")
        return 0
    rewrite_session()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
