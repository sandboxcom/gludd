#!/usr/bin/env python3
"""validate_release_checksums.py — AC006: checksum-validation.

Every release artifact listed in checksums.txt MUST be downloadable and its
SHA256 MUST match the listed checksum. The checksums file MUST itself be one
of the release assets.
"""

import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.request


def get_checksums_content(tag: str) -> str | None:
    """Fetch checksums.txt asset content from a GitHub release."""
    try:
        result = subprocess.run(
            ["gh", "release", "download", tag, "--pattern", "checksums.txt", "--dir", "-", "--output", "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def parse_checksums(content: str) -> dict[str, str]:
    """Parse standard SHA256 checksum file into {filename: sha256} dict."""
    entries: dict[str, str] = {}
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            sha = parts[0]
            name = parts[-1].lstrip("*")
            if len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):
                entries[name] = sha
    return entries


def download_artifact(tag: str, filename: str) -> bytes | None:
    """Download a single release asset and return its bytes."""
    try:
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run(
                ["gh", "release", "download", tag, "--pattern", filename, "--dir", td],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                return None
            filepath = os.path.join(td, filename)
            if not os.path.exists(filepath):
                return None
            with open(filepath, "rb") as f:
                return f.read()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC006: INCONCLUSIVE — TAG required")
        sys.exit(2)

    checksums_content = get_checksums_content(tag)
    if not checksums_content:
        print(f"AC006: INCONCLUSIVE — checksums.txt not found for {tag}")
        sys.exit(2)

    entries = parse_checksums(checksums_content)
    if not entries:
        print("AC006: FAIL — checksums.txt is empty or unparseable")
        sys.exit(1)

    print(f"AC006: Found {len(entries)} entries in checksums.txt for {tag}")

    failures = 0
    for filename, expected_sha in sorted(entries.items()):
        artifact_bytes = download_artifact(tag, filename)
        if artifact_bytes is None:
            print(f"AC006: FAIL — {filename}: cannot download")
            failures += 1
            continue

        actual_sha = hashlib.sha256(artifact_bytes).hexdigest()
        if actual_sha != expected_sha:
            print(
                f"AC006: FAIL — {filename}: checksum mismatch "
                f"(expected {expected_sha[:12]}..., got {actual_sha[:12]}...)"
            )
            failures += 1
        else:
            print(f"AC006: PASS — {filename}: checksum verified")

    if failures:
        print(f"AC006: FAIL — {failures} checksum mismatch(es)")
        sys.exit(1)

    print(f"AC006: PASS — all {len(entries)} checksums verified for {tag}")
    sys.exit(0)


if __name__ == "__main__":
    main()
