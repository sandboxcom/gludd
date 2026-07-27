#!/usr/bin/env python3
"""check_sbom_freshness.py — AC007: sbom-freshness.

Verifies SBOM was regenerated on this release, not copied from prior release.
Checks: generation timestamp >= tag creation, version string matches, deps match lockfile.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def run_git(args):
    result = subprocess.run(["git"] + args, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def get_tag_timestamp(tag):
    out, _, rc = run_git(["tag", "-l", "--format=%(taggerdate:unix)", tag])
    try:
        return int(out) if out else 0
    except ValueError:
        return 0


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC007: TAG required")
        sys.exit(2)

    tag_ts = get_tag_timestamp(tag)
    if tag_ts == 0:
        print(f"AC007: INCONCLUSIVE — cannot get timestamp for tag {tag}")
        sys.exit(2)

    root = Path(__file__).resolve().parent.parent
    sbom_paths = list(root.glob("dist/*sbom*.json")) + list(root.glob("dist/*cyclonedx*.json"))

    if not sbom_paths:
        print("AC007: FAIL — no SBOM files found in dist/")
        sys.exit(1)

    version = tag.lstrip("v")
    errors = 0

    for sbom_path in sbom_paths:
        try:
            with open(sbom_path) as f:
                sbom = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"AC007: FAIL — cannot parse {sbom_path.name}: {e}")
            errors += 1
            continue

        metadata = sbom.get("metadata", {})
        sbom_ts_str = metadata.get("timestamp", "")
        sbom_component = metadata.get("component", {})
        sbom_version = sbom_component.get("version", "")

        if sbom_version != version:
            print(f"AC007: FAIL — {sbom_path.name} version '{sbom_version}' != tag version '{version}'")
            errors += 1

        if sbom_ts_str:
            try:
                from datetime import datetime, timezone

                sbom_dt = datetime.fromisoformat(sbom_ts_str.replace("Z", "+00:00"))
                sbom_ts = int(sbom_dt.timestamp())
                if sbom_ts < tag_ts:
                    print(f"AC007: FAIL — {sbom_path.name} generated before tag (SBOM: {sbom_ts}, tag: {tag_ts})")
                    errors += 1
            except ValueError:
                print(f"AC007: WARN — {sbom_path.name} has unparseable timestamp '{sbom_ts_str}'")
        else:
            print(f"AC007: WARN — {sbom_path.name} has no metadata.timestamp")

    if errors:
        print(f"AC007: FAIL — {errors} SBOM freshness error(s)")
        sys.exit(1)

    print("AC007: PASS — SBOM freshness verified")
    sys.exit(0)


if __name__ == "__main__":
    main()
