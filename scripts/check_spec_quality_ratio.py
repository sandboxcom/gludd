"""check_spec_quality_ratio.py — AB020 enforcement.

Verifies that ≥90% of specs have real enforcement code (pass audit-spec-entry).
Blocks new spec creation when the ratio is below threshold — agent must
upgrade existing specs with real enforcement before writing new ones.

Exit 0 if ratio ≥90%; exit 1 if below threshold.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"

SPEC_RE = re.compile(r"^### (A[AB]\d{3}) — (.+)$", re.MULTILINE)
ENFORCEMENT_RE = re.compile(r"\*\*Enforcement:\*\*\s*(.+)$", re.MULTILINE)

TARGET_THRESHOLD = 0.90
REAL_ENFORCEMENT_INDICATORS = [
    r"\.(?:ts|py|sh|yml|yaml|js|mjs)",
    r"`make\s+[\w-]+`",
    r"AGENTS\.md",
    r"opencode\.json",
    r"\.github/workflows/",
    r"Makefile\s+(?:target|guard|prerequisite)",
]


def has_real_enforcement(body: str) -> bool:
    """A spec has real enforcement if its Enforcement field references
    concrete files, targets, or policy sections."""
    enf_match = ENFORCEMENT_RE.search(body)
    if not enf_match:
        return False
    enf_text = enf_match.group(1)
    for indicator in REAL_ENFORCEMENT_INDICATORS:
        if re.search(indicator, enf_text, re.IGNORECASE):
            return True
    return False


def parse_specs(text: str) -> list[tuple[str, str]]:
    specs = []
    for m in SPEC_RE.finditer(text):
        start = m.end()
        next_m = SPEC_RE.search(text, start)
        end = next_m.start() if next_m else len(text)
        specs.append((m.group(1), text[start:end].strip()))
    return specs


def main() -> int:
    if not SPECS_FILE.exists():
        return 0

    text = SPECS_FILE.read_text(encoding="utf-8")
    specs = parse_specs(text)

    total = len(specs)
    with_enforcement = sum(1 for _, body in specs if has_real_enforcement(body))
    ratio = with_enforcement / total if total > 0 else 1.0

    if ratio < TARGET_THRESHOLD:
        print(
            f"AB020 SPEC-QUALITY-RATIO: {with_enforcement}/{total} specs "
            f"have real enforcement ({ratio:.1%}). "
            f"Threshold: {TARGET_THRESHOLD:.0%}. "
            f"BLOCKED — upgrade {int(total * TARGET_THRESHOLD) - with_enforcement} "
            f"more specs with real enforcement mechanisms before writing new specs."
        )
        return 1

    print(
        f"AB020: {with_enforcement}/{total} specs have real enforcement "
        f"({ratio:.1%}). Threshold: {TARGET_THRESHOLD:.0%}. PASS"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
