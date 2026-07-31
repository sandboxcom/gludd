#!/usr/bin/env python3
"""check_provenance_attestation.py — AC011: provenance-attestation.

Verifies SLSA provenance attestation exists for release artifacts.
"""

import json
import os
import subprocess
import sys

PROVENANCE_PATTERNS = [".build.provenance", "provenance.json", "attestation.json", ".intoto.jsonl"]


def check_provenance_attestation(assets):
    binary_assets = [
        a
        for a in assets
        if any(p in a.get("name", "").lower() for p in ["linux", "macos", "windows", "binary", "gludd"])
        and "provenance" not in a.get("name", "").lower()
    ]
    provenance_assets = [a for a in assets if any(p in a.get("name", "").lower() for p in PROVENANCE_PATTERNS)]
    passed = len(provenance_assets) > 0
    return passed, len(provenance_assets), len(binary_assets)


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC011: TAG required")
        sys.exit(2)

    try:
        result = subprocess.run(
            ["gh", "release", "view", tag, "--json", "assets"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("AC011: INCONCLUSIVE — gh CLI unavailable")
        sys.exit(2)

    if result.returncode != 0:
        print(f"AC011: INCONCLUSIVE — release '{tag}' not found")
        sys.exit(2)

    try:
        data = json.loads(result.stdout)
        assets = data.get("assets", [])
    except json.JSONDecodeError:
        print("AC011: INCONCLUSIVE — cannot parse gh output")
        sys.exit(2)

    passed, prov_count, bin_count = check_provenance_attestation(assets)

    if not passed:
        print("AC011: FAIL — no provenance attestation found in release assets")
        sys.exit(1)

    print(f"AC011: PASS — {prov_count} provenance attestation(s) found")
    if bin_count:
        print(f"AC011: INFO — {bin_count} binary assets, {prov_count} attestations")
    sys.exit(0)


if __name__ == "__main__":
    main()
