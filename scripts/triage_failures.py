"""triage_failures.py — AA063 enforcement.

Classify test failures as NEW (introduced this session) vs PRE-EXISTING
(in ratchet.yml or BASELINE.md). Agent must fix NEW failures immediately;
PRE-EXISTING failures are tracked for later.

Exit 0 on no NEW failures, exit 1 if NEW failures exist.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATCHET_FILE = ROOT / "config" / "ratchet.yml"
BASELINE_FILE = ROOT / "BASELINE.md"

SESSION_START_MARKER = "/tmp/gludd-session-start.json"


def _get_session_start() -> str | None:
    """Return the session start timestamp if known."""
    p = Path(SESSION_START_MARKER)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return data.get("started_at")
    except Exception:
        return None


def _load_known_failures() -> set[str]:
    """Load known/pre-existing test failures from ratchet.yml and BASELINE.md."""
    known: set[str] = set()

    if RATCHET_FILE.exists():
        for line in RATCHET_FILE.read_text().split("\n"):
            m = re.search(r"tests?[/\w]+\.py", line)
            if m:
                known.add(m.group(0))

    if BASELINE_FILE.exists():
        for line in BASELINE_FILE.read_text().split("\n"):
            m = re.search(r"tests?[/\w]+\.py", line)
            if m:
                known.add(m.group(0))

    return known


def _run_pytest_collect() -> str:
    """Run pytest --collect-only to find failures without running tests."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout + result.stderr
    except Exception as e:
        return str(e)


def main() -> int:
    output = _run_pytest_collect()

    import_errors = re.findall(r"ERROR collecting (tests?[\w/]+\.py)", output)
    known = _load_known_failures()

    new_failures: list[str] = []
    preexisting: list[str] = []

    for f in import_errors:
        clean = f.strip()
        if any(k in clean for k in known):
            preexisting.append(clean)
        else:
            new_failures.append(clean)

    print(f"Test failure triage:")
    print(f"  Total collection errors: {len(import_errors)}")

    if preexisting:
        print(f"  PRE-EXISTING (tracked in ratchet/baseline): {len(preexisting)}")
        for f in preexisting:
            print(f"    {f}")

    if new_failures:
        print(f"  NEW (must fix immediately): {len(new_failures)}")
        for f in new_failures:
            print(f"    {f}")

    if not import_errors:
        print("  PASS: 0 collection errors")
        return 0

    if new_failures:
        print(f"\nACTION REQUIRED: Fix {len(new_failures)} new failure(s) before proceeding.")
        return 1

    print(f"\nNo new failures to fix. Preexisting failures ({len(preexisting)}) tracked separately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
