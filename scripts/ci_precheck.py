#!/usr/bin/env python3
"""Pretend out CI locally: run the exact same checks CI runs before every push.

Checks (in CI `gate` job order, without smoke):
  1. make lint             — ruff check src tests
  2. make typecheck        — mypy -p general_ludd
  3. make test-count       — verify 0 collection errors
  4. make check-node-v26-compat — verify plugin compatibility
  5. make check-readme-status   — verify README is current

Exit 0 only if ALL pass.  Otherwise exit 1 with a summary of failures.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CHECKS: list[tuple[str, list[str]]] = [
    ("lint", ["make", "lint"]),
    ("typecheck", ["make", "typecheck"]),
    ("test-count", ["make", "test-count"]),
    ("check-node-v26-compat", ["make", "check-node-v26-compat"]),
    ("check-readme-status", ["make", "check-readme-status"]),
]


def _red(text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[91m{text}\033[0m"
    return text


def _green(text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[92m{text}\033[0m"
    return text


def run_one(name: str, cmd: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    ok = result.returncode == 0
    detail = result.stdout.strip() + "\n" + result.stderr.strip()
    return ok, detail.strip()


def main() -> int:
    failures: list[str] = []

    print("=== CI precheck ===\n")
    for name, cmd in CHECKS:
        print(f"--- {name} ({' '.join(cmd)}) ---")
        ok, detail = run_one(name, cmd)
        if detail:
            print(detail)
        if ok:
            print(_green(f"  PASS: {name}\n"))
        else:
            print(_red(f"  FAIL: {name}\n"))
            failures.append(name)

    if failures:
        print(_red("=== FAILURES ==="))
        for f in failures:
            print(_red(f"  {f}"))
        return 1

    print(_green("=== ALL PASSED ==="))
    return 0


if __name__ == "__main__":
    sys.exit(main())
