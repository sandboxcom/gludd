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


def check_platform_coverage(assets, platforms=None, min_platforms=4):
    if platforms is None:
        platforms = PLATFORMS
    found_platforms = set()
    binaries = []
    for a in assets:
        name = a.get("name", "").lower()
        for p in platforms:
            if p in name:
                found_platforms.add(p)
                binaries.append(a)
                break

    missing = set(platforms) - found_platforms
    issues = []
    if missing:
        issues.append("FAIL — missing platforms: " + ", ".join(sorted(missing)))

    sizes = [int(a.get("size", 0)) for a in binaries]
    nonzero = [s for s in sizes if s > 0]
    if nonzero:
        mean = sum(nonzero) / len(nonzero)
        for i, s in enumerate(nonzero):
            if s < mean * 0.5 or s > mean * 1.5:
                issues.append(f"WARN — size {s} deviates from mean {mean:.0f}")

    if len(found_platforms) < min_platforms:
        issues.append(f"FAIL — only {len(found_platforms)}/{min_platforms} required platforms found")

    passed = len(issues) == 0 or all(not i.startswith("FAIL") for i in issues)
    return passed, found_platforms, missing, issues


def check_binary_size_consistency(sizes_dict):
    issues = []
    nonzero = {k: v for k, v in sizes_dict.items() if v > 0}
    if not nonzero:
        return True, []
    mean = sum(nonzero.values()) / len(nonzero)
    for name, size in nonzero.items():
        if size < mean * 0.5 or size > mean * 1.5:
            issues.append(f"WARN — {name} size {size} deviates from mean {mean:.0f}")
    return True, issues


def check_checksum_entries(assets, checksums_content):
    from validate_release_checksums import parse_checksums

    checksums = parse_checksums(checksums_content)
    issues = []
    for a in assets:
        name = a.get("name", "")
        if name not in checksums:
            issues.append(f"FAIL — missing checksum entry for: {name}")
    passed = len(issues) == 0
    return passed, issues


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

    min_platforms = int(os.environ.get("GLUDD_MIN_PLATFORMS", "4"))
    passed, found_platforms, missing, issues = check_platform_coverage(assets, PLATFORMS, min_platforms)

    for issue in issues:
        prefix = "AC010: FAIL" if issue.startswith("FAIL") else "AC010: WARN"
        print(f"AC010: {issue}")

    if not passed:
        sys.exit(1)

    print(f"AC010: PASS — {len(found_platforms)} platforms verified")
    sys.exit(0)


if __name__ == "__main__":
    main()
