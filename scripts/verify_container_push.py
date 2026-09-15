#!/usr/bin/env python3
"""verify_container_push.py — AC008: container-push-verification.

Verifies container image exists in registry after push.
Tries skopeo, crane, docker in order.
"""

import json
import os
import re
import subprocess
import sys

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def image_digest_from_output(output: str) -> str | None:
    """Return one canonical manifest digest without trusting a mutable tag."""
    stripped = output.strip()
    if _DIGEST_RE.fullmatch(stripped) is not None:
        return stripped
    try:
        document = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return None

    candidates: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, member in value.items():
                if (
                    isinstance(key, str)
                    and key.casefold() == "digest"
                    and isinstance(member, str)
                    and _DIGEST_RE.fullmatch(member) is not None
                ):
                    candidates.add(member)
                visit(member)
        elif isinstance(value, list):
            for member in value:
                visit(member)

    visit(document)
    if len(candidates) != 1:
        return None
    return next(iter(candidates))


def try_skopeo(image: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["skopeo", "inspect", f"docker://{image}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "skopeo unavailable"


def try_crane(image: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["crane", "digest", image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "crane unavailable"


def try_docker(image: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                "--format",
                "{{json .Manifest}}",
                image,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "docker unavailable"


def main() -> None:
    image = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("IMAGE", "")
    tag = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TAG", "")
    if not image and not tag:
        print("AC008: IMAGE or TAG required")
        sys.exit(2)
    if not image and tag:
        print("AC008: IMAGE required (got TAG only)")
        sys.exit(2)

    tool_available = False
    for name, fn in [("skopeo", try_skopeo), ("crane", try_crane), ("docker", try_docker)]:
        ok, output = fn(image)
        if "unavailable" not in output:
            tool_available = True
        if ok:
            digest = image_digest_from_output(output)
            if digest is not None:
                print(f"AC008: PASS — image {image} verified via {name} digest={digest}")
                sys.exit(0)
        if "unavailable" in output:
            continue

    if not tool_available:
        print("AC008: INCONCLUSIVE — no container inspection tool available (tried skopeo, crane, docker)")
        print("AC008: Install skopeo, crane, or Docker Buildx for container push verification")
        sys.exit(2)
    print(f"AC008: FAIL — image {image} did not resolve to one canonical manifest digest")
    sys.exit(1)


if __name__ == "__main__":
    main()
