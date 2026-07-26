#!/usr/bin/env python3
"""Convert bare test env writes to pytest's monkeypatch fixture."""
from __future__ import annotations

import re
from pathlib import Path

ROOTS = (Path("tests/e2e"),)
WRITE = re.compile(r'^(?P<indent>\s*)os\.environ\[(?P<quote>[\'\"])(?P<key>[^\'\"]+)(?P=quote)\]\s*=\s*(?P<value>.+)$')
DEF = re.compile(r'^(?P<indent>\s*)def\s+(?P<name>test_[^(]+)\((?P<args>[^)]*)\):')


def migrate(path: Path) -> int:
    lines = path.read_text().splitlines(keepends=True)
    current_def: int | None = None
    changed = 0
    for i, line in enumerate(lines):
        match = DEF.match(line)
        if match:
            current_def = i
            if "monkeypatch" not in match.group("args"):
                args = match.group("args").strip()
                lines[i] = f"{match.group('indent')}def {match.group('name')}(" + (
                    f"{args}, monkeypatch" if args else "monkeypatch"
                ) + "):\n"
        write = WRITE.match(line)
        if write:
            if current_def is None:
                raise RuntimeError(f"environment write outside test function: {path}:{i + 1}")
            lines[i] = (
                f"{write.group('indent')}monkeypatch.setenv({write.group('quote')}{write.group('key')}"
                f"{write.group('quote')}, {write.group('value')})\n"
            )
            changed += 1
    if changed:
        path.write_text("".join(lines))
    return changed


def main() -> int:
    total = sum(migrate(path) for root in ROOTS for path in sorted(root.rglob("test_*.py")))
    print(f"migrated {total} environment writes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
