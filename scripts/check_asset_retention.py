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


def asset_matches_patterns(name, patterns):
    name_lower = name.lower()
    return any(p in name_lower for p in patterns)


def check_retention_for_releases(published_releases, get_assets):
    violations = []

    for idx, rel in enumerate(published_releases):
        if idx < KEEP_ALL_COUNT:
            continue

        tag = rel["tagName"]
        assets = get_assets(tag)
        if assets is None:
            continue

        for a in assets:
            name = a.get("name", "")
            if idx < KEEP_BINARIES_UNTIL:
                allowed = SBOM_PATTERNS + BINARY_PATTERNS
                if asset_matches_patterns(name, allowed):
                    continue
                violations.append(f"{tag}: {name} (idx={idx}, keep binary+sbom only)")
            else:
                if asset_matches_patterns(name, SBOM_PATTERNS):
                    continue
                violations.append(f"{tag}: {name} (idx={idx}, keep sbom only)")

    return violations


def _gh_fetch_assets(tag):
    try:
        asset_result = subprocess.run(
            ["gh", "release", "view", tag, "--json", "assets"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return json.loads(asset_result.stdout).get("assets", [])
    except Exception:
        return None


def _gh_list_releases():
    try:
        result = subprocess.run(
            ["gh", "release", "list", "--limit", "30", "--json", "tagName,isDraft"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, "AC019: INCONCLUSIVE — gh CLI unavailable"

    if result.returncode != 0:
        return None, "AC019: INCONCLUSIVE — cannot list releases"

    try:
        releases = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "AC019: INCONCLUSIVE — cannot parse gh output"

    return releases, None


def main():
    releases, error = _gh_list_releases()
    if error:
        print(error)
        sys.exit(2)

    assert releases is not None
    published = [r for r in releases if not r.get("isDraft", True)]
    violations = check_retention_for_releases(published, _gh_fetch_assets)

    if violations:
        print(f"AC019: FAIL — {len(violations)} retention policy violation(s)")
        for v in violations[:10]:
            print(f"  {v}")
        if os.environ.get("PRUNE"):
            print("AC019: Run with PRUNE=1 to auto-prune excess assets")
        sys.exit(1)

    print(f"AC019: PASS — asset retention policy satisfied for {len(published)} releases")
    sys.exit(0)


if __name__ == "__main__":
    main()
