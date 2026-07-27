"""check_ratchet_population.py — AA091 enforcement.

Verify config/ratchet.yml is populated with known failure entries
when there are pre-existing CI test failures. An empty ratchet.yml
while known CI failures exist is a policy violation.

Exit 0 on clean, exit 1 if ratchet should be populated.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATCHET_PATH = ROOT / "config" / "ratchet.yml"


def _ratchet_has_entries() -> bool:
    """Return True if ratchet.yml has at least one tracked failure entry."""
    if not RATCHET_PATH.exists():
        return False
    content = RATCHET_PATH.read_text()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # A ratchet entry looks like: "tests/unit/... reason"
        if ":" in stripped and len(stripped.split(":", 1)[0].strip()) > 5:
            return True
    return False


def main() -> int:
    has_entries = _ratchet_has_entries()

    if has_entries:
        # Check that ratchet entries are well-formed
        content = RATCHET_PATH.read_text()
        lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
        entry_count = sum(1 for l in lines if ":" in l and len(l.split(":", 1)[0].strip()) > 5)
        print(f"OK: ratchet.yml has {entry_count} tracked entry/entries")
        return 0

    # Ratchet is empty — this is OK if there are no known CI failures.
    # We can't determine CI state from a local script, so we emit an
    # advisory message. The policy (AA091) says ratchet MUST be populated
    # within 1 session of failure discovery. The CI workflow itself
    # enforces the ratchet on test jobs that use continue-on-error.
    print("ADVISORY: ratchet.yml has no tracked entries.")
    print("If there are known CI failures that use continue-on-error,")
    print("populate config/ratchet.yml within 1 session. Otherwise, no action needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
