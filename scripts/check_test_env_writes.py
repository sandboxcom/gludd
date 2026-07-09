#!/usr/bin/env python3
"""Forbid bare ``os.environ[...] =`` writes in test files.

Bare env writes leak across tests on the same xdist worker and bypass
``monkeypatch`` auto-restore. The canonical pattern is
``monkeypatch.setenv("X", "v")`` (auto-restored at fixture teardown) backed by
the ``_restore_leaky_env_vars`` autouse backstop in ``tests/conftest.py``.

Exits non-zero with a listing of any violations. ``ALLOWED_VIOLATIONS`` is
intentionally EMPTY — all prior violations were converted to
``monkeypatch.setenv`` in the 2026-07-08 P3 audit. Do NOT add entries here to
silence a new violation; convert it to ``monkeypatch.setenv`` instead (see
AGENTS.md "Guardrail Integrity Policy").
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Matches `os.environ["X"] = v`, `os.environ[VAR] = v`, etc. (bare writes only).
# Deliberately does NOT match reads (os.environ.get / os.environ["X"] as rvalue),
# pops (os.environ.pop), dels (del os.environ[...]), or monkeypatch.setenv.
_BARE_WRITE_RE = re.compile(
    r'^[^#\n]*\bos\.environ\[\s*(?:\w+|\'[^\']*\'|"[^"]*")\s*\]\s*=(?!=)'
)

ALLOWED_VIOLATIONS: frozenset[str] = frozenset()


def scan_file(path: Path) -> list[str]:
    violations: list[str] = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if _BARE_WRITE_RE.match(line):
            key = f"{path}:{lineno}"
            if key not in ALLOWED_VIOLATIONS:
                violations.append(f"{key}: {line.strip()}")
    return violations


def main(argv: list[str]) -> int:
    roots = argv[1:] or ["tests"]
    all_violations: list[str] = []
    for root in roots:
        root_path = Path(root)
        if root_path.is_file() and root_path.suffix == ".py":
            all_violations.extend(scan_file(root_path))
        else:
            for py in sorted(root_path.rglob("test_*.py")):
                all_violations.extend(scan_file(py))
    if all_violations:
        print("BARE os.environ[...] = writes forbidden in tests/ (use monkeypatch.setenv):")
        for v in all_violations:
            print(f"  {v}")
        print(f"\n{len(all_violations)} violation(s) found.")
        return 1
    print("OK: no bare os.environ writes in tests/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
