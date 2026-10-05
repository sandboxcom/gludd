#!/usr/bin/env python3
"""Verify that a published release carries a current, versioned CycloneDX SBOM."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

DEFAULT_REPOSITORY = "sandboxcom/gludd"
SBOM_ASSET_NAME = "sbom.json"


def run_git(args: list[str]) -> tuple[str, str, int]:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def get_tag_timestamp(tag: str) -> int:
    out, _, _ = run_git(["tag", "-l", "--format=%(taggerdate:unix)", tag])
    try:
        return int(out) if out else 0
    except ValueError:
        return 0


def get_sbom_content(tag: str, repository: str = DEFAULT_REPOSITORY) -> str | None:
    """Read the canonical published SBOM through GitHub CLI stdout mode."""
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
                SBOM_ASSET_NAME,
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


def _normalized_distribution_name(value: object) -> str:
    return str(value).strip().lower().replace("_", "-")


def validate_sbom(payload: object, version: str, tag_timestamp: int) -> list[str]:
    """Return all schema, generation-time, and release-identity errors."""
    if not isinstance(payload, dict):
        return ["SBOM JSON root must be an object"]
    errors: list[str] = []
    if payload.get("bomFormat") != "CycloneDX":
        errors.append("SBOM is not CycloneDX")

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("SBOM metadata is missing")
        metadata = {}
    timestamp = metadata.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        errors.append("SBOM metadata.timestamp is missing")
    else:
        try:
            generated = int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp())
        except ValueError:
            errors.append(f"SBOM metadata.timestamp is invalid: {timestamp}")
        else:
            if generated < tag_timestamp:
                errors.append(
                    f"SBOM generated before tag (SBOM: {generated}, tag: {tag_timestamp})"
                )

    components: list[object] = []
    metadata_component = metadata.get("component")
    if isinstance(metadata_component, dict):
        components.append(metadata_component)
    listed_components = payload.get("components")
    if isinstance(listed_components, list):
        components.extend(listed_components)
    else:
        errors.append("SBOM components must be a list")

    has_release_component = any(
        isinstance(component, dict)
        and _normalized_distribution_name(component.get("name")) == "general-ludd-agent"
        and component.get("version") == version
        for component in components
    )
    if not has_release_component:
        errors.append(f"SBOM does not identify general-ludd-agent {version}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    tag = args[0] if args else os.environ.get("TAG", "")
    repository = os.environ.get("GLUDD_GITHUB_REPOSITORY", DEFAULT_REPOSITORY)
    if not tag:
        print("AC007: INCONCLUSIVE — TAG required")
        return 2

    tag_timestamp = get_tag_timestamp(tag)
    if tag_timestamp == 0:
        print(f"AC007: INCONCLUSIVE — cannot get timestamp for tag {tag}")
        return 2

    content = get_sbom_content(tag, repository)
    if content is None:
        print(f"AC007: INCONCLUSIVE — cannot read published {SBOM_ASSET_NAME} for {tag}")
        return 2
    try:
        payload: object = json.loads(content)
    except json.JSONDecodeError as exc:
        print(f"AC007: FAIL — {SBOM_ASSET_NAME} is invalid JSON: {exc}")
        return 1

    errors = validate_sbom(payload, tag.removeprefix("v"), tag_timestamp)
    for error in errors:
        print(f"AC007: FAIL — {error}")
    if errors:
        print(f"AC007: FAIL — {len(errors)} SBOM freshness error(s)")
        return 1
    print(f"AC007: PASS — published {SBOM_ASSET_NAME} is fresh and version-correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
