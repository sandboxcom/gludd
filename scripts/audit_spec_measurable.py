"""audit_spec_measurable.py — AB005 enforcement.

Check that each behavioral spec includes a measurable threshold/outcome.
A spec without a measurable outcome is flagged as DRAFT and doesn't count
toward the spec target.

Exit 0 if all specs have measurable outcomes; exit 1 with violations listed.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"

MEASURABLE_INDICATORS = [
    r"\b\d+\s+(?:seconds?|minutes?|hours?|days?|times?|pushes?|checks?|commits?|artifacts?)\b",
    r"\bno more than\b",
    r"\bat least\b",
    r"\bmaximum\b",
    r"\bminimum\b",
    r"\bthreshold\b",
    r"\bcap(?:ped)?\s+(?:at|of)\b",
    r"\b(capped|limited|restricted)\s+(?:at|to)\b",
    r"\bper\s+(?:session|cycle|hour|day|push|CI)\b",
    r"\bBLOCKED\b",
    r"\bDENIED\b",
    r"\bNOT SHIPPED\b",
    r"\bABORT(?:ED)?\b",
    r"\bFORBIDDEN\b",
    r"\bMUST NOT\b",
    r"\bMUST\b.*\bbefore\b",
    r"\bREQUIRED\b",
    r"\bevery\s+\d+\s+(?:specs?|minutes?|seconds?)\b",
    r"\bexit\s+code\b",
    r"\bmake\s+[\w-]+\b",
    r"\bplugin\b.*\bblocks?\b",
]


def parse_specs(text: str) -> list[tuple[str, str, str]]:
    """Parse specs into (id, title, body) tuples."""
    specs = []
    pattern = re.compile(r"^### (AB\d{3}|AA\d{3}) — (.+)$", re.MULTILINE)
    for m in pattern.finditer(text):
        spec_id = m.group(1)
        title = m.group(2)
        start = m.end()
        next_m = pattern.search(text, start)
        end = next_m.start() if next_m else len(text)
        body = text[start:end].strip()
        specs.append((spec_id, title, body))
    return specs


def has_measurable_outcome(body: str) -> bool:
    """Check if spec body contains at least one measurable threshold."""
    for indicator in MEASURABLE_INDICATORS:
        if re.search(indicator, body, re.IGNORECASE):
            return True
    return False


def main() -> int:
    if not SPECS_FILE.exists():
        print(f"ERROR: {SPECS_FILE} not found")
        return 1

    text = SPECS_FILE.read_text(encoding="utf-8")
    specs = parse_specs(text)

    violations: list[str] = []
    for spec_id, title, body in specs:
        if len(body) < 50:
            violations.append(f"{spec_id} ({title}): body too short ({len(body)} chars)")
            continue
        if not has_measurable_outcome(body):
            violations.append(f"{spec_id} ({title}): no measurable outcome found")

    if violations:
        print(f"AB005: {len(violations)} specs lack measurable outcomes:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(f"AB005: All {len(specs)} specs have measurable outcomes. PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
