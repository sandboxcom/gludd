#!/usr/bin/env python3
"""check_provenance_attestation.py — AC011: provenance-attestation.

Verifies SLSA provenance attestation exists for release artifacts.
"""

import json
import os
import subprocess
import sys


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

    provenance_patterns = [".build.provenance", "provenance.json", "attestation.json", ".intoto.jsonl"]
    binary_assets = [
        a
        for a in assets
        if any(p in a.get("name", "").lower() for p in ["linux", "macos", "windows", "binary", "gludd"])
        and "provenance" not in a.get("name", "").lower()
    ]

    provenance_assets = [a for a in assets if any(p in a.get("name", "").lower() for p in provenance_patterns)]

    if not provenance_assets:
        print("AC011: FAIL — no provenance attestation found in release assets")
        sys.exit(1)

    print(f"AC011: PASS — {len(provenance_assets)} provenance attestation(s) found")
    if binary_assets:
        print(f"AC011: INFO — {len(binary_assets)} binary assets, {len(provenance_assets)} attestations")
    sys.exit(0)


if __name__ == "__main__":
    main()
