#!/usr/bin/env python3
"""check_release_audit_trail.py — AC020: release-audit-trail.

Verifies every release has a complete audit trail JSON file.
Records: tag SHA, CI run, artifacts, timestamp, gate status, changelog, key, operator.
"""

import json
import os
import sys
from pathlib import Path


REQUIRED_FIELDS = [
    "tag",
    "tag_sha",
    "ci_run_id",
    "ci_conclusion",
    "artifacts",
    "release_cut_timestamp",
    "gate_status",
    "changelog_range",
    "signing_key_fingerprint",
    "operator",
]


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    root = Path(__file__).resolve().parent.parent
    audit_dir = root / "docs" / "releases"

    if not tag:
        all_audits = sorted(audit_dir.glob("audit-*.json")) if audit_dir.exists() else []
        if not all_audits:
            print("AC020: FAIL — no audit files found in docs/releases/")
            sys.exit(1)

        errors = 0
        for audit_path in all_audits:
            try:
                with open(audit_path) as f:
                    data = json.load(f)
                missing = [f for f in REQUIRED_FIELDS if f not in data or data[f] is None]
                if missing:
                    print(f"AC020: FAIL — {audit_path.name} missing fields: {', '.join(missing)}")
                    errors += 1
            except (json.JSONDecodeError, OSError) as e:
                print(f"AC020: FAIL — {audit_path.name}: {e}")
                errors += 1

        if errors:
            print(f"AC020: FAIL — {errors} audit file error(s)")
            sys.exit(1)
        print(f"AC020: PASS — {len(all_audits)} audit files")
        sys.exit(0)

    version = tag.lstrip("v")
    audit_path = audit_dir / f"audit-{version}.json"

    if not audit_path.exists():
        print(f"AC020: FAIL — {audit_path} not found")
        sys.exit(1)

    try:
        with open(audit_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"AC020: FAIL — {audit_path}: {e}")
        sys.exit(1)

    missing = [f for f in REQUIRED_FIELDS if f not in data or data[f] is None]
    if missing:
        print(f"AC020: FAIL — audit trail for {tag} missing fields: {', '.join(missing)}")
        sys.exit(1)

    print(f"AC020: PASS — audit trail complete for {tag}")
    sys.exit(0)


if __name__ == "__main__":
    main()
