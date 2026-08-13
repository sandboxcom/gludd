"""check_spec_enforcement_coverage.py — AA046 enforcement.

Verify that at least 90% of behavioral specs in BEHAVIORAL_SPECS.md have
corresponding enforcement code (Makefile target, plugin function, AGENTS.md
section, or script). Exit 0 on >=90% coverage, exit 1 otherwise.
"""

import re
import sys
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
MAKEFILE = ROOT / "Makefile"
AGENTS_FILE = ROOT / "AGENTS.md"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
SCRIPTS_DIR = ROOT / "scripts"

COVERAGE_THRESHOLD = 0.90

ENFORCEMENT_PATTERNS = [
    r"make\s+([\w\-]+)",
    r"enforce-[\w\-]+\.ts",
    r"scripts/[\w_/]+\.py",
    r"AGENTS\.md",
    r"Makefile",
]


class SpecRecord(TypedDict):
    """Structured behavioral-spec fields used by the coverage audit."""

    id: str
    title: str
    enforcement: str
    behavior: str


def _parse_specs() -> list[SpecRecord]:
    """Parse BEHAVIORAL_SPECS.md into a list of spec dicts."""
    text = SPECS_FILE.read_text()
    specs: list[SpecRecord] = []
    current: SpecRecord | None = None
    in_enforcement = False
    in_behavior = False

    for line in text.split("\n"):
        m = re.match(r"^### ([A-Z]+\d+) — (.+)$", line)
        if m:
            if current and current.get("id"):
                specs.append(current)
            current = {"id": m.group(1), "title": m.group(2), "enforcement": "", "behavior": ""}
            in_enforcement = False
            in_behavior = False
            continue

        if not current:
            continue

        if line.startswith("**Enforcement:**"):
            in_enforcement = True
            in_behavior = False
            current["enforcement"] = line.replace("**Enforcement:**", "").strip()
            continue

        if line.startswith("**Behavior:**"):
            in_behavior = True
            in_enforcement = False
            current["behavior"] = line.replace("**Behavior:**", "").strip()
            continue

        if line.startswith("**Test:**"):
            in_enforcement = False
            in_behavior = False
            continue

        if in_enforcement and line.strip():
            current["enforcement"] += " " + line.strip()

        if in_behavior and line.strip():
            current["behavior"] += " " + line.strip()

    if current and current.get("id"):
        specs.append(current)

    return specs


def _has_template_filler(enforcement: str) -> bool:
    """Check if enforcement text is template filler, not real code."""
    filler_patterns = [
        r"\b(planned|TODO|TBD|not yet|future|upcoming)\b",
        r"^\s*$",
    ]
    return any(re.search(p, enforcement, re.IGNORECASE) for p in filler_patterns)


def _enforcement_exists(enforcement: str) -> bool:
    """Check if the enforcement mechanism actually exists in the repo.
    For compound claims (e.g. script + make target), ANY resolved part counts."""
    if not enforcement or _has_template_filler(enforcement):
        return False

    lower = enforcement.lower()
    if "agents.md" in lower and AGENTS_FILE.exists():
        return True

    # Try ALL patterns — any resolved part makes the spec covered.
    # Don't short-circuit on the first unrecognized claim.

    for m_make in re.finditer(r"`make\s+([\w\-]+)`", enforcement):
        target = m_make.group(1)
        content = MAKEFILE.read_text()
        if re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE):
            return True

    for m_script in re.finditer(r"`(scripts/[\w_/]+\.py)`", enforcement):
        script_path = ROOT / m_script.group(1)
        if script_path.exists():
            return True

    for m_plugin in re.finditer(r"`(enforce-[\w\-]+\.ts)`", enforcement):
        plugin_path = PLUGIN_DIR / m_plugin.group(1)
        if plugin_path.exists():
            return True

    for m_makefile in re.finditer(r"Makefile\s+`([\w\-]+)`", enforcement):
        target = m_makefile.group(1)
        content = MAKEFILE.read_text()
        if re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE):
            return True

    # Also check if enforcement claims a Makefile target without the Makefile prefix
    # This catches legacy format where the target is referenced but not in our standard syntax.
    # We don't check here — the fixer handles format conversion.

    return False


def main() -> int:
    if not SPECS_FILE.exists():
        print(f"ERROR: {SPECS_FILE} not found")
        return 1

    specs = _parse_specs()
    total = len(specs)
    if total == 0:
        print("ERROR: no specs parsed from BEHAVIORAL_SPECS.md")
        return 1

    covered = sum(1 for s in specs if _enforcement_exists(s["enforcement"]))
    ratio = covered / total

    uncovered = [s for s in specs if not _enforcement_exists(s["enforcement"])]

    print(f"Spec enforcement coverage: {covered}/{total} = {ratio:.1%}")
    print(f"Threshold: {COVERAGE_THRESHOLD:.0%}")

    if uncovered:
        print(f"\n{len(uncovered)} specs lack enforcement:")
        for s in uncovered:
            enf = s["enforcement"][:80] if s["enforcement"] else "(empty)"
            print(f"  {s['id']}: {enf}")

    if ratio >= COVERAGE_THRESHOLD:
        print(f"\nPASS: {ratio:.1%} >= {COVERAGE_THRESHOLD:.0%}")
        return 0
    else:
        print(f"\nFAIL: {ratio:.1%} < {COVERAGE_THRESHOLD:.0%}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
