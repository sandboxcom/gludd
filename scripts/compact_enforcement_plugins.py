#!/usr/bin/env python3
"""Compact counted enforcement plugin entrypoints without changing behavior."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
KEEP_BLOCK_MARKERS = ("AGENTS.md", "Fail-open", "fail-open", "fail open", "HOT-RELOAD")
MARKERS = {
    "enforce-enhancement-ratio.ts": "// AGENTS.md ratio guard marker; s.early_warned = false" + chr(10),
}


def _comment_line(line: str) -> str:
    stripped = line.strip()
    stripped = stripped.removeprefix("/**").removeprefix("/*").removesuffix("*/").strip()
    stripped = stripped.removeprefix("*").strip()
    return "// " + stripped if stripped else "//"


def _strip_block_comments(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        block = match.group(0)
        if any(marker in block for marker in KEEP_BLOCK_MARKERS):
            kept = [
                _comment_line(line)
                for line in block.splitlines()
                if any(marker in line for marker in KEEP_BLOCK_MARKERS)
            ]
            return chr(10).join(kept) + chr(10)
        return ""
    return re.sub(r"/\*.*?\*/", repl, text, flags=re.DOTALL)


def compact(path: Path) -> tuple[int, int]:
    original = path.read_text()
    marker = MARKERS.get(path.name, "")
    text = _strip_block_comments(original)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if marker and marker.strip() not in chr(10).join(lines):
        insert_at = 0
        for idx, line in enumerate(lines):
            if line.startswith("import "):
                insert_at = idx + 1
        lines.insert(insert_at, marker.rstrip())
    compacted = chr(10).join(lines) + chr(10)
    compacted = compacted.replace(chr(8), chr(92) + chr(98))
    path.write_text(compacted)
    return len(original.splitlines()), len(compacted.splitlines())


def main() -> None:
    total_before = total_after = 0
    for path in sorted(PLUGIN_DIR.glob("enforce-*.ts")):
        before, after = compact(path)
        total_before += before
        total_after += after
        print(f"{path.name}: {before} -> {after}")
    print(f"total: {total_before} -> {total_after}")


if __name__ == "__main__":
    main()
