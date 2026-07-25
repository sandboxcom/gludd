#!/usr/bin/env python3
"""
check_coverage_claim.py — block commits whose message claims finality
("final"/"complete"/"done" near "e2e"/"coverage"/"test") when actual
coverage is below the 85% target.

Usage:
    python3 scripts/check_coverage_claim.py            # check HEAD commit msg
    python3 scripts/check_coverage_claim.py --msg '...' # check explicit message
    python3 scripts/check_coverage_claim.py --coverage 0.68  # override coverage

Exit codes:
    0  No false claim detected (or no claim at all).
    1  False completion claim detected — coverage below 85%.
    2  Coverage data unavailable (fail-open).
"""
from __future__ import annotations

import re
import subprocess
import sys

COVERAGE_TARGET = 0.85
FINALITY_RE = re.compile(
    r"\b(?:final|complete|finished|done)\s+(?:e2e|coverage|test)\s+(?:push|expansion|wave)\b",
    re.IGNORECASE,
)
COVERAGE_RE = re.compile(
    r"(?:coverage|at)\s*(?:is\s*)?(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)


def _git_log_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "log", "-1", "--format=%s"],
            text=True,
        ).strip()
    except Exception:
        return ""


def _coverage_from_pytest() -> float | None:
    try:
        out = subprocess.check_output(
            ["make", "coverage-pct"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", out)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", out)
    if m:
        return float(m.group(1)) / 100
    return None


def _check(msg: str, coverage_pct: float | None) -> int:
    if not FINALITY_RE.search(msg):
        return 0
    cov_from_msg: float | None = None
    cm = COVERAGE_RE.search(msg)
    if cm:
        cov_from_msg = float(cm.group(1)) / 100

    effective = cov_from_msg if cov_from_msg is not None else coverage_pct
    if effective is None:
        print(
            "WARNING: message claims completion but coverage is unknown "
            "(fail-open: allowing).", file=sys.stderr,
        )
        return 2

    if effective >= COVERAGE_TARGET:
        return 0

    print(
        f"BLOCKED: commit message claims completion "
        f"('{msg[:80]}...') but coverage is {effective:.0%} "
        f"(target: {COVERAGE_TARGET:.0%}). "
        f"Fix coverage or remove the finality claim.",
    )
    return 1


def main(argv: list[str]) -> int:
    msg: str | None = None
    coverage_pct: float | None = None

    args = list(argv)
    i = 1
    while i < len(args):
        if args[i] == "--msg" and i + 1 < len(args):
            msg = args[i + 1]
            i += 2
        elif args[i].startswith("--msg="):
            msg = args[i].split("=", 1)[1]
            i += 1
        elif args[i] == "--coverage" and i + 1 < len(args):
            try:
                coverage_pct = float(args[i + 1])
            except ValueError:
                print(f"ERROR: invalid coverage value: {args[i + 1]}", file=sys.stderr)
                return 2
            i += 2
        elif args[i].startswith("--coverage="):
            try:
                coverage_pct = float(args[i].split("=", 1)[1])
            except ValueError:
                print(f"ERROR: invalid coverage value: {args[i]}", file=sys.stderr)
                return 2
            i += 1
        else:
            i += 1

    if msg is None:
        msg = _git_log_head()
    if not msg:
        return 0

    if coverage_pct is None:
        coverage_pct = _coverage_from_pytest()

    return _check(msg, coverage_pct)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
