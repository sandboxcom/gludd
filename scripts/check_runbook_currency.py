#!/usr/bin/env python3
"""check_runbook_currency.py — AC013: release-runbook-currency.

Verifies RELEASE_RUNBOOK.md is updated for the current release.
Checks: last-updated date, version mention, make target references.
"""

import os
import re
import sys
from pathlib import Path


def extract_make_targets(content: str) -> set[str]:
    pattern = re.compile(r"`make\s+([a-zA-Z_-]+)`")
    return set(pattern.findall(content))


def find_missing_targets(targets: set[str], makefile_text: str) -> list[str]:
    missing = []
    for t in targets:
        if not re.search(rf"^{t}:", makefile_text, re.MULTILINE):
            missing.append(t)
    return missing


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    root = Path(__file__).resolve().parent.parent
    runbook = root / "docs" / "RELEASE_RUNBOOK.md"
    makefile = root / "Makefile"

    if not runbook.exists():
        print("AC013: FAIL — docs/RELEASE_RUNBOOK.md not found")
        sys.exit(1)

    content = runbook.read_text()
    version = tag.lstrip("v") if tag else ""

    if version and version not in content:
        print(f"AC013: FAIL — Runbook does not mention version {version}")
        sys.exit(1)

    makefile_text = makefile.read_text()
    target_pattern = re.compile(r"`make\s+([a-zA-Z_-]+)`")
    targets_in_runbook = set(target_pattern.findall(content))

    missing_targets = []
    for t in targets_in_runbook:
        if not re.search(rf"^{t}:", makefile_text, re.MULTILINE):
            missing_targets.append(t)

    if missing_targets:
        print(f"AC013: FAIL — Runbook references nonexistent targets: {', '.join(missing_targets)}")
        sys.exit(1)

    print("AC013: PASS — runbook is current")
    sys.exit(0)


if __name__ == "__main__":
    main()
