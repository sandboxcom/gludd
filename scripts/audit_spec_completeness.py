"""audit_spec_completeness.py — AA064 enforcement.

Check whether the agent's CURRENT behavior matches its written specs.
Detects recursive self-reference (writing specs about a behavior while
performing that behavior). Flags violations in real-time.

Exit 0 if no violations, exit 1 if violations found.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"


SPEC_BEHAVIORS = {
    "compulsive_ci_checking": {
        "pattern": r"\bci-verdict-safe\b",
        "spec_ids": ["AA003"],
    },
    "text_only_with_pending_work": {
        "pattern": r"text.only.*pending",
        "spec_ids": ["S01", "S02"],
    },
    "force_push_without_auth": {
        "pattern": r"GLUDD_FORCE_PUSH=1",
        "spec_ids": ["AA047"],
    },
    "batch_push_bypass": {
        "pattern": r"COMMIT_THRESHOLD=1",
        "spec_ids": ["AA067"],
    },
    "cooldown_bypass": {
        "pattern": r"FORCE=1.*ci-verdict",
        "spec_ids": ["AA068"],
    },
}


def _count_violations_in_file(path: Path) -> dict[str, int]:
    """Count how many times each spec's anti-pattern appears in a file."""
    if not path.exists():
        return {}
    content = path.read_text()
    counts: dict[str, int] = {}
    for name, info in SPEC_BEHAVIORS.items():
        matches = len(re.findall(info["pattern"], content))
        if matches > 0:
            counts[name] = matches
    return counts


def main() -> int:
    violations: list[str] = []

    for name, info in SPEC_BEHAVIORS.items():
        for spec_id in info["spec_ids"]:
            spec_text = _find_spec_text(spec_id)
            if not spec_text:
                continue

            if re.search(info["pattern"], spec_text):
                violations.append(
                    f"SELF-REFERENCE: spec {spec_id} ({name}) describes avoiding "
                    f"'{info['pattern']}' but this behavior appears IN the spec text itself"
                )

    agent_md = ROOT / "AGENTS.md"
    if agent_md.exists():
        agent_violations = _count_violations_in_file(agent_md)
        for name, count in agent_violations.items():
            if count > 10:
                violations.append(
                    f"SPEC-CODE MISMATCH: AGENTS.md contains {count} instances of "
                    f"pattern '{SPEC_BEHAVIORS[name]['pattern']}' while spec "
                    f"{SPEC_BEHAVIORS[name]['spec_ids']} forbids this behavior"
                )

    if violations:
        print(f"AUDIT: {len(violations)} spec-behavior violation(s):")
        for v in violations:
            print(f"  {v}")
        return 1

    print("AUDIT: PASS (no spec-behavior violations detected)")
    return 0


def _find_spec_text(spec_id: str) -> str | None:
    """Find the spec text for a given spec ID."""
    if not SPECS_FILE.exists():
        return None
    content = SPECS_FILE.read_text()
    pattern = rf"### {re.escape(spec_id)} — .+?\n(?=### |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return m.group(0) if m else None


if __name__ == "__main__":
    sys.exit(main())
