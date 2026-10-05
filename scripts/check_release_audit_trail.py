#!/usr/bin/env python3
"""check_release_audit_trail.py — AC020: release-audit-trail.

Verifies every release has a complete audit trail JSON file.
Records: tag SHA, CI run, artifacts, timestamp, gate status, changelog, key, operator.
"""

import json
import os
import re
import sys
from pathlib import Path

REQUIRED_FIELDS = [
    "tag",
    "tag_sha",
    "ci_run_id",
    "ci_conclusion",
    "artifacts",
    "artifact_count",
    "artifact_digest_index",
    "release_cut_timestamp",
    "gate_status",
    "changelog_range",
    "signing_key_fingerprint",
    "operator",
    "policy_compliant",
]

_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def _record_issue(issues: list[str], field: str) -> None:
    if field not in issues:
        issues.append(field)


def _has_unsigned_exception(data: dict[str, object]) -> bool:
    exceptions = data.get("policy_exceptions")
    if not isinstance(exceptions, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("control") == "AC017"
        and isinstance(item.get("detail"), str)
        and bool(item["detail"].strip())
        and isinstance(item.get("fix_forward"), str)
        and bool(item["fix_forward"].strip())
        for item in exceptions
    )


def get_audit_dir(script_root: str | Path | None = None) -> Path:
    root = Path(__file__).resolve().parent.parent if script_root is None else Path(script_root)
    return root / "docs" / "releases"


def validate_audit_entry(data: object) -> list[str]:
    """Return missing or malformed audit fields without claiming compliance."""
    if not isinstance(data, dict):
        return ["audit_object"]

    issues = [field for field in REQUIRED_FIELDS if field not in data or data[field] is None]

    tag = data.get("tag")
    if tag is not None and (not isinstance(tag, str) or not tag.startswith("v")):
        _record_issue(issues, "tag")

    tag_sha = data.get("tag_sha")
    if tag_sha is not None and (not isinstance(tag_sha, str) or _FULL_SHA_RE.fullmatch(tag_sha) is None):
        _record_issue(issues, "tag_sha")

    run_id = data.get("ci_run_id")
    if run_id is not None and (isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0):
        _record_issue(issues, "ci_run_id")
    if data.get("ci_conclusion") is not None and data.get("ci_conclusion") != "success":
        _record_issue(issues, "ci_conclusion")

    artifacts = data.get("artifacts")
    if artifacts is not None and (
        not isinstance(artifacts, list)
        or not artifacts
        or any(not isinstance(item, str) or not item.strip() for item in artifacts)
        or len(set(artifacts)) != len(artifacts)
    ):
        _record_issue(issues, "artifacts")
    artifact_count = data.get("artifact_count")
    if artifact_count is not None and (
        isinstance(artifact_count, bool)
        or not isinstance(artifact_count, int)
        or not isinstance(artifacts, list)
        or artifact_count != len(artifacts)
    ):
        _record_issue(issues, "artifact_count")
    digest_index = data.get("artifact_digest_index")
    if digest_index is not None and (not isinstance(digest_index, str) or not digest_index.strip()):
        _record_issue(issues, "artifact_digest_index")

    timestamp = data.get("release_cut_timestamp")
    if timestamp is not None and (
        not isinstance(timestamp, str) or _TIMESTAMP_RE.fullmatch(timestamp) is None
    ):
        _record_issue(issues, "release_cut_timestamp")
    if data.get("gate_status") is not None and data.get("gate_status") != "PASS":
        _record_issue(issues, "gate_status")
    changelog_range = data.get("changelog_range")
    if changelog_range is not None and (
        not isinstance(changelog_range, str)
        or not isinstance(tag, str)
        or not changelog_range.endswith(f"..{tag}")
    ):
        _record_issue(issues, "changelog_range")

    fingerprint = data.get("signing_key_fingerprint")
    if fingerprint == "UNSIGNED":
        if data.get("policy_compliant") is not False:
            _record_issue(issues, "policy_compliant")
        if not _has_unsigned_exception(data):
            _record_issue(issues, "policy_exceptions[AC017]")
    elif fingerprint is not None and (
        not isinstance(fingerprint, str)
        or re.fullmatch(r"[0-9A-F]{16,64}", fingerprint) is None
    ):
        _record_issue(issues, "signing_key_fingerprint")

    operator = data.get("operator")
    if operator is not None and (not isinstance(operator, str) or not operator.strip()):
        _record_issue(issues, "operator")
    return issues


def validate_audit_file(
    audit_path: str | Path,
    *,
    expected_tag: str | None = None,
) -> tuple[bool, str | None]:
    try:
        with open(audit_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return False, str(e)

    if not isinstance(data, dict):
        return False, "missing fields: audit_object"
    missing = validate_audit_entry(data)
    if missing:
        return False, f"missing fields: {', '.join(missing)}"
    if expected_tag is not None and data["tag"] != expected_tag:
        return False, f"tag does not match expected release: {data['tag']} != {expected_tag}"
    return True, None


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    audit_dir = get_audit_dir()

    if not tag:
        all_audits = sorted(audit_dir.glob("audit-*.json")) if audit_dir.exists() else []
        if not all_audits:
            print("AC020: FAIL — no audit files found in docs/releases/")
            sys.exit(1)

        errors = 0
        for audit_path in all_audits:
            expected_tag = f"v{audit_path.stem.removeprefix('audit-')}"
            ok, reason = validate_audit_file(audit_path, expected_tag=expected_tag)
            if not ok:
                print(f"AC020: FAIL — {audit_path.name}: {reason}")
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

    ok, reason = validate_audit_file(audit_path, expected_tag=tag)
    if not ok:
        print(f"AC020: FAIL — {audit_path}: {reason}")
        sys.exit(1)

    print(f"AC020: PASS — audit trail complete for {tag}")
    sys.exit(0)


if __name__ == "__main__":
    main()
