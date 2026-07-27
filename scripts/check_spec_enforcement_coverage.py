"""check_spec_enforcement_coverage.py — AA046 enforcement.

Verify that at least 90% of behavioral specs in BEHAVIORAL_SPECS.md have
corresponding enforcement code (Makefile target, plugin function, AGENTS.md
section, or script). Exit 0 on >=90% coverage, exit 1 otherwise.
"""

import re
import sys
from pathlib import Path

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


def _parse_specs() -> list[dict]:
    """Parse BEHAVIORAL_SPECS.md into a list of spec dicts."""
    text = SPECS_FILE.read_text()
    specs: list[dict] = []
    current: dict | None = None
    in_enforcement = False
    in_behavior = False

    for line in text.split("\n"):
        m = re.match(r"^### (A[A-Z]\d+) — (.+)$", line)
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
        r"(planned|TODO|TBD|not yet|future|upcoming)",
        r"^\s*$",
    ]
    return any(re.search(p, enforcement, re.IGNORECASE) for p in filler_patterns)


def _enforcement_exists(enforcement: str) -> bool:
    """Check if the enforcement mechanism actually exists in the repo."""
    if not enforcement or _has_template_filler(enforcement):
        return False

    lower = enforcement.lower()
    if "agents.md" in lower:
        return AGENTS_FILE.exists()

    m_make = re.search(r"`make\s+([\w\-]+)`", enforcement)
    if m_make:
        target = m_make.group(1)
        content = MAKEFILE.read_text()
        return bool(re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE))

    m_script = re.search(r"`(scripts/[\w_/]+\.py)`", enforcement)
    if m_script:
        script_path = ROOT / m_script.group(1)
        return script_path.exists()

    m_plugin = re.search(r"`(enforce-[\w\-]+\.ts)`", enforcement)
    if m_plugin:
        plugin_path = PLUGIN_DIR / m_plugin.group(1)
        return plugin_path.exists()

    m_makefile = re.search(r"Makefile\s+`([\w\-]+)`", enforcement)
    if m_makefile:
        target = m_makefile.group(1)
        content = MAKEFILE.read_text()
        return bool(re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE))

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
        for s in uncovered[:20]:
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
