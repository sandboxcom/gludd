"""prune_dead_specs.py — AB019 enforcement.

Removes specs whose Enforcement field references files/targets that no
longer exist. Run before deduplication to keep spec count reflecting only
live enforcement.

Usage: python prune_dead_specs.py [--dry-run]
Exit 0 on success; exit 1 on error.
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


def is_spec_dead(body: str, targets: set[str]) -> bool:
    """A spec is dead if ALL its enforcement refs are unresolvable."""
    enf_match = ENFORCEMENT_RE.search(body)
    if not enf_match:
        return False
    enf_text = enf_match.group(1)

    file_refs = FILE_REF_RE.findall(enf_text)
    target_refs = TARGET_REF_RE.findall(enf_text)

    all_refs = file_refs + target_refs
    if not all_refs:
        return False

    resolved = any(resolve_file(f) for f in file_refs) or any(t in targets for t in target_refs)
    return not resolved


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if not SPECS_FILE.exists():
        print(f"ERROR: {SPECS_FILE} not found")
        return 1

    text = SPECS_FILE.read_text(encoding="utf-8")
    targets = load_makefile_targets()

    dead_ids: set[str] = set()
    pos = 0
    for m in SPEC_RE.finditer(text):
        start = m.start()
        next_m = SPEC_RE.search(text, m.end())
        end = next_m.start() if next_m else len(text)
        body = text[m.end() : end].strip()
        if is_spec_dead(body, targets):
            dead_ids.add(m.group(1))

    if not dead_ids:
        print("AB019: No dead specs found. PASS")
        return 0

    print(f"AB019: {len(dead_ids)} dead specs found (enforcement refs no longer resolve):")
    for sid in sorted(dead_ids):
        print(f"  - {sid}")

    if dry_run:
        print("DRY-RUN: no changes made. Run without --dry-run to prune.")
        return 0

    new_lines = []
    skip_until_next = False
    for line in text.splitlines(keepends=True):
        spec_match = re.match(r"^### (A[AB]\d{3}) —", line)
        if spec_match:
            skip_until_next = spec_match.group(1) in dead_ids
            if skip_until_next:
                continue
        if not skip_until_next:
            new_lines.append(line)

    new_text = "".join(new_lines)
    SPECS_FILE.write_text(new_text, encoding="utf-8")

    print(f"Pruned {len(dead_ids)} dead specs from {SPECS_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
