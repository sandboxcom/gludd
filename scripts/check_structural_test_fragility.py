"""check_structural_test_fragility.py — AA058 enforcement.

Identify tests that read source files as plaintext and flag them for migration
to behavioral tests. Structural tests check content strings, not runtime behavior,
and break en masse when source code is refactored.

Exit 0 if fragility is within threshold, exit 1 if excessive structural tests found.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "tests"

PLAINTEXT_READ_PATTERNS = [
    r"\.read_text\(\)",
    r"open\([^)]*\.ts",
    r"open\([^)]*\.py.*\)",
    r"Path\([^)]+\)\.read_text\(\)",
    r"grep\s+-[A-Za-z]*['\"].*\.ts",
]

STRUCTURAL_ASSERT_PATTERNS = [
    r"assert.*in.*read_text",
    r"assert.*\.ts",
    r"assert.*'enforce-",
    r"assert.*\"enforce-",
    r"assert.*FLOOR\s*=\s*\d+",
    r"assert.*MIN_DISPATCHES",
]


def _scan_file(filepath: Path) -> list[dict]:
    """Scan a test file for structural-test patterns."""
    findings: list[dict] = []
    try:
        content = filepath.read_text()
    except Exception:
        return findings

    for i, line in enumerate(content.split("\n"), 1):
        for pat in PLAINTEXT_READ_PATTERNS:
            if re.search(pat, line):
                findings.append(
                    {
                        "file": str(filepath.relative_to(ROOT)),
                        "line": i,
                        "pattern": pat,
                        "type": "plaintext_read",
                    }
                )
                break

    for i, line in enumerate(content.split("\n"), 1):
        for pat in STRUCTURAL_ASSERT_PATTERNS:
            if re.search(pat, line):
                findings.append(
                    {
                        "file": str(filepath.relative_to(ROOT)),
                        "line": i,
                        "pattern": pat,
                        "type": "structural_assert",
                    }
                )
                break

    return findings


def main() -> int:
    fragility_threshold = 100

    all_findings: list[dict] = []
    test_files = list(TEST_DIR.rglob("*.py"))

    for tf in test_files:
        findings = _scan_file(tf)
        all_findings.extend(findings)

    structural = [f for f in all_findings if f["type"] == "structural_assert"]
    plaintext = [f for f in all_findings if f["type"] == "plaintext_read"]

    print(f"Structural test fragility scan: {len(test_files)} test files")
    print(f"  Plaintext source reads: {len(plaintext)}")
    print(f"  Structural assertions on source: {len(structural)}")

    total = len(plaintext) + len(structural)

    if total > fragility_threshold:
        print(f"\nWARNING: {total} structural-test patterns found (threshold: {fragility_threshold})")
        print("These tests read source files as plaintext and will break on refactors.")
        by_file: dict[str, int] = {}
        for f in structural + plaintext:
            by_file[f["file"]] = by_file.get(f["file"], 0) + 1
        for fname, count in sorted(by_file.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:>3}  {fname}")

    if total <= fragility_threshold:
        print(f"\nPASS: structural-test patterns ({total}) within threshold ({fragility_threshold})")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
