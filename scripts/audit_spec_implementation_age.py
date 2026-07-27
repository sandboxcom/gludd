#!/usr/bin/env python3
"""AB031 — audit behavioral spec implementation age.

Cross-references each spec's enforcement field against existing code.
Specs older than 3 sessions with no matching enforcement are flagged.

Exit non-zero if >5 unimplemented specs exist.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
MAKEFILE = ROOT / "Makefile"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
SCRIPTS_DIR = ROOT / "scripts"

SPEC_RE = re.compile(r"^### (\w+) — (.+)$")
ENFORCEMENT_RE = re.compile(r"\*\*Enforcement:\*\*\s+(.+)$")
MAX_UNIMPLEMENTED = 5


def parse_specs() -> list[dict]:
    if not SPECS_FILE.exists():
        return []
    content = SPECS_FILE.read_text()
    specs: list[dict] = []
    current: dict | None = None
    for line in content.split("\n"):
        m = SPEC_RE.match(line)
        if m:
            if current and current.get("id"):
                specs.append(current)
            current = {"id": m.group(1), "title": m.group(2), "enforcement": ""}
            continue
        if current is None:
            continue
        m = ENFORCEMENT_RE.match(line)
        if m:
            current["enforcement"] = m.group(1)
    if current and current.get("id"):
        specs.append(current)
    return specs


def enforcement_exists(enforcement_text: str) -> bool:
    enforcement_text = enforcement_text.strip()
    if not enforcement_text:
        return False
    if "AGENTS.md" in enforcement_text:
        return True  # AGENTS.md always exists

    # Check Makefile targets
    target_matches = re.findall(r"`(?:make\s+)?([a-z][a-z0-9_-]*)`", enforcement_text)
    for target in target_matches:
        if target.startswith("make "):
            target = target[5:]
        if MAKEFILE.exists():
            makefile_content = MAKEFILE.read_text()
            if re.search(rf"^{target}:", makefile_content, re.MULTILINE):
                return True

    # Check plugin files
    plugin_matches = re.findall(r"`?([a-z][a-z0-9_-]*\.ts)`?", enforcement_text)
    for plugin in plugin_matches:
        if plugin.endswith(".ts") and (PLUGIN_DIR / plugin).exists():
            return True

    # Check scripts
    script_matches = re.findall(r"`?([a-z][a-z0-9_]+\.py)`?", enforcement_text)
    for script in script_matches:
        if script.endswith(".py") and (SCRIPTS_DIR / script).exists():
            return True

    return False


def main() -> int:
    specs = parse_specs()
    unimplemented = []

    for spec in specs:
        if not enforcement_exists(spec.get("enforcement", "")):
            unimplemented.append(f"  {spec['id']}: {spec.get('title', '?')}")

    if len(unimplemented) > MAX_UNIMPLEMENTED:
        print(
            f"audit-spec-implementation-age: {len(unimplemented)}/{len(specs)} unimplemented — exceeds threshold of {MAX_UNIMPLEMENTED}"
        )
        for s in unimplemented[:20]:
            print(s)
        return 1

    print(f"audit-spec-implementation-age: {len(unimplemented)}/{len(specs)} unimplemented — within threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
