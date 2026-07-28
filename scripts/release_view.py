#!/usr/bin/env python3
"""Render a GitHub release without masking lookup or JSON failures."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import cast

DEFAULT_REPO = "sandboxcom/gludd"
JSON_FIELDS = (
    "tagName,name,isDraft,isPrerelease,publishedAt,url,assets"
)


def _run(cmd: list[str]) -> tuple[int, str, str]:
    """Run a subprocess and convert execution failures into fail-closed results."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        return 1, "", str(exc)
    except subprocess.TimeoutExpired:
        return 1, "", f"timed out running {cmd[0]}"
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _validated_release(
    payload: object,
    expected_tag: str,
) -> tuple[dict[str, object] | None, str | None]:
    """Validate every field used by the renderer before printing a success view."""
    if not isinstance(payload, dict):
        return None, "top-level value must be an object"
    data = cast(dict[str, object], payload)

    tag = data.get("tagName")
    if not isinstance(tag, str) or not tag:
        return None, "tagName must be a non-empty string"
    if tag != expected_tag:
        return None, f"tagName mismatch: expected {expected_tag!r}, got {tag!r}"

    for key in ("url", "publishedAt"):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            return None, f"{key} must be a non-empty string"

    for key in ("isDraft", "isPrerelease"):
        if not isinstance(data.get(key), bool):
            return None, f"{key} must be a boolean"

    assets = data.get("assets")
    if not isinstance(assets, list):
        return None, "assets must be a list"
    for index, asset_value in enumerate(assets):
        if not isinstance(asset_value, dict):
            return None, f"assets[{index}] must be an object"
        asset = cast(dict[str, object], asset_value)
        name = asset.get("name")
        size = asset.get("size")
        if not isinstance(name, str) or not name:
            return None, f"assets[{index}].name must be a non-empty string"
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            return None, f"assets[{index}].size must be a non-negative integer"

    return data, None


def _failure(message: str) -> int:
    print(f"ERROR: release-view failed: {message}", file=sys.stderr)
    print("RELEASE VIEW: FAIL (fail-closed)", file=sys.stderr)
    return 1


def view_release(tag: str, repo: str) -> int:
    """Query and display one release; return non-zero on every uncertain state."""
    rc, stdout, stderr = _run(
        [
            "gh",
            "release",
            "view",
            tag,
            "-R",
            repo,
            "--json",
            JSON_FIELDS,
        ]
    )
    if rc != 0:
        return _failure(stderr or stdout or "gh returned non-zero with no output")

    try:
        payload: object = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return _failure(f"invalid JSON from gh: {exc}")

    release, schema_error = _validated_release(payload, tag)
    if release is None:
        return _failure(f"invalid JSON schema from gh: {schema_error}")

    assets = cast(list[dict[str, object]], release["assets"])
    print(f"RELEASE: {release['tagName']} | {release['url']}")
    print(
        f"  draft={release['isDraft']} "
        f"prerelease={release['isPrerelease']} "
        f"published={release['publishedAt']}"
    )
    print(f"  ASSETS ({len(assets)}):")
    for asset in assets:
        print(f"   - {asset['name']} {asset['size']} bytes")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3) or not argv[1].strip():
        print(
            "Usage: python scripts/release_view.py <TAG> [owner/repo]",
            file=sys.stderr,
        )
        return 1
    tag = argv[1].strip()
    repo = argv[2].strip() if len(argv) == 3 else DEFAULT_REPO
    if not repo:
        return _failure("repository must not be empty")
    return view_release(tag, repo)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
