"""check_spec_priority.py — AA045 enforcement.

Assigns P0-P4 priority levels to behavioral specs in BEHAVIORAL_SPECS.md.
Classification:
  P0: Active CI failures, pipeline blocking, ship-blocker
  P1: Release blockage, deployment-blocking
  P2: Repeated user frustration, behavior causing user interrupts
  P3: Quality improvement, static analysis, guardrail hardening
  P4: Aspirational, future-work, nice-to-have

Reports priority distribution and any P0/P1 specs that are unimplemented
(missing enforcement code). P0 specs must be implemented before P1, etc.

Exit 0 on clean classification, exit 1 if P0/P1 specs are unimplemented.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_PATH = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
MAKEFILE_PATH = ROOT / "Makefile"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

# ── Priority classification rules ──

P0_KEYWORDS: list[str] = [
    "ci.*(?:fail|red|broken|block)",
    "pipeline.*(?:fail|block|stop)",
    "release.*(?:block|fail|gate)",
    "ship.blocker",
    "deploy.*(?:fail|block)",
    "gate.*(?:fail|red)",
    "push.*cancel.*ci",
    "circular.*ci",
    "ci.*restart",
    "continue.on.error",
    "force.push",
    "tag.push.*(?:fail|block)",
]

P1_KEYWORDS: list[str] = [
    "release.*(?:discipline|cut|artifact)",
    "deploy.*(?:without|missing|unpushed)",
    "unpushed",
    "pre.commit.*(?:conflict|block|stash)",
    "stash.*(?:conflict|leak|race)",
    "merge.*(?:conflict|safety|guard)",
    "commit.*discipline",
    "branch.*discipline",
    "push.*discipline",
]

P2_KEYWORDS: list[str] = [
    "frustration",
    "repeat",
    "user.*interrupt",
    "essay.writing",
    "single.task",
    "wrong.branch",
    "objective.*priorit",
    "deprioritiz",
    "ignoring.*objective",
    "secondary.request",
    "stop.*pattern",
]

P3_KEYWORDS: list[str] = [
    "quality.*(?:gate|improve)",
    "lint",
    "typecheck",
    "guardrail",
    "enforcement.*(?:plugin|code)",
    "plugin.*(?:weaken|advisory)",
    "hot.module|hot.reload",
    "assert.*depend",
    "test.*(?:integrity|quality|name)",
    "dead.code",
    "spec.*(?:duplicat|quality|lint)",
    "structural.test",
    "node.v26",
]

P4_KEYWORDS: list[str] = [
    "aspirational",
    "documentation",
    "docstring",
    "readme",
    "presentation",
    "meta",
    "spec.count",
    "spec.*target",
    "future",
    "nice.to.have",
]


def classify_spec(title: str, behavior: str, category: str) -> str:
    """Classify a spec into P0-P4 based on its title, behavior text, and category."""
    combined = f"{title.lower()} {behavior.lower()} {category.lower()}"

    for kw_pattern in P0_KEYWORDS:
        if re.search(kw_pattern, combined, re.IGNORECASE):
            return "P0"
    for kw_pattern in P1_KEYWORDS:
        if re.search(kw_pattern, combined, re.IGNORECASE):
            return "P1"
    for kw_pattern in P2_KEYWORDS:
        if re.search(kw_pattern, combined, re.IGNORECASE):
            return "P2"
    for kw_pattern in P3_KEYWORDS:
        if re.search(kw_pattern, combined, re.IGNORECASE):
            return "P3"
    for kw_pattern in P4_KEYWORDS:
        if re.search(kw_pattern, combined, re.IGNORECASE):
            return "P4"

    # Default: category-based fallback
    cat_lower = category.lower()
    if "ci discipline" in cat_lower or "release discipline" in cat_lower:
        return "P1"
    if "commit discipline" in cat_lower or "merge safety" in cat_lower:
        return "P1"
    if "subagent discipline" in cat_lower or "intent priority" in cat_lower:
        return "P2"
    return "P3"


def parse_specs() -> list[dict]:
    """Parse BEHAVIORAL_SPECS.md into structured spec records."""
    if not SPECS_PATH.exists():
        print(f"ERROR: {SPECS_PATH} not found")
        return []

    content = SPECS_PATH.read_text()
    specs: list[dict] = []

    # Match spec headers: ### AA001 — title or ### P01 — title
    spec_re = re.compile(
        r"###\s+(AA\d+|AB\d+|P\d+|B\d+)\s*[—\-]\s*(.+?)\n"
        r"\*\*Category:\*\*\s*(.+?)\n"
        r"\*\*Enforcement:\*\*\s*(.+?)\n"
        r"\*\*Behavior:\*\*\s*(.+?)(?=\n###|\n---|\Z)",
        re.DOTALL,
    )

    for match in spec_re.finditer(content):
        spec_id = match.group(1)
        title = match.group(2).strip()
        category = match.group(3).strip()
        enforcement = match.group(4).strip()
        behavior = match.group(5).strip()

        specs.append(
            {
                "id": spec_id,
                "title": title,
                "category": category,
                "enforcement": enforcement,
                "behavior": behavior,
            }
        )

    return specs


def check_enforcement_exists(spec: dict) -> bool:
    """Check if a spec's claimed enforcement mechanism actually exists."""
    enforcement = spec["enforcement"].lower()

    # Check for Makefile guards
    guard_match = re.search(r"`([\w_-]+)`\s*(?:in|target|guard)?", spec["enforcement"])
    if guard_match:
        guard_name = guard_match.group(1).strip("`")
        # Check if it's a Makefile target
        makefile_text = MAKEFILE_PATH.read_text()
        if re.search(rf"^{guard_name}:", makefile_text, re.MULTILINE):
            return True
        # Check if it's a plugin
        plugin_path = guard_name
        if not plugin_path.endswith(".ts"):
            plugin_path += ".ts"
        if (PLUGIN_DIR / plugin_path).exists():
            return True
        # Check if it's a script
        if (ROOT / "scripts" / (guard_name + ".py")).exists():
            return True

    # Check for plugin references
    if re.search(r"enforce-[\w-]+\.ts", enforcement):
        plugin_name = re.search(r"enforce-[\w-]+\.ts", enforcement).group(0)
        return (PLUGIN_DIR / plugin_name).exists()

    # Check for Makefile target references
    target_match = re.search(r"make\s+([\w_-]+)", enforcement)
    if target_match:
        target_name = target_match.group(1)
        makefile_text = MAKEFILE_PATH.read_text()
        return bool(re.search(rf"^{target_name}:", makefile_text, re.MULTILINE))

    # Check for script references
    script_match = re.search(r"scripts/([\w_]+)\.py", enforcement)
    if script_match:
        return (ROOT / "scripts" / (script_match.group(1) + ".py")).exists()

    # Check for AGENTS.md section references
    if "agents.md" in enforcement:
        return True

    return False


def main() -> int:
    specs = parse_specs()
    if not specs:
        print("No specs found in BEHAVIORAL_SPECS.md")
        return 1

    priorities: dict[str, list[dict]] = defaultdict(list)
    unimplemented_p0_p1: list[str] = []

    for spec in specs:
        priority = classify_spec(spec["title"], spec["behavior"], spec["category"])
        spec["priority"] = priority
        priorities[priority].append(spec)

        if priority in ("P0", "P1"):
            if not check_enforcement_exists(spec):
                unimplemented_p0_p1.append(f"{spec['id']} ({priority}): {spec['title']}")

    # Report distribution
    total = len(specs)
    print(f"SPEC PRIORITY: {total} specs classified")
    for p in ("P0", "P1", "P2", "P3", "P4"):
        count = len(priorities.get(p, []))
        pct = count / total * 100 if total > 0 else 0
        print(f"  {p}: {count} ({pct:.1f}%)")

    # Report unimplemented P0/P1 specs
    if unimplemented_p0_p1:
        print(f"\nUNIMPLEMENTED P0/P1: {len(unimplemented_p0_p1)} spec(s) lack enforcement:")
        for item in unimplemented_p0_p1:
            print(f"  {item}")
        print("\nP0/P1 specs with missing enforcement block CI. Fix or provide enforcement.")
        return 1

    print("\nPRIORITY CHECK: all P0/P1 specs have enforcement code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
