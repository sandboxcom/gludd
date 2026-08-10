"""Diagnose why specific specs fail _enforcement_exists."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
MAKEFILE = ROOT / "Makefile"
AGENTS_FILE = ROOT / "AGENTS.md"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


def parse_spec(text):
    """Parse a spec block, return enforcement text."""
    in_enforcement = False
    enforcement = ""
    for line in text.split("\n"):
        if line.startswith("**Enforcement:**"):
            in_enforcement = True
            enforcement = line.replace("**Enforcement:**", "").strip()
        elif in_enforcement and line.strip() and not line.startswith("### "):
            enforcement += " " + line.strip()
        elif line.startswith("### "):
            break
    return enforcement


def check(text):
    if not text:
        return False, "empty"
    lower = text.lower()
    filler = re.search(r"(planned|TODO|TBD|not yet|future|upcoming)", lower, re.IGNORECASE)
    if filler:
        return False, f"filler: {filler.group(0)}"

    if "agents.md" in lower and AGENTS_FILE.exists():
        return True, "AGENTS.md"

    for m in re.finditer(r"`make\s+([\w\-]+)`", text):
        target = m.group(1)
        content = MAKEFILE.read_text()
        if re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE):
            return True, f"make target: {target}"
        else:
            return False, f"make target not found: {target}"

    for m in re.finditer(r"`(scripts/[\w_/]+\.py)`", text):
        path = ROOT / m.group(1)
        if path.exists():
            return True, f"script: {m.group(1)}"
        else:
            return False, f"script not found: {m.group(1)}"

    for m in re.finditer(r"`(enforce-[\w\-]+\.ts)`", text):
        path = PLUGIN_DIR / m.group(1)
        if path.exists():
            return True, f"plugin: {m.group(1)}"
        else:
            return False, f"plugin not found: {m.group(1)}"

    return False, "no pattern matched"


# Find the specs
content = SPECS_FILE.read_text()
target_ids = ["V95", "Y37", "Y47", "Y100"]

for tid in target_ids:
    pattern = rf"### {tid} — .+?\n"
    m = re.search(pattern, content)
    if m:
        # Extract from this position until next ### or end
        start = m.start()
        next_spec = re.search(r"^### [A-Z]+\d+ — ", content[start + len(m.group()) :], re.MULTILINE)
        if next_spec:
            end = start + len(m.group()) + next_spec.start()
        else:
            end = len(content)
        block = content[start:end]
        enf = parse_spec(block)
        ok, reason = check(enf)
        print(f"{tid}: {'PASS' if ok else 'FAIL'} — {reason}")
        print(f"  enforcement: {enf[:200]}")
    else:
        print(f"{tid}: NOT FOUND")
