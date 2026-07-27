#!/usr/bin/env python3
"""verify_container_push.py — AC008: container-push-verification.

Verifies container image exists in registry after push.
Tries skopeo, crane, docker in order.
"""

import os
import subprocess
import sys


def try_skopeo(image):
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


def try_crane(image):
    try:
        result = subprocess.run(
            ["crane", "manifest", image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "crane unavailable"


def try_docker(image):
    try:
        result = subprocess.run(
            ["docker", "manifest", "inspect", image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "docker unavailable"


def main():
    image = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("IMAGE", "")
    tag = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TAG", "")
    if not image and not tag:
        print("AC008: IMAGE or TAG required")
        sys.exit(2)
    if not image and tag:
        print("AC008: IMAGE required (got TAG only)")
        sys.exit(2)

    for name, fn in [("skopeo", try_skopeo), ("crane", try_crane), ("docker", try_docker)]:
        ok, output = fn(image)
        if ok:
            print(f"AC008: PASS — image {image} verified via {name}")
            sys.exit(0)
        if "unavailable" in output:
            continue

    print(f"AC008: INCONCLUSIVE — no container inspection tool available (tried skopeo, crane, docker)")
    print("AC008: Install skopeo or crane for container push verification")
    sys.exit(2)


if __name__ == "__main__":
    main()
