#!/usr/bin/env python3
"""Validate the canonical SHA256SUMS index against published release assets."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKSUM_ASSET_NAME = "SHA256SUMS"
DEFAULT_REPOSITORY = "sandboxcom/gludd"


def get_checksums_content(tag: str, repository: str = DEFAULT_REPOSITORY) -> str | None:
    """Return the published aggregate checksum index without writing it to disk."""
    try:
        result = subprocess.run(
            [
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                repository,
                "--pattern",
                CHECKSUM_ASSET_NAME,
                "--output",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def parse_checksums(content: str) -> dict[str, str]:
    """Parse a standard SHA-256 index into ``filename -> digest`` entries."""
    entries: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0]
        name = parts[-1].lstrip("*")
        if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest):
            entries[name] = digest
    return entries


def get_release_asset_names(
    tag: str, repository: str = DEFAULT_REPOSITORY
) -> set[str] | None:
    """Return the exact published asset-name set, or ``None`` on API failure."""
    try:
        result = subprocess.run(
            [
                "gh",
                "release",
                "view",
                tag,
                "--repo",
                repository,
                "--json",
                "assets",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload: object = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
        return None
    names: set[str] = set()
    for asset in payload["assets"]:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            return None
        names.add(asset["name"])
    return names


def validate_checksum_coverage(
    entries: dict[str, str], release_assets: set[str]
) -> list[str]:
    """Require one safe digest entry for every asset except the index itself."""
    errors: list[str] = []
    unsafe = sorted(name for name in entries if Path(name).name != name)
    if unsafe:
        errors.append("unsafe checksum names: " + ", ".join(unsafe))

    safe_entries = {name for name in entries if Path(name).name == name}
    expected = release_assets - {CHECKSUM_ASSET_NAME}
    missing = sorted(expected - safe_entries)
    absent = sorted(safe_entries - expected)
    if missing:
        errors.append("checksums missing published assets: " + ", ".join(missing))
    if absent:
        errors.append("checksums reference absent assets: " + ", ".join(absent))
    if CHECKSUM_ASSET_NAME not in release_assets:
        errors.append(f"published release is missing {CHECKSUM_ASSET_NAME}")
    return errors


def download_artifact_digest(
    tag: str, filename: str, repository: str = DEFAULT_REPOSITORY
) -> str | None:
    """Download one asset to bounded temporary storage and return its SHA-256."""
    if Path(filename).name != filename:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="gludd-release-checksum-") as temp_dir:
            output = Path(temp_dir) / "asset"
            result = subprocess.run(
                [
                    "gh",
                    "release",
                    "download",
                    tag,
                    "--repo",
                    repository,
                    "--pattern",
                    filename,
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0 or not output.is_file():
                return None
            digest = hashlib.sha256()
            with output.open("rb") as artifact:
                for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    tag = args[0] if args else os.environ.get("TAG", "")
    repository = os.environ.get("GLUDD_GITHUB_REPOSITORY", DEFAULT_REPOSITORY)
    if not tag:
        print("AC006: INCONCLUSIVE — TAG required")
        return 2

    checksums_content = get_checksums_content(tag, repository)
    if not checksums_content:
        print(f"AC006: INCONCLUSIVE — {CHECKSUM_ASSET_NAME} not readable for {tag}")
        return 2

    entries = parse_checksums(checksums_content)
    if not entries:
        print(f"AC006: FAIL — {CHECKSUM_ASSET_NAME} is empty or unparseable")
        return 1

    release_assets = get_release_asset_names(tag, repository)
    if release_assets is None:
        print(f"AC006: INCONCLUSIVE — cannot enumerate published assets for {tag}")
        return 2

    coverage_errors = validate_checksum_coverage(entries, release_assets)
    for error in coverage_errors:
        print(f"AC006: FAIL — {error}")
    if coverage_errors:
        return 1

    print(
        f"AC006: Found {len(entries)} complete entries in "
        f"{CHECKSUM_ASSET_NAME} for {tag}",
        flush=True,
    )
    mismatches = 0
    unavailable = 0
    for filename, expected_digest in sorted(entries.items()):
        print(f"AC006: VERIFY — downloading {filename}", flush=True)
        actual_digest = download_artifact_digest(tag, filename, repository)
        if actual_digest is None:
            print(f"AC006: INCONCLUSIVE — {filename}: cannot download")
            unavailable += 1
        elif actual_digest != expected_digest:
            print(
                f"AC006: FAIL — {filename}: checksum mismatch "
                f"(expected {expected_digest[:12]}..., got {actual_digest[:12]}...)"
            )
            mismatches += 1
        else:
            print(f"AC006: PASS — {filename}: checksum verified")

    if mismatches:
        print(f"AC006: FAIL — {mismatches} checksum mismatch(es)")
        return 1
    if unavailable:
        print(f"AC006: INCONCLUSIVE — {unavailable} asset download(s) unavailable")
        return 2
    print(f"AC006: PASS — all {len(entries)} published asset checksums verified for {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
