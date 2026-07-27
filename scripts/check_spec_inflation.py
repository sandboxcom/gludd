"""check_spec_inflation.py — AB012 enforcement.

Detects spec count inflation: commits that modify existing specs without
adding new spec IDs. If >80% of spec changes in a commit are to existing
spec bodies without adding new IDs, the commit is flagged.

Exit 0 if clean; exit 1 if inflation detected.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"

SPEC_ID_RE = re.compile(r"^### (A[AB]\d{3}) —")


def diff_added_spec_ids() -> tuple[int, int]:
    """Return (new_spec_ids_added, existing_specs_modified) from git diff."""
    import subprocess

    cp = subprocess.run(
        ["git", "diff", "--cached", "--", str(SPECS_FILE)],
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0 or not cp.stdout:
        return 0, 0

    added_ids = 0
    modified_lines = 0
    for line in cp.stdout.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            if SPEC_ID_RE.match(line.lstrip("+")):
                added_ids += 1
        elif line.startswith("+") or line.startswith("-"):
            if not line.startswith("---") and not line.startswith("+++"):
                modified_lines += 1

    return added_ids, max(modified_lines - added_ids, 0)


def main() -> int:
    if not SPECS_FILE.exists():
        return 0

    import subprocess

    cp = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
    )
    if "BEHAVIORAL_SPECS.md" not in cp.stdout:
        return 0

    added, modified = diff_added_spec_ids()
    total = added + modified

    if total == 0:
        return 0

    ratio = modified / total if total > 0 else 0
    if ratio > 0.8 and added == 0:
        print(
            f"AB012 SPEC-INFLATION: {modified}/{total} changes are edits to "
            f"existing specs without adding new spec IDs ({ratio:.0%}). "
            f"Threshold: ≤80% edits. Commit flagged for review."
        )
        return 1

    print(f"AB012: {added} new specs, {modified} modifications. Edit ratio: {ratio:.0%}. PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
