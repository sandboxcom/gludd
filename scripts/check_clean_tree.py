#!/usr/bin/env python3
"""Refuse push if working tree is dirty. Prevents pre-commit stash conflicts."""
import subprocess
import sys

result = subprocess.run(
    ["git", "status", "--porcelain"], capture_output=True, text=True
)
dirty = result.stdout.strip()
if dirty:
    count = len(dirty.split("\n"))
    print(
        f"DIRTY TREE ({count} files): cannot push. Commit or stash first.",
        file=sys.stderr,
    )
    print(dirty, file=sys.stderr)
    print(
        "\nFix: make git-add-all && make ship-commit MSG='...'",
        file=sys.stderr,
    )
    print("  or: make git-stash", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
