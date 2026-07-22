#!/usr/bin/env python3
"""Extract contextual lines from a local CI shard log."""

from __future__ import annotations

import argparse
from pathlib import Path


def _allowed_log_path(path: Path) -> bool:
    parts = path.parts
    if ".." in parts:
        return False
    return ".gate-logs" in parts


def extract_context(
    log_path: Path,
    pattern: str,
    *,
    before: int = 20,
    after: int = 80,
    max_matches: int = 5,
) -> list[str]:
    """Return context blocks around lines containing pattern."""

    if not pattern:
        raise ValueError("pattern is required")
    if before < 0 or after < 0 or max_matches < 1:
        raise ValueError("before/after must be >= 0 and max_matches must be >= 1")
    if not _allowed_log_path(log_path):
        raise ValueError(f"refusing log path outside workspace gate logs: {log_path}")
    if not log_path.exists():
        raise FileNotFoundError(log_path)

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    matches = [idx for idx, line in enumerate(lines) if pattern in line]
    blocks: list[str] = []
    for match_number, idx in enumerate(matches[:max_matches], start=1):
        start = max(0, idx - before)
        end = min(len(lines), idx + after + 1)
        blocks.append(f"--- match {match_number}/{len(matches)} line {idx + 1} ---")
        for line_no in range(start, end):
            marker = ">" if line_no == idx else " "
            blocks.append(f"{marker}{line_no + 1}: {lines[line_no]}")
    if not blocks:
        blocks.append(f"No matches for pattern: {pattern}")
    return blocks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--before", type=int, default=20)
    parser.add_argument("--after", type=int, default=80)
    parser.add_argument("--max-matches", type=int, default=5)
    args = parser.parse_args()

    try:
        lines = extract_context(
            args.log,
            args.pattern,
            before=args.before,
            after=args.after,
            max_matches=args.max_matches,
        )
    except Exception as exc:
        print(f"CI-SHARDS-LOG-CONTEXT error: {exc}")
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
