#!/usr/bin/env python3
"""Audit features.yml: count features, verify 100% claims, check evidence quality."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

import yaml  # type: ignore

MANIFEST = _REPO_ROOT / "docs" / "features.yml"


def load_features() -> list[dict[str, Any]]:
    with open(MANIFEST) as f:
        data = yaml.safe_load(f)
    all_feats = []
    for section in data.get("sections", []):
        for feat in section.get("features", []):
            feat["_section_title"] = section.get("title", "")
            all_feats.append(feat)
    return all_feats


def extract_test_paths(evidence_refs: list[str]) -> list[tuple[str, str]]:
    """Return list of (raw_ref, test_file_path) for test: refs."""
    results = []
    for ref in evidence_refs:
        if ref.startswith("test:"):
            node_id = ref[len("test:"):]
            file_path = node_id.split("::")[0]
            results.append((ref, file_path))
    return results


def classify_evidence(evidence_refs: list[str]) -> str:
    """Classify evidence quality."""
    has_test = any(r.startswith("test:") for r in evidence_refs)
    has_file = any(r.startswith("file:") for r in evidence_refs)
    has_role = any(r.startswith("role:") for r in evidence_refs)
    has_module = any(r.startswith("module:") for r in evidence_refs)
    has_molecule = any(r.startswith("molecule:") for r in evidence_refs)
    has_other = any(r.startswith("file:TASKS") for r in evidence_refs)

    if not evidence_refs:
        return "NO_EVIDENCE"
    if has_test and (has_file or has_role or has_module):
        return "TEST_PLUS_CODE"
    if has_test:
        return "TEST_ONLY"
    if has_file or has_role or has_module:
        return "CODE_ONLY"
    if has_molecule:
        return "MOLECULE_ONLY"
    if has_other:
        return "TASKS_REF_ONLY"
    return "UNKNOWN"


def main():
    features = load_features()

    total = len(features)
    pct_100 = [f for f in features if f.get("pct") == 100]
    pct_0 = [f for f in features if f.get("pct") == 0]
    pct_local = [f for f in pct_100 if f.get("pct_note") == "(local)"]
    pct_no_evidence = [f for f in pct_100 if not f.get("evidence_refs")]

    print("=" * 70)
    print("FEATURES.YML AUDIT REPORT")
    print("=" * 70)
    print(f"\nTotal features: {total}")
    print(f"Features at 100%: {len(pct_100)}")
    print(f"Features at 0%:   {len(pct_0)}")
    print(f"Features at 100% with '(local)' note: {len(pct_local)}")
    print(f"Features at 100% with NO evidence_refs: {len(pct_no_evidence)}")

    # Check test file existence for all 100% features
    missing_tests = []
    found_tests = []
    for feat in pct_100:
        for ref, path in extract_test_paths(feat.get("evidence_refs", [])):
            full_path = _REPO_ROOT / path
            if full_path.exists():
                found_tests.append((feat["id"], path, feat["title"]))
            else:
                missing_tests.append((feat["id"], path, feat["title"]))

    print(f"\nTest file references in 100% features: {len(found_tests) + len(missing_tests)}")
    print(f"  Test files found: {len(found_tests)}")
    print(f"  Test files MISSING: {len(missing_tests)}")

    # Categorize by evidence type
    categories: dict[str, list[str]] = {}
    for feat in pct_100:
        cat = classify_evidence(feat.get("evidence_refs", []))
        categories.setdefault(cat, []).append(feat["id"])

    print("\n--- Evidence quality breakdown (100% features) ---")
    for cat, ids in sorted(categories.items()):
        print(f"  {cat}: {len(ids)}")

    # Detailed: CI-verified vs local-only vs false claims
    print("\n--- CI vs Local vs False Claims ---")
    ci_verified = []
    local_only = []
    false_claims = []
    code_only = []

    for feat in pct_100:
        fid = feat["id"]
        refs = feat.get("evidence_refs", [])
        pct_note = feat.get("pct_note", "")
        notes = feat.get("notes", "")

        has_ci = "CI-green" in notes or "CI green" in notes or "ci-green" in notes.lower()
        has_local = "(local)" in pct_note
        has_test_ref = any(r.startswith("test:") for r in refs)
        has_test_file_missing = any(
            not (_REPO_ROOT / path).exists()
            for _, path in extract_test_paths(refs)
        )
        has_no_evidence = not refs

        if has_no_evidence:
            false_claims.append((fid, "No evidence_refs", feat["title"]))
        elif has_test_file_missing:
            false_claims.append((fid, "Test file missing", feat["title"]))
        elif has_local:
            local_only.append((fid, "Molecule/role tests — local only", feat["title"]))
        elif has_ci:
            ci_verified.append((fid, notes[:80], feat["title"]))
        elif has_test_ref:
            local_only.append((fid, "Test exists but no CI evidence", feat["title"]))
        elif refs:
            code_only.append((fid, "No test ref — code/file only", feat["title"]))

    print(f"\n  CI-verified (notes mention CI-green): {len(ci_verified)}")
    print(f"  Local-only (no CI evidence): {len(local_only)}")
    print(f"  Code-only (no test ref at all): {len(code_only)}")
    print(f"  FALSE CLAIMS (no evidence or test missing): {len(false_claims)}")

    if false_claims:
        print("\n--- FALSE CLAIMS (100% with broken evidence) ---")
        for fid, reason, title in false_claims:
            print(f"  [{fid}] {reason}")
            print(f"    Title: {title}")

    if local_only:
        print("\n--- LOCAL-ONLY (no CI evidence) ---")
        for fid, reason, title in local_only:
            print(f"  [{fid}] {reason}")
            print(f"    Title: {title}")

    if code_only:
        print("\n--- CODE-ONLY (no test) ---")
        for fid, reason, title in code_only:
            print(f"  [{fid}] {reason}")
            print(f"    Title: {title}")

    if missing_tests:
        print("\n--- MISSING TEST FILES ---")
        for fid, path, title in missing_tests:
            print(f"  [{fid}] {path}")
            print(f"    Title: {title}")

    # Section breakdown
    print("\n--- Per-section breakdown ---")
    from collections import Counter
    section_counts: Counter[str] = Counter()
    section_100: Counter[str] = Counter()
    for feat in features:
        section = feat.get("_section_title", "(unknown)")
        section_counts[section] += 1
        if feat.get("pct") == 100:
            section_100[section] += 1

    for section, count in section_counts.most_common():
        h = section_100.get(section, 0)
        print(f"  {section}: {count} total, {h} at 100%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
