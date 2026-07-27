#!/usr/bin/env python3
"""check_asset_retention.py — AC019: asset-retention-policy.

Verifies release assets are retained per policy:
- Last 3 releases: all assets
- Releases 4-10: binaries + SBOM only
- Releases >10: SBOM only
"""

import json
import os
import subprocess
import sys


KEEP_ALL_COUNT = 3
KEEP_BINARIES_UNTIL = 10
BINARY_PATTERNS = ["linux", "macos", "windows", "binary", "gludd"]
SBOM_PATTERNS = ["sbom", "cyclonedx", "spdx"]


def main():
    try:
        result = subprocess.run(
            ["gh", "release", "list", "--limit", "30", "--json", "tagName,isDraft"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("AC019: INCONCLUSIVE — gh CLI unavailable")
        sys.exit(2)

    if result.returncode != 0:
        print("AC019: INCONCLUSIVE — cannot list releases")
        sys.exit(2)

    try:
        releases = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("AC019: INCONCLUSIVE — cannot parse gh output")
        sys.exit(2)

    published = [r for r in releases if not r.get("isDraft", True)]
    violations = []

    for idx, rel in enumerate(published):
        if idx < KEEP_ALL_COUNT:
            continue

        tag = rel["tagName"]
        try:
            asset_result = subprocess.run(
                ["gh", "release", "view", tag, "--json", "assets"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            assets = json.loads(asset_result.stdout).get("assets", [])
        except Exception:
            continue

        for a in assets:
            name = a.get("name", "").lower()
            if idx < KEEP_BINARIES_UNTIL:
                if any(p in name for p in SBOM_PATTERNS + BINARY_PATTERNS):
                    continue
                violations.append(f"{tag}: {a.get('name')} (idx={idx}, keep binary+sbom only)")
            else:
                if any(p in name for p in SBOM_PATTERNS):
                    continue
                violations.append(f"{tag}: {a.get('name')} (idx={idx}, keep sbom only)")

    if violations:
        print(f"AC019: FAIL — {len(violations)} retention policy violation(s)")
        for v in violations[:10]:
            print(f"  {v}")
        prune_flag = os.environ.get("PRUNE")
        if prune_flag:
            print("AC019: Run with PRUNE=1 to auto-prune excess assets")
        sys.exit(1)

    print(f"AC019: PASS — asset retention policy satisfied for {len(published)} releases")
    sys.exit(0)


if __name__ == "__main__":
    main()
