"""audit_spec_liveness.py — AA084 enforcement.

Classify each behavioral spec as ACTIONABLE, ASPIRATIONAL, REDUNDANT, or DEAD.
Exit 0 if >=90% actionable; exit 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip(), b.strip()).ratio()


def _parse_specs() -> list[dict]:
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


def _classify(spec: dict, all_behaviors: list[str]) -> str:
    enf = spec["enforcement"].lower()
    beh = spec["behavior"].lower()
    enf_empty = not enf or any(w in enf for w in ("planned", "todo", "tbd", "not yet"))

    if enf_empty:
        return "ASPIRATIONAL"

    filenames = re.findall(r"`([\w_/.\-]+)`", spec["enforcement"])
    all_dead = True
    for fname in filenames:
        if fname.endswith(".ts"):
            path = ROOT / ".opencode" / "plugin" / fname
        elif fname.endswith(".py"):
            path = ROOT / fname if fname.startswith("scripts/") else ROOT / "scripts" / fname
        elif fname.endswith(".md"):
            path = ROOT / fname
        else:
            target = re.match(r"make\s+(.+)", fname)
            if target:
                path = ROOT / "Makefile"
                content = path.read_text() if path.exists() else ""
                if not re.search(rf"^{re.escape(target.group(1))}:", content, re.MULTILINE):
                    continue
                all_dead = False
                break
            continue
        if path.exists():
            all_dead = False
            break

    if all_dead and filenames:
        return "DEAD"

    for other in all_behaviors:
        if other is not beh and _similarity(beh, other) > 0.70:
            return "REDUNDANT"

    return "ACTIONABLE"


def main() -> int:
    if not SPECS_FILE.exists():
        print(f"ERROR: {SPECS_FILE} not found")
        return 1
    specs = _parse_specs()
    behaviors = [s["behavior"] for s in specs]
    counts = {"ACTIONABLE": 0, "ASPIRATIONAL": 0, "REDUNDANT": 0, "DEAD": 0}
    for s in specs:
        cat = _classify(s, behaviors)
        counts[cat] += 1
    total = len(specs)
    pct = counts["ACTIONABLE"] / total * 100 if total else 0
    print(f"Spec liveness audit: {total} specs")
    for cat, n in counts.items():
        print(f"  {cat}: {n} ({n / total * 100:.1f}%)")
    threshold = 90.0
    if pct >= threshold:
        print(f"\nPASS: {pct:.1f}% actionable >= {threshold:.0f}%")
        return 0
    print(f"\nFAIL: {pct:.1f}% actionable < {threshold:.0f}%")
    return 1


if __name__ == "__main__":
    sys.exit(main())
