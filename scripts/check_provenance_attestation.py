#!/usr/bin/env python3
"""Verify GitHub/Sigstore SLSA provenance for a published release."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

DEFAULT_REPOSITORY = "sandboxcom/gludd"
PROVENANCE_PATTERNS = [
    ".build.provenance",
    "provenance.json",
    "attestation.json",
    ".intoto.jsonl",
]


def check_provenance_attestation(
    assets: list[dict[str, Any]],
) -> tuple[bool, int, int]:
    """Classify legacy sidecar assets; a release manifest is not an attestation."""
    binary_assets = [
        asset
        for asset in assets
        if any(
            marker in str(asset.get("name", "")).lower()
            for marker in ["linux", "macos", "windows", "binary", "gludd"]
        )
        and "provenance" not in str(asset.get("name", "")).lower()
    ]
    provenance_assets = [
        asset
        for asset in assets
        if any(
            pattern in str(asset.get("name", "")).lower()
            for pattern in PROVENANCE_PATTERNS
        )
    ]
    return bool(provenance_assets), len(provenance_assets), len(binary_assets)


def parse_provenance_file(content: str) -> object | None:
    try:
        payload: object = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload


def verify_provenance_digest(
    provenance_dict: dict[str, Any] | None, binary_sha256: str
) -> bool:
    if not provenance_dict:
        return False
    subjects = provenance_dict.get("subject", [])
    if not isinstance(subjects, list) or not subjects:
        return False
    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        digest = subject.get("digest", {})
        if isinstance(digest, dict) and digest.get("sha256") == binary_sha256:
            return True
    return False


def extract_builder_id(provenance_dict: dict[str, Any] | None) -> str | None:
    if not provenance_dict:
        return None
    builder = provenance_dict.get("builder", {})
    if not isinstance(builder, dict):
        return None
    builder_id = builder.get("id")
    return builder_id if isinstance(builder_id, str) else None


def verify_release_attestation(
    tag: str, repository: str = DEFAULT_REPOSITORY
) -> tuple[bool | None, str]:
    """Delegate signature, identity, digest, and release-subject checks to ``gh``."""
    try:
        result = subprocess.run(
            [
                "gh",
                "release",
                "verify",
                tag,
                "--repo",
                repository,
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return None, f"gh release verification unavailable: {exc}"
    detail = result.stdout.strip() or result.stderr.strip() or "no verification output"
    if result.returncode != 0:
        return False, detail
    try:
        payload: object = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "gh release verify returned invalid JSON"
    if payload in ({}, []):
        return False, "gh release verify returned no attestations"
    return True, detail


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    tag = args[0] if args else os.environ.get("TAG", "")
    repository = os.environ.get("GLUDD_GITHUB_REPOSITORY", DEFAULT_REPOSITORY)
    if not tag:
        print("AC011: INCONCLUSIVE — TAG required")
        return 2

    verified, detail = verify_release_attestation(tag, repository)
    if verified is None:
        print(f"AC011: INCONCLUSIVE — {detail}")
        return 2
    if not verified:
        print(f"AC011: FAIL — release provenance did not verify: {detail}")
        return 1
    print(f"AC011: PASS — GitHub/Sigstore release provenance verified for {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
