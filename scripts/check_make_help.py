#!/usr/bin/env python3
"""Verify public Makefile targets are listed in ``make help`` output."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TARGET_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):(?:\s|$)")
HELP_TARGET_RE = re.compile(r'^\s*@echo\s+"  ([A-Za-z0-9][A-Za-z0-9_.-]*)\s+')
INTERNAL_PREFIXES = ("_", ".")
INTERNAL_TARGETS = {
    "commit-bootstrap",
    "fix-benchmark-mock",
    "merge-spec-groups",
    "patch-test",
    "sdd-audit",
    "sdd-constitution",
    "sdd-critic",
    "sdd-discover",
    "sdd-harvest",
    "sdd-implement",
    "sdd-plan",
    "sdd-pr",
    "sdd-quickfix",
    "sdd-release",
    "sdd-specify",
    "sdd-tasks",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def public_targets(makefile: Path) -> list[str]:
    targets: set[str] = set()
    for line in makefile.read_text(encoding="utf-8").splitlines():
        match = TARGET_RE.match(line)
        if not match:
            continue
        target = match.group(1)
        if target.startswith(INTERNAL_PREFIXES) or target in INTERNAL_TARGETS:
            continue
        targets.add(target)
    return sorted(targets)


def help_targets_from_makefile(makefile: Path) -> set[str]:
    targets: set[str] = set()
    for line in makefile.read_text(encoding="utf-8").splitlines():
        match = HELP_TARGET_RE.match(line)
        if match:
            targets.add(match.group(1))
    return targets


def help_targets_from_output() -> set[str]:
    result = subprocess.run(
        ["make", "--no-print-directory", "help"],
        cwd=_repo_root(),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    targets: set[str] = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        name = stripped.split(maxsplit=1)[0]
        if TARGET_RE.match(f"{name}:"):
            targets.add(name)
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-index", action="store_true")
    args = parser.parse_args(argv)

    makefile = _repo_root() / "Makefile"
    public = public_targets(makefile)

    if args.print_index:
        for target in public:
            print(f"  {target:<30} available")
        return 0

    listed = help_targets_from_makefile(makefile) | help_targets_from_output()
    missing = [target for target in public if target not in listed]

    if missing:
        print("Makefile targets missing from `make help`:")
        for target in missing:
            print(f"  - {target}")
        return 1
    print(f"make help lists all {len(public)} public targets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
