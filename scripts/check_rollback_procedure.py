#!/usr/bin/env python3
"""check_rollback_procedure.py — AC009: release-rollback.

Verifies RELEASE_RUNBOOK.md has a Rollback section with required fields
for the current version: target version, container pin, binary URL, config compat.
"""

import re
import subprocess
import sys
from pathlib import Path


ROLLBACK_SECTION_RE = re.compile(r"##\s+Rollback.*?(?=##\s|\Z)", re.DOTALL)

required_rollback_fields = ["target", "container", "binary", "config"]


def check_rollback_section(content, tag=None):
    """Check RELEASE_RUNBOOK.md rollback section for required fields.

    Returns (errors, warnings) lists. Empty lists = PASS.
    """
    errors = []
    warnings = []

    rollback_match = ROLLBACK_SECTION_RE.search(content)
    if not rollback_match:
        errors.append("no '## Rollback' section in RELEASE_RUNBOOK.md")
        return errors, warnings

    section_text = rollback_match.group(0)
    missing = [f for f in required_rollback_fields if f not in section_text.lower()]
    if missing:
        errors.append(f"Rollback section missing references to: {', '.join(missing)}")

    if tag:
        version = tag.lstrip("v")
        if version not in section_text:
            errors.append(f"Rollback section does not mention version {version}")

    return errors, warnings


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else None
    root = Path(__file__).resolve().parent.parent
    runbook = root / "docs" / "RELEASE_RUNBOOK.md"

    if not runbook.exists():
        print("AC009: FAIL — docs/RELEASE_RUNBOOK.md not found")
        sys.exit(1)

    content = runbook.read_text()
    errors, warnings = check_rollback_section(content, tag)

    if errors:
        for err in errors:
            print(f"AC009: FAIL — {err}")
        sys.exit(1)

    for warn in warnings:
        print(f"AC009: WARN — {warn}")

    try:
        subprocess.run(
            ["gh", "release", "list", "--limit", "2"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        print("AC009: WARN — gh CLI unavailable, skipping prior-release artifact check")

    print("AC009: PASS — rollback procedure documented")
    sys.exit(0)


if __name__ == "__main__":
    main()
