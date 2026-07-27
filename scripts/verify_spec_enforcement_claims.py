"""verify_spec_enforcement_claims.py — AB013 enforcement.

Mechanically checks each spec's Enforcement field references.
If a spec claims enforcement via a filename (.ts, .py, .sh), that file
MUST exist. If it claims a Makefile target, the target MUST exist.
Specs with claims that don't resolve are flagged UNVERIFIED.

Exit 0 if all claims resolve; exit 1 with violations.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
MAKEFILE = ROOT / "Makefile"

SPEC_RE = re.compile(r"^### (A[AB]\d{3}) — (.+)$", re.MULTILINE)
ENFORCEMENT_RE = re.compile(r"\*\*Enforcement:\*\*\s*(.+)$", re.MULTILINE)
FILE_REF_RE = re.compile(r"([a-zA-Z0-9_\-./]+\.(?:ts|py|sh|yml|yaml|json|js|mjs))")
TARGET_REF_RE = re.compile(r"`(?:make\s+)?([a-zA-Z0-9_\-]+)`")


def parse_specs(text: str) -> list[tuple[str, str, str]]:
    specs = []
    for m in SPEC_RE.finditer(text):
        spec_id = m.group(1)
        title = m.group(2)
        start = m.end()
        next_m = SPEC_RE.search(text, start)
        end = next_m.start() if next_m else len(text)
        body = text[start:end].strip()
        specs.append((spec_id, title, body))
    return specs


def extract_enforcement_refs(body: str) -> tuple[list[str], list[str]]:
    """Extract file references and make target references from enforcement line."""
    enf_match = ENFORCEMENT_RE.search(body)
    if not enf_match:
        return [], []
    enf_text = enf_match.group(1)

    files = [m.group(1) for m in FILE_REF_RE.finditer(enf_text)]
    targets = [m.group(1) for m in TARGET_REF_RE.finditer(enf_text)]
    return files, targets


class EnforcementClaims:
    """Caches expensive lookups for reuse across specs."""

    def __init__(self):
        self._makefile_targets: set[str] | None = None
        self._known_make_targets: set[str] | None = None

    @property
    def makefile_targets(self) -> set[str]:
        if self._makefile_targets is None:
            targets = set()
            if MAKEFILE.exists():
                for line in MAKEFILE.read_text().splitlines():
                    m = re.match(r"^([a-zA-Z0-9_\-]+):", line)
                    if m:
                        targets.add(m.group(1))
            self._makefile_targets = targets
        return self._makefile_targets


def main() -> int:
    if not SPECS_FILE.exists():
        print(f"ERROR: {SPECS_FILE} not found")
        return 1

    text = SPECS_FILE.read_text(encoding="utf-8")
    specs = parse_specs(text)
    claims = EnforcementClaims()

    violations: list[str] = []
    for spec_id, title, body in specs:
        files, targets = extract_enforcement_refs(body)
        if not files and not targets:
            violations.append(f"{spec_id} ({title}): no enforcement refs found")
            continue

        for f in files:
            resolved = False
            candidates = [
                ROOT / f,
                ROOT / ".opencode" / "plugin" / f.rsplit("/", 1)[-1] if "/" not in f else ROOT / f,
            ]
            for c in candidates:
                if c.exists():
                    resolved = True
                    break
            if not resolved:
                violations.append(f"{spec_id} ({title}): claimed file '{f}' does not exist")

        for t in targets:
            if t not in claims.makefile_targets:
                violations.append(f"{spec_id} ({title}): claimed make target '{t}' not found in Makefile")

    if violations:
        print(f"AB013: {len(violations)}/{len(specs)} specs have unresolved enforcement claims:")
        for v in violations[:50]:
            print(f"  - {v}")
        if len(violations) > 50:
            print(f"  ... and {len(violations) - 50} more")
        return 1

    print(f"AB013: All {len(specs)} specs have verifiable enforcement claims. PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
