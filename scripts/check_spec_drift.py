"""check_spec_drift.py — AB017 enforcement.

Detects when enforcement code changes (plugins renamed, Makefile targets
removed, scripts deleted) making spec claims stale. Flags specs whose
Enforcement references no longer resolve to existing files/targets.

Exit 0 if no drift detected; exit 1 with stale specs listed.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
MAKEFILE = ROOT / "Makefile"

SPEC_RE = re.compile(r"^### (A[AB]\d{3}) — (.+)$", re.MULTILINE)
ENFORCEMENT_RE = re.compile(r"\*\*Enforcement:\*\*\s*(.+)$", re.MULTILINE)
FILE_REF_RE = re.compile(r"([a-zA-Z0-9_\-./]+\.(?:ts|py|sh|yml|yaml))")
TARGET_REF_RE = re.compile(r"`(?:make\s+)?([a-zA-Z0-9_\-]+)`")


def parse_specs(text: str) -> list[tuple[str, str, str]]:
    specs = []
    for m in SPEC_RE.finditer(text):
        start = m.end()
        next_m = SPEC_RE.search(text, start)
        end = next_m.start() if next_m else len(text)
        specs.append((m.group(1), m.group(2), text[start:end].strip()))
    return specs


def load_makefile_targets() -> set[str]:
    targets: set[str] = set()
    if MAKEFILE.exists():
        for line in MAKEFILE.read_text().splitlines():
            m = re.match(r"^([a-zA-Z0-9_\-]+):", line)
            if m:
                targets.add(m.group(1))
    return targets


def resolve_file(file_ref: str) -> bool:
    candidates = [
        ROOT / file_ref,
        ROOT / "scripts" / file_ref,
        ROOT / ".opencode" / "plugin" / file_ref,
    ]
    return any(c.exists() for c in candidates)


def main() -> int:
    if not SPECS_FILE.exists():
        print(f"ERROR: {SPECS_FILE} not found")
        return 1

    text = SPECS_FILE.read_text(encoding="utf-8")
    specs = parse_specs(text)
    targets = load_makefile_targets()

    stale: list[str] = []
    for spec_id, title, body in specs:
        enf_match = ENFORCEMENT_RE.search(body)
        if not enf_match:
            continue
        enf_text = enf_match.group(1)

        file_refs = FILE_REF_RE.findall(enf_text)
        target_refs = TARGET_REF_RE.findall(enf_text)

        missing_files = [f for f in file_refs if not resolve_file(f)]
        missing_targets = [t for t in target_refs if t not in targets]

        if missing_files or missing_targets:
            parts = []
            if missing_files:
                parts.append(f"missing files: {missing_files}")
            if missing_targets:
                parts.append(f"missing targets: {missing_targets}")
            stale.append(f"{spec_id} ({title}): " + "; ".join(parts))

    if stale:
        print(f"AB017 SPEC-DRIFT: {len(stale)}/{len(specs)} specs reference non-existent enforcement:")
        for s in stale[:50]:
            print(f"  - {s}")
        if len(stale) > 50:
            print(f"  ... and {len(stale) - 50} more")
        return 1

    print(f"AB017: All {len(specs)} specs have valid enforcement references. PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
