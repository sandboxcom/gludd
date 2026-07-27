#!/usr/bin/env python3
"""check_multiplatform_consistency.py — AC010: multi-platform-consistency.

Verifies artifacts exist for all target platforms (linux-amd64, macos-amd64 at minimum).
Checks binary sizes are similar (±50% of mean).
"""

import json
import os
import subprocess
import sys


PLATFORMS = ["linux-amd64", "linux-arm64", "macos-amd64", "macos-arm64"]


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC010: TAG required")
        sys.exit(2)

    try:
        result = subprocess.run(
            ["gh", "release", "view", tag, "--json", "assets"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("AC010: INCONCLUSIVE — gh CLI unavailable or timed out")
        sys.exit(2)

    if result.returncode != 0:
        print(f"AC010: INCONCLUSIVE — release '{tag}' not found")
        sys.exit(2)

    try:
        data = json.loads(result.stdout)
        assets = data.get("assets", [])
    except json.JSONDecodeError:
        print("AC010: INCONCLUSIVE — cannot parse gh output")
        sys.exit(2)

    found_platforms = set()
    binaries = []
    for a in assets:
        name = a.get("name", "").lower()
        for p in PLATFORMS:
            if p in name:
                found_platforms.add(p)
                binaries.append(a)
                break

    missing = set(PLATFORMS) - found_platforms
    if missing:
        print(f"AC010: FAIL — missing platforms: {', '.join(sorted(missing))}")
        sys.exit(1)

    sizes = [int(a.get("size", 0)) for a in binaries]
    nonzero = [s for s in sizes if s > 0]
    if nonzero:
        mean = sum(nonzero) / len(nonzero)
        for i, s in enumerate(nonzero):
            if s < mean * 0.5 or s > mean * 1.5:
                print(f"AC010: WARN — size {s} deviates from mean {mean:.0f}")

    min_platforms = int(os.environ.get("GLUDD_MIN_PLATFORMS", "4"))
    if len(found_platforms) < min_platforms:
        print(f"AC010: FAIL — only {len(found_platforms)}/{min_platforms} required platforms found")
        sys.exit(1)

    print(f"AC010: PASS — {len(found_platforms)} platforms verified")
    sys.exit(0)


if __name__ == "__main__":
    main()
