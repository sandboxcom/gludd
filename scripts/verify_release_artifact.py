#!/usr/bin/env python3
"""
verify_release_artifact.py — confirm a GitHub Release has published assets.

A git tag is NOT a release.  The Build-and-Release CI job must complete
successfully and upload assets before a version is considered "shipped."
This script is the machine-enforceable definition of "done" for a release.

Usage:
    python scripts/verify_release_artifact.py <TAG> [owner/repo]

Exit codes:
    0  — release exists, is not a draft, and has at least one published asset
    1  — release missing, draft-only, zero assets, or gh unavailable (fail-closed)
"""

from __future__ import annotations

import json
import subprocess
import sys

FALLBACK_REPO = "sandboxcom/gludd"


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


def check_artifact(tag: str, repo: str) -> int:
    """
    Query gh for the release and check assets.

    Returns 0 (PASS) or 1 (FAIL).
    """
    rc, out, err = _run([
        "gh", "release", "view", tag,
        "-R", repo,
        "--json", "tagName,isDraft,assets,url,publishedAt",
    ])

    if rc != 0:
        msg = err or out or "gh release view returned non-zero with no output"
        print(f"ERROR: gh release view failed: {msg}", file=sys.stderr)
        print(
            f"ARTIFACT CHECK: FAIL — release '{tag}' not found on {repo} "
            f"or gh unavailable (fail-closed)"
        )
        return 1

    try:
        d = json.loads(out)
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse gh output as JSON: {exc}", file=sys.stderr)
        print("ARTIFACT CHECK: FAIL — could not parse release JSON (fail-closed)")
        return 1

    is_draft = d.get("isDraft", True)
    assets = d.get("assets", [])
    url = d.get("url", "(unknown)")
    published_at = d.get("publishedAt", "(unknown)")

    print(f"  tag          : {d.get('tagName', tag)}")
    print(f"  url          : {url}")
    print(f"  published_at : {published_at}")
    print(f"  isDraft      : {is_draft}")
    print(f"  assets       : {len(assets)}")
    for a in assets:
        print(f"    - {a.get('name', '?')}  ({a.get('size', 0):,} bytes)")

    if is_draft:
        print(
            f"ARTIFACT CHECK: FAIL — {tag} is a DRAFT release (not published).\n"
            f"  A draft is not a shipped release; the release job must have published it."
        )
        return 1

    if not assets:
        print(
            f"ARTIFACT CHECK: FAIL — {tag} release record exists but has ZERO assets.\n"
            f"  This means the Build-and-Release CI job either:\n"
            f"    (a) has not yet completed (still running — wait and retry), OR\n"
            f"    (b) failed on the gate/build jobs and the release job was skipped.\n"
            f"  Check CI: make ci-status\n"
            f"  Retry:    make verify-release-artifact TAG={tag}"
        )
        return 1

    print(
        f"ARTIFACT CHECK: PASS — {tag} has {len(assets)} published asset(s) on {repo}.\n"
        f"  A release is an artifact, not a tag.  This version is shipped."
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "Usage: python scripts/verify_release_artifact.py <TAG> [owner/repo]",
            file=sys.stderr,
        )
        return 1

    tag = argv[1].strip()
    repo = argv[2].strip() if len(argv) > 2 else _resolve_repo()

    print(f"[verify-release-artifact] tag={tag} repo={repo}")
    return check_artifact(tag, repo)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
