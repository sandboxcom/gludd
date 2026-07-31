#!/usr/bin/env python3
"""check_tag_immutability.py — AC003: tag-immutability.

Once a tag's CI run is GREEN, the tag MUST NOT be moved, deleted, or overwritten.
The only sanctioned tag movement is `make release-recut` when the Build-and-Release
job itself failed but the commit is known-good.
"""

import os
import subprocess
import sys


def ci_green_for_sha(sha: str) -> bool:
    """Return True if the latest CI run for this SHA has conclusion='success'."""
    try:
        result = subprocess.run(
            ["gh", "run", "list", "--commit", sha, "--json", "conclusion", "--limit", "1", "--status", "completed"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    import json

    try:
        runs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not runs:
        return False
    return runs[0].get("conclusion") == "success"


def tag_has_artifacts(tag: str) -> bool:
    """Return True if the release has downloadable assets."""
    try:
        result = subprocess.run(
            ["gh", "release", "view", tag, "--json", "isDraft,assets", "--jq", ".isDraft, (.assets | length)"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    lines = result.stdout.strip().split("\n")
    if len(lines) < 2:
        return False
    is_draft = lines[0].strip() == "true"
    asset_count = int(lines[1].strip()) if lines[1].strip().isdigit() else 0
    return not is_draft and asset_count > 0


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    force = os.environ.get("FORCE", "") == "1"

    if not tag:
        print("AC003: INCONCLUSIVE — TAG required")
        sys.exit(2)

    if force:
        print(f"AC003: BYPASS — FORCE=1 set, skipping immutability check for {tag}")
        sys.exit(0)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        print("AC003: INCONCLUSIVE — git tag lookup timed out")
        sys.exit(2)

    if result.returncode != 0:
        print(f"AC003: PASS — tag '{tag}' does not exist yet (safe to create)")
        sys.exit(0)

    tag_sha = result.stdout.strip()

    if ci_green_for_sha(tag_sha):
        print(
            f"AC003: BLOCKED — tag '{tag}' ({tag_sha[:12]}) has GREEN CI. "
            "Tag is immutable. Use FORCE=1 only for release-recut pipeline."
        )
        sys.exit(1)

    if tag_has_artifacts(tag):
        print(
            f"AC003: BLOCKED — tag '{tag}' has published artifacts. "
            "Tag is immutable. Use FORCE=1 only for release-recut pipeline."
        )
        sys.exit(1)

    print(f"AC003: PASS — tag '{tag}' exists but has no green CI or artifacts (safe to recut)")
    sys.exit(0)


if __name__ == "__main__":
    main()
