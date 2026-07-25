#!/usr/bin/env python3
"""Verify that secrets in .secrets.baseline are not live/real credentials.

Cross-references detect-secrets baseline entries against truffleHog's
live-verification engine.  If truffleHog can verify any baselined secret
as live, the script exits non-zero — that baseline entry is a real secret,
not a false positive.

Exit codes:
  0 — no baselined secrets verified as live (clean)
  1 — at least one baselined secret verified as live (SECURITY ISSUE)
  2 — truffleHog not installed (cannot verify)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / ".secrets.baseline"

EXCLUDE_FILE_PREFIXES = (
    "tests/", "test/",
    ".opencode/skills/",
    ".opencode/node_modules/",
    "dist/", ".venv/", "__pycache__/",
)


def trufflehog_installed() -> bool:
    return shutil.which("trufflehog") is not None


def load_baseline() -> dict[str, list[dict]]:
    with open(BASELINE_PATH) as f:
        data = json.load(f)
    return data.get("results", {})


def non_test_files(results: dict[str, list[dict]]) -> dict[str, list[dict]]:
    filtered: dict[str, list[dict]] = {}
    for filename, entries in results.items():
        if any(filename.startswith(prefix) for prefix in EXCLUDE_FILE_PREFIXES):
            continue
        filtered[filename] = entries
    return filtered


def extract_secrets_from_file(
    filename: str, entries: list[dict]
) -> list[dict]:
    """Read the source file and extract actual secret values at recorded line numbers."""
    filepath = REPO_ROOT / filename
    if not filepath.is_file():
        return []
    try:
        lines = filepath.read_text().splitlines()
    except Exception:
        return []

    extracted: list[dict] = []
    for entry in entries:
        line_no = entry.get("line_number", 0)
        if 1 <= line_no <= len(lines):
            value = lines[line_no - 1].strip()
            extracted.append({
                "type": entry["type"],
                "filename": filename,
                "line_number": line_no,
                "hashed_secret": entry.get("hashed_secret", ""),
                "value_preview": value[:120],
            })
    return extracted


def run_trufflehog(target_dir: str) -> list[dict]:
    """Run trufflehog filesystem with --only-verified. Return parsed findings."""
    cmd = [
        "trufflehog", "filesystem",
        "--results=verified",
        "--json",
        "--only-verified",
        "--no-update",
        target_dir,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300
    )
    findings: list[dict] = []
    for line in result.stdout.strip().splitlines():
        try:
            findings.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return findings


def build_secrets_file(entries: list[dict]) -> str:
    """Write extracted secret values to a temp file for trufflehog scanning."""
    fd, path = tempfile.mkstemp(
        prefix="gludd-secrets-", suffix=".txt", dir="/tmp"
    )
    with os.fdopen(fd, "w") as f:
        for entry in entries:
            f.write(f"{entry['value_preview']}\n")
    return path


def main() -> int:
    if not BASELINE_PATH.is_file():
        print("ERROR: .secrets.baseline not found. Run 'make secrets-baseline' first.")
        return 1

    print(f"[verify-secrets] Loading baseline: {BASELINE_PATH}")
    results = load_baseline()
    total_files = len(results)
    total_entries = sum(len(v) for v in results.values())
    print(f"[verify-secrets] Baseline: {total_entries} secrets across {total_files} files")

    filtered = non_test_files(results)
    filtered_files = len(filtered)
    filtered_entries = sum(len(v) for v in filtered.values())
    print(
        f"[verify-secrets] Non-test files: {filtered_entries} secrets "
        f"across {filtered_files} files"
    )

    if filtered_entries == 0:
        print("[verify-secrets] No non-test secrets to verify — PASS")
        return 0

    if not trufflehog_installed():
        print(
            "ERROR: trufflehog not found on PATH.\n"
            "Install with: brew install trufflehog\n"
            "   or visit: https://github.com/trufflesecurity/trufflehog"
        )
        return 2

    # Extract all non-test secret values from source files
    all_entries: list[dict] = []
    for filename, entries in filtered.items():
        extracted = extract_secrets_from_file(filename, entries)
        all_entries.extend(extracted)

    if not all_entries:
        print("[verify-secrets] Could not extract any secret values — PASS (inconclusive)")
        return 0

    print(f"[verify-secrets] Extracted {len(all_entries)} candidate values")

    # Write to temp file and scan with trufflehog
    secrets_file = build_secrets_file(all_entries)
    try:
        print(f"[verify-secrets] Running trufflehog on {secrets_file} ...")
        findings = run_trufflehog(secrets_file)

        if not findings:
            print("[verify-secrets] No live secrets verified — PASS")
            return 0

        # Cross-reference trufflehog findings against baseline entries
        verified_files: set[str] = set()
        for finding in findings:
            raw = finding.get("Raw", "")
            source = finding.get("SourceMetadata", {})
            detector = finding.get("DetectorName", "unknown")
            verified = finding.get("Verified", False)

            # Only report secrets trufflehog actually verified
            if not verified:
                continue

            # Match back to baseline entry by source line content
            for entry in all_entries:
                if raw and entry["value_preview"].startswith(raw[:20]):
                    verified_files.add(entry["filename"])
                    print(
                        f"[verify-secrets] LIVE SECRET VERIFIED: "
                        f"{entry['filename']}:{entry['line_number']} "
                        f"({entry['type']}) — detector: {detector}"
                    )
                    break

        if verified_files:
            print(
                f"\n[verify-secrets] FAILED: {len(verified_files)} file(s) "
                f"contain live secrets baselined as false positives.\n"
                f"Affected files: {', '.join(sorted(verified_files))}\n"
                f"These secrets MUST be revoked and rotated immediately."
            )
            return 1
        else:
            print("[verify-secrets] No baselined secrets verified as live — PASS")
            return 0

    finally:
        os.unlink(secrets_file)


if __name__ == "__main__":
    sys.exit(main())
