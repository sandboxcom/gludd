#!/usr/bin/env python3
"""check_prerelease_flag.py — AC005: prerelease-flag-vs-tag-shape.

Ensures GitHub Release prerelease flag matches tag semver shape.
Tags with -alpha/-beta/-rc → prerelease: true
Tags matching v[0-9]+.[0-9]+.[0-9]+ exactly → prerelease: false
"""

import os
import re
import subprocess
import sys


def expected_prerelease(tag):
    if re.search(r"-(alpha|beta|rc|dev|pre)", tag):
        return True
    if re.match(r"^v\d+\.\d+\.\d+$", tag):
        return False
    return True


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC005: TAG required")
        sys.exit(2)

    expected = expected_prerelease(tag)
    expected_str = "true" if expected else "false"

    try:
        result = subprocess.run(
            ["gh", "release", "view", tag, "--json", "isPrerelease"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("AC005: INCONCLUSIVE — gh CLI unavailable or timed out")
        sys.exit(2)

    if result.returncode != 0:
        print(f"AC005: INCONCLUSIVE — release '{tag}' not found or gh error")
        print(result.stderr[-200:] if result.stderr else "No stderr")
        sys.exit(2)

    import json

    try:
        data = json.loads(result.stdout)
        actual = data.get("isPrerelease", None)
    except json.JSONDecodeError:
        print("AC005: INCONCLUSIVE — cannot parse gh output")
        sys.exit(2)

    if actual is None:
        print(f"AC005: INCONCLUSIVE — isPrerelease field missing for {tag}")
        sys.exit(2)

    actual_str = "true" if actual else "false"

    if actual == expected:
        print(f"AC005: PASS — prerelease flag {actual_str} matches tag shape {tag}")
        sys.exit(0)
    else:
        print(f"AC005: FAIL — prerelease flag is {actual_str}, expected {expected_str} for tag {tag}")
        print(f"AC005: Run: gh release edit {tag} --prerelease")
        sys.exit(1)


if __name__ == "__main__":
    main()
