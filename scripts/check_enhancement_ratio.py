"""Read-only diagnostic for the enhancement-ratio enforcement state file.

Prints the current wave composition and session aggregate.
Exposed as `make check-enhancement-ratio`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

STATE_FILE = Path("/tmp/gludd-enhancement-ratio.json")


def main() -> None:
    if not STATE_FILE.exists():
        print("RATIO STATE: no data (no dispatches recorded yet)")
        sys.exit(0)

    try:
        data = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"RATIO STATE: unreadable ({exc})")
        sys.exit(2)

    wave = data.get("wave", [])
    enhancements = sum(1 for e in wave if e.get("type") == "enhancement")
    fixes = sum(1 for e in wave if e.get("type") == "fix")
    total = len(wave)
    fix_pct = (fixes / total * 100) if total > 0 else 0
    enh_pct = (enhancements / total * 100) if total > 0 else 0

    print(f"CURRENT WAVE: {total} dispatches — {enhancements} enhancements ({enh_pct:.0f}%), "
          f"{fixes} fixes ({fix_pct:.0f}%)")

    if total >= 2 and fix_pct > 50:
        print(f"  STATUS: VIOLATION — fix ratio {fix_pct:.0f}% exceeds 50% threshold")
    elif total >= 2:
        print(f"  STATUS: OK — within 50% threshold")
    elif total > 0:
        print(f"  STATUS: pending ({total} dispatch(es), need 2+ for wave check)")

    session_e = data.get("session_enhancements", 0)
    session_f = data.get("session_fixes", 0)
    session_u = data.get("session_unknown", 0)
    print(f"SESSION: {session_e} enhancements, {session_f} fixes, {session_u} unknown")

    if total >= 2 and fix_pct > 50:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
