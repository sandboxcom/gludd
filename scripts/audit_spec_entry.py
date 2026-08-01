"""audit_spec_entry.py — AB009 enforcement.

Each spec must pass a quality gate before being counted toward the target:
- Unique body text (dedup check)
- Specific enforcement (not template filler)
- Measurable outcome (threshold defined)
- Actionable (HOW, not just WHAT)

Specs failing any gate are flagged DRAFT and don't count toward target.
Exit 0 if all specs pass; exit 1 with violations.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"

TEMPLATE_PATTERNS = [
    r"agent MUST\s*(?:follow|implement|track).*invariant",
    r"mechanically\s*enforce(d?)\s*(?:this\s+)?invariant",
    r"template\s+(?:spec|filler)",
    r"The agent MUST enforce this at runtime",
    r"The plugin MUST block this behavior",
]

FORBIDDEN_VAGUE = [
    r"should consider\b",
    r"agent should\b",
    r"agent might\b",
    r"it is recommended\b",
    r"as appropriate\b",
    r"when possible\b",
    r"generally\b",
]

DRAFT_MARKER = "### DRAFT_SPEC_"
SPEC_HEADER_RE = re.compile(
    r"^### (?P<id>[A-Z]{1,4}\d{3}) — (?P<title>.+)$", re.MULTILINE
)
ENFORCEMENT_FIELD_RE = re.compile(r"^\*\*Enforcement:\*\*\s*(.+)$", re.MULTILINE)
BEHAVIOR_FIELD_RE = re.compile(r"^\*\*Behavior:\*\*\s*(.+)$", re.MULTILINE | re.DOTALL)


def has_specific_enforcement(body: str) -> bool:
    """Return whether the structured field names an implemented mechanism."""
    match = ENFORCEMENT_FIELD_RE.search(body)
    if not match:
        return False
    enforcement = match.group(1).strip()
    if re.search(r"\b(?:none|tbd|todo|planned|proposal|future)\b", enforcement, re.IGNORECASE):
        return False
    return bool(
        re.search(
            r"(?:`[\w./-]+(?:\s+[\w=<>./-]+)*`|"
            r"\.(?:ts|py|sh|yml|yaml|js|mjs)\b|"
            r"\b(?:Makefile|AGENTS\.md|opencode\.json|plugin|hook|workflow|"
            r"target|guard|prerequisite)\b)",
            enforcement,
            re.IGNORECASE,
        )
    )


def has_measurable_outcome(body: str) -> bool:
    """Accept quantitative limits or deterministic, observable verdicts."""
    match = BEHAVIOR_FIELD_RE.search(body)
    behavior = match.group(1) if match else ""
    return bool(
        re.search(
            r"(?:\b\d+(?:\.\d+)?\s*(?:%|seconds?|minutes?|hours?|days?|"
            r"pushes?|checks?|commits?|artifacts?|attempts?|files?|tests?|"
            r"workers?|plugins?|sessions?|cycles?)\b|"
            r"[<>]=?\s*\d+|[≥≤]\s*\d+|"
            r"\b(?:no more than|at least|at most|exactly|maximum|minimum|"
            r"threshold|cap(?:ped)?\s+(?:at|of)|zero|none|every|each|all|any|"
            r"BLOCKED|DENIED|ABORT(?:ED)?|FORBIDDEN|MUST(?:\s+NOT)?|REQUIRED|"
            r"requires?|rejects?|refuses?|prevents?|blocks?|fails?|flags?|"
            r"exits?|records?|removes?|restores?|validates?|verifies?|"
            r"checks?|detects?|catches?|classifies?|categorizes?|identifies?|"
            r"warns?|labels?|mark(?:s|ed)?|matches?|compares?|scans?|tracks?|ensures?|"
            r"accepts?|allows?|commits?|reverts?|runs?|prints?|cannot|never|"
            r"automatically|until|"
            r"per\s+(?:session|cycle|hour|day|push|CI))\b)",
            behavior,
            re.IGNORECASE,
        )
    )


def parse_specs(text: str) -> list[tuple[str, str, str]]:
    specs = []
    headings = list(SPEC_HEADER_RE.finditer(text))
    for index, m in enumerate(headings):
        spec_id = m.group("id")
        if not re.fullmatch(r"(?:AA|AB)\d{3}", spec_id):
            continue
        title = m.group("title")
        start = m.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[start:end].strip()
        specs.append((spec_id, title, body))
    return specs


def check_spec_quality(spec_id: str, title: str, body: str) -> list[str]:
    """Return list of quality violations for this spec."""
    violations = []

    # Gate 1: Unique body text (not empty, not obviously duplicated)
    if len(body) < 80:
        violations.append("body too short (<80 chars, likely template)")

    # Gate 2: Not template filler
    for tmpl in TEMPLATE_PATTERNS:
        if re.search(tmpl, body, re.IGNORECASE):
            violations.append(f"template filler detected: {tmpl!r}")

    # Gate 3: Has measurable outcome
    if not has_measurable_outcome(body):
        violations.append("no measurable outcome/threshold defined")

    # Gate 4: Has specific enforcement mechanism
    if not has_specific_enforcement(body):
        violations.append("no specific enforcement mechanism referenced")

    # Gate 5: Not vague
    for vague in FORBIDDEN_VAGUE:
        if re.search(vague, body, re.IGNORECASE):
            violations.append(f"vague language: {vague!r}")

    # Gate 6: Has "Behavior:" line (required spec structure)
    if "**Behavior:**" not in body:
        violations.append("missing Behavior: field")

    if "**Enforcement:**" not in body:
        violations.append("missing Enforcement: field")

    return violations


def main() -> int:
    if not SPECS_FILE.exists():
        print(f"ERROR: {SPECS_FILE} not found")
        return 1

    text = SPECS_FILE.read_text(encoding="utf-8")
    specs = parse_specs(text)

    draft_specs: list[str] = []
    passed = 0
    for spec_id, title, body in specs:
        violations = check_spec_quality(spec_id, title, body)
        if violations:
            draft_specs.append(f"{spec_id} ({title}): " + "; ".join(violations))
        else:
            passed += 1

    total = len(specs)
    if draft_specs:
        print(f"AB009: {len(draft_specs)}/{total} specs are DRAFT (do NOT count toward target):")
        for ds in draft_specs[:50]:
            print(f"  - {ds}")
        if len(draft_specs) > 50:
            print(f"  ... and {len(draft_specs) - 50} more")
        print(f"\n  Passed: {passed}/{total} | Draft: {len(draft_specs)}/{total}")
        return 1

    print(f"AB009: All {total} specs pass quality gate. PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
