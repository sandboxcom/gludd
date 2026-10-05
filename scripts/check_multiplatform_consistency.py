#!/usr/bin/env python3
"""Verify the published native platform matrix and aggregate checksum coverage."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable

from validate_release_checksums import CHECKSUM_ASSET_NAME, get_checksums_content, parse_checksums

DEFAULT_REPOSITORY = "sandboxcom/gludd"
PLATFORMS = ["linux-x86_64", "linux-aarch64", "macos-arm64", "windows-x86_64"]
PLATFORM_PATTERNS = {
    "linux-x86_64": re.compile(r"^gludd-.+-linux-x86_64\.tar\.gz$", re.IGNORECASE),
    "linux-aarch64": re.compile(r"^gludd-.+-linux-aarch64\.tar\.gz$", re.IGNORECASE),
    "macos-arm64": re.compile(r"^gludd-.+-macos-arm64\.tar\.gz$", re.IGNORECASE),
    "windows-x86_64": re.compile(r"^gludd-.+-windows-x86_64\.zip$", re.IGNORECASE),
}


def _matches_platform(filename: str, platform: str) -> bool:
    pattern = PLATFORM_PATTERNS.get(platform)
    if pattern is not None:
        return pattern.fullmatch(filename) is not None
    return platform.lower() in filename.lower() and not filename.lower().endswith(".sha256")


def _asset_size(asset: dict[str, object]) -> int:
    value = asset.get("size", 0)
    if not isinstance(value, (int, str)):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def check_platform_coverage(
    assets: Iterable[dict[str, object]],
    platforms: list[str] | None = None,
    min_platforms: int = 4,
) -> tuple[bool, set[str], set[str], list[str]]:
    """Return coverage and size diagnostics for primary native archives only."""
    required = PLATFORMS if platforms is None else platforms
    found_platforms: set[str] = set()
    binaries: list[dict[str, object]] = []
    for asset in assets:
        name = str(asset.get("name", ""))
        for platform in required:
            if _matches_platform(name, platform):
                found_platforms.add(platform)
                binaries.append(asset)
                break

    missing = set(required) - found_platforms
    issues: list[str] = []
    if missing:
        issues.append("FAIL — missing platforms: " + ", ".join(sorted(missing)))

    sizes = [_asset_size(asset) for asset in binaries]
    nonzero = [size for size in sizes if size > 0]
    if nonzero:
        mean = sum(nonzero) / len(nonzero)
        for size in nonzero:
            if size < mean * 0.5 or size > mean * 1.5:
                issues.append(f"WARN — size {size} deviates from mean {mean:.0f}")

    if len(found_platforms) < min_platforms:
        issues.append(
            f"FAIL — only {len(found_platforms)}/{min_platforms} required platforms found"
        )

    passed = all(not issue.startswith("FAIL") for issue in issues)
    return passed, found_platforms, missing, issues


def check_binary_size_consistency(
    sizes_dict: dict[str, int],
) -> tuple[bool, list[str]]:
    issues: list[str] = []
    nonzero = {name: size for name, size in sizes_dict.items() if size > 0}
    if not nonzero:
        return True, []
    mean = sum(nonzero.values()) / len(nonzero)
    for name, size in nonzero.items():
        if size < mean * 0.5 or size > mean * 1.5:
            issues.append(f"WARN — {name} size {size} deviates from mean {mean:.0f}")
    return True, issues


def check_checksum_entries(
    assets: Iterable[dict[str, object]], checksums_content: str
) -> tuple[bool, list[str]]:
    """Require aggregate digest coverage for every asset except the index itself."""
    checksums = parse_checksums(checksums_content)
    issues: list[str] = []
    for asset in assets:
        name = str(asset.get("name", ""))
        if name == CHECKSUM_ASSET_NAME:
            continue
        if name not in checksums:
            issues.append(f"FAIL — missing checksum entry for: {name}")
    return len(issues) == 0, issues


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    tag = args[0] if args else os.environ.get("TAG", "")
    repository = os.environ.get("GLUDD_GITHUB_REPOSITORY", DEFAULT_REPOSITORY)
    if not tag:
        print("AC010: INCONCLUSIVE — TAG required")
        return 2

    try:
        result = subprocess.run(
            ["gh", "release", "view", tag, "--repo", repository, "--json", "assets"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        print("AC010: INCONCLUSIVE — gh CLI unavailable or timed out")
        return 2
    if result.returncode != 0:
        print(f"AC010: INCONCLUSIVE — release '{tag}' not found")
        return 2

    try:
        data: object = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("AC010: INCONCLUSIVE — cannot parse gh output")
        return 2
    if not isinstance(data, dict) or not isinstance(data.get("assets"), list):
        print("AC010: INCONCLUSIVE — release assets have an invalid shape")
        return 2
    assets = data["assets"]

    min_platforms = int(os.environ.get("GLUDD_MIN_PLATFORMS", str(len(PLATFORMS))))
    passed, found_platforms, _, issues = check_platform_coverage(
        assets, PLATFORMS, min_platforms
    )

    checksums_content = get_checksums_content(tag, repository)
    if checksums_content is None:
        print(f"AC010: INCONCLUSIVE — cannot read published {CHECKSUM_ASSET_NAME}")
        return 2
    checksums_passed, checksum_issues = check_checksum_entries(assets, checksums_content)
    issues.extend(checksum_issues)
    passed = passed and checksums_passed

    for issue in issues:
        print(f"AC010: {issue}")
    if not passed:
        return 1

    print(
        f"AC010: PASS — {len(found_platforms)} canonical platforms and "
        f"aggregate checksums verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
