#!/usr/bin/env python3
"""
verify_release_completeness.py — check that a GitHub Release has ALL expected artifacts.

A green verify-release-artifact means at least one asset exists. This script
goes further: it verifies the FULL expected set of platform binaries, checksums,
SBOM, and metadata files are all present. A release is "complete" only when
every expected artifact is published.

Usage:
    python scripts/verify_release_completeness.py <TAG> [owner/repo]

Exit codes:
    0 — all expected artifacts present
    1 — one or more artifacts missing, or gh unavailable (fail-closed)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Callable

FALLBACK_REPO = "sandboxcom/gludd"

EXPECTED_CATEGORIES: dict[str, Callable[[set[str]], bool]] = {
    "linux-x86_64 binary": lambda a: any(
        re.search(r"linux.*(x86[._-]?64|amd64)", n, re.IGNORECASE) for n in a
    ),
    "linux-aarch64 binary": lambda a: any(
        re.search(r"linux.*(aarch64|arm64)", n, re.IGNORECASE) for n in a
    ),
    "macos-arm64 binary": lambda a: any(
        re.search(r"(macos|darwin).*arm64", n, re.IGNORECASE) for n in a
    ),
    "windows-x86_64 binary": lambda a: any(
        n.startswith("gludd-windows-") or n.startswith("gludd-windows.")
        or re.search(r"win(dows)?.*(x86[._-]?64|amd64)", n, re.IGNORECASE)
        for n in a
    ),
    "checksums": lambda a: any(
        re.search(r"(checksums?|SHA256SUMS|sha256)|\.sha256(\.txt)?", n, re.IGNORECASE)
        for n in a
    ),
    "SBOM": lambda a: any(
        re.search(r"sbom|spdx|cyclonedx|\.cdx\.|\.spdx\.", n, re.IGNORECASE) for n in a
    ),
    "LICENSE": lambda a: any(
        re.search(r"^LICENSE\b", n) for n in a
    ),
    "THIRD_PARTY_LICENSES": lambda a: any(
        re.search(r"THIRD_PARTY", n, re.IGNORECASE) for n in a
    ),
}

MIN_ASSETS = 8  # 4 platform builds + 1 checksum + 1 SBOM + 2 docs


def _run(cmd: list[str]) -> tuple[int, str, str]:
    """Run a subprocess; return (returncode, stdout, stderr). Never raises."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError as exc:
        return 1, "", str(exc)
    except subprocess.TimeoutExpired:
        return 1, "", f"timed out running {cmd[0]}"


def _resolve_repo() -> str:
    """Return 'owner/repo' from the sandboxcom remote, or the fallback."""
    rc, out, _ = _run(["git", "remote", "get-url", "sandboxcom"])
    if rc == 0 and out:
        url = out.rstrip(".git")
        if "github.com:" in url:
            return url.split("github.com:")[-1]
        if "github.com/" in url:
            parts = url.split("github.com/")[-1].split("/")
            if len(parts) >= 2:
                return "/".join(parts[:2])
    return FALLBACK_REPO


def check_completeness(tag: str, repo: str) -> int:
    """
    Query gh for the release, then check every expected artifact category.

    Returns 0 (all present) or 1 (any missing).
    """
    rc, out, err = _run([
        "gh", "release", "view", tag,
        "-R", repo,
        "--json", "tagName,isDraft,assets,url,publishedAt",
    ])

    if rc != 0:
        msg = err or out or "gh release view returned non-zero with no output"
        print(f"ERROR: gh release view failed: {msg}", file=sys.stderr)
        print(f"COMPLETENESS CHECK: FAIL — release '{tag}' not found (fail-closed)")
        return 1

    try:
        d = json.loads(out)
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse gh output as JSON: {exc}", file=sys.stderr)
        print("COMPLETENESS CHECK: FAIL — could not parse release JSON (fail-closed)")
        return 1

    is_draft = d.get("isDraft", True)
    assets = d.get("assets", [])
    asset_names = {a.get("name", "") for a in assets}

    print(f"  tag          : {d.get('tagName', tag)}")
    print(f"  url          : {d.get('url', '(unknown)')}")
    print(f"  published_at : {d.get('publishedAt', '(unknown)')}")
    print(f"  isDraft      : {is_draft}")
    print(f"  total assets : {len(assets)}")
    print()

    if is_draft:
        print("COMPLETENESS CHECK: FAIL — release is a DRAFT (not published).")
        return 1

    if not assets:
        print("COMPLETENESS CHECK: FAIL — zero assets on this release.")
        return 1

    missing = 0
    for label, check_fn in EXPECTED_CATEGORIES.items():
        if check_fn(asset_names):
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label} — MISSING")
            missing += 1

    print()
    if len(assets) < MIN_ASSETS:
        print(
            f"  FAIL  minimum asset count: expected >= {MIN_ASSETS}, "
            f"got {len(assets)}"
        )
        missing += 1
    else:
        print(f"  PASS  minimum asset count: {len(assets)} >= {MIN_ASSETS}")

    print()
    print("Asset names found:")
    for a in sorted(asset_names):
        print(f"  - {a}")

    print()
    if missing == 0:
        print(f"COMPLETENESS CHECK: PASS — all {len(EXPECTED_CATEGORIES) + 1} checks passed.")
        return 0
    else:
        print(f"COMPLETENESS CHECK: FAIL — {missing} check(s) failed.")
        return 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "Usage: python scripts/verify_release_completeness.py <TAG> [owner/repo]",
            file=sys.stderr,
        )
        return 1

    tag = argv[1].strip()
    repo = argv[2].strip() if len(argv) > 2 else _resolve_repo()

    print(f"[verify-release-completeness] tag={tag} repo={repo}")
    return check_completeness(tag, repo)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
