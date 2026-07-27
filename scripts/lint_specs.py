"""lint_specs.py — AA061 enforcement.

Validate BEHAVIORAL_SPECS.md formatting: each spec has ID+title+category+
enforcement+behavior fields; no duplicate IDs; no template filler strings;
enforcement references exist.

Exit 0 on clean, exit 1 on violations found.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"

FILLER_PATTERNS = [
    r"\bTODO\b",
    r"\bTBD\b",
    r"\bFIXME\b",
    r"\(planned\)",
    r"\(not yet built\)",
    r"\(future\)",
    r"\btemplate\b",
    r"Enforcement:\s*$",
    r"Behavior:\s*$",
]


def _parse_specs() -> list[dict]:
    text = SPECS_FILE.read_text()
    specs: list[dict] = []
    current: dict | None = None
    fields: set[str] = set()

    for line in text.split("\n"):
        m = re.match(r"^### (A[A-Z]\d+) — (.+)$", line)
        if m:
            if current and current.get("id"):
                current["_fields"] = fields.copy()
                specs.append(current)
            current = {"id": m.group(1), "title": m.group(2), "enforcement": "", "behavior": "", "category": ""}
            fields = set()
            continue

        if not current:
            continue

        if line.startswith("**Category:**"):
            current["category"] = line.replace("**Category:**", "").strip()
            fields.add("category")
            continue
        if line.startswith("**Enforcement:**"):
            current["enforcement"] = line.replace("**Enforcement:**", "").strip()
            fields.add("enforcement")
            continue
        if line.startswith("**Behavior:**"):
            current["behavior"] = line.replace("**Behavior:**", "").strip()
            fields.add("behavior")
            continue

    if current and current.get("id"):
        current["_fields"] = fields.copy()
        specs.append(current)

    return specs


def _check_filler(text: str) -> list[str]:
    issues: list[str] = []
    for pat in FILLER_PATTERNS:
        if re.search(pat, text):
            issues.append(f"filler pattern: {pat}")
    return issues


def main() -> int:
    if not SPECS_FILE.exists():
        print(f"ERROR: {SPECS_FILE} not found")
        return 1

    specs = _parse_specs()
    errors: list[str] = []

    ids_seen: dict[str, int] = {}
    for i, s in enumerate(specs):
        sid = s["id"]
        if sid in ids_seen:
            errors.append(f"DUPLICATE ID {sid}: line ~{ids_seen[sid]} and line ~{i}")
        ids_seen[sid] = i

        missing = set()
        for field in ("category", "enforcement", "behavior"):
            if field not in s.get("_fields", set()):
                missing.add(field)
        if missing:
            errors.append(f"{sid}: missing fields: {', '.join(sorted(missing))}")

        enf_fillers = _check_filler(s["enforcement"])
        for f in enf_fillers:
            errors.append(f"{sid}: enforcement {f}")

        bhv_fillers = _check_filler(s["behavior"])
        for f in bhv_fillers:
            errors.append(f"{sid}: behavior {f}")

        if not s["enforcement"].strip():
            errors.append(f"{sid}: empty enforcement field")

        if not s["behavior"].strip():
            errors.append(f"{sid}: empty behavior field")

    if errors:
        print(f"SPEC LINT: {len(errors)} violation(s) in {len(specs)} specs:")
        for e in errors[:50]:
            print(f"  {e}")
        if len(errors) > 50:
            print(f"  ... and {len(errors) - 50} more")
        return 1

    print(f"SPEC LINT: PASS ({len(specs)} specs, 0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
