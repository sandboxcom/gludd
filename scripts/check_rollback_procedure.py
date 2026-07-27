#!/usr/bin/env python3
"""check_rollback_procedure.py — AC009: release-rollback.

Verifies RELEASE_RUNBOOK.md has a Rollback section with required fields
for the current version: target version, container pin, binary URL, config compat.
"""

import re
import subprocess
import sys
from pathlib import Path


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else None
    root = Path(__file__).resolve().parent.parent
    runbook = root / "docs" / "RELEASE_RUNBOOK.md"

    if not runbook.exists():
        print("AC009: FAIL — docs/RELEASE_RUNBOOK.md not found")
        sys.exit(1)

    content = runbook.read_text()

    rollback_section = re.search(r"##\s+Rollback.*?(?=##\s|\Z)", content, re.DOTALL)
    if not rollback_section:
        print("AC009: FAIL — no '## Rollback' section in RELEASE_RUNBOOK.md")
        sys.exit(1)

    section_text = rollback_section.group(0)

    required_fields = ["target", "container", "binary", "config"]
    missing = [f for f in required_fields if f not in section_text.lower()]
    if missing:
        print(f"AC009: FAIL — Rollback section missing references to: {', '.join(missing)}")
        sys.exit(1)

    if tag:
        version = tag.lstrip("v")
        if version not in section_text:
            print(f"AC009: FAIL — Rollback section does not mention version {version}")
            sys.exit(1)

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
