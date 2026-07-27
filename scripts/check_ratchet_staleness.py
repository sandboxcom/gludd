#!/usr/bin/env python3
"""AB032 — check ratchet entry staleness.

Ratchet entries with `since:` timestamps older than 30 days without any
fix attempt are flagged as STALE. Non-zero exit if any entry exceeds threshold.

Usage:
  python scripts/check_ratchet_staleness.py [--max-age-days DAYS]
"""

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATCHET_FILE = ROOT / "config" / "ratchet.yml"
DEFAULT_MAX_AGE_DAYS = 30

SINCE_RE = re.compile(r"since:\s*['\"]?(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})")


def parse_ratchet_entries() -> list[dict]:
    if not RATCHET_FILE.exists():
        return []

    content = RATCHET_FILE.read_text()
    entries: list[dict] = []
    current: dict | None = None

    for line in content.split("\n"):
        if line.startswith("  - ") or re.match(r"^\s+- ", line):
            if current and current.get("since"):
                entries.append(current)
            current = {"raw": line.strip()}

        m = SINCE_RE.search(line)
        if m and current is not None:
            try:
                current["since"] = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
            except ValueError:
                pass

    if current and current.get("since"):
        entries.append(current)

    return entries


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check ratchet entry staleness")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS, help="Max age in days")
    args = parser.parse_args()

    entries = parse_ratchet_entries()

    if not entries:
        print("check-ratchet-staleness: no dated ratchet entries found")
        return 0

    now = datetime.now(timezone.utc)
    stale = []

    for entry in entries:
        since = entry.get("since")
        if since is None:
            continue
        age_days = (now - since).days
        if age_days > args.max_age_days:
            stale.append(f"  {entry.get('raw', '?')[:80]} (age: {age_days}d)")

    if stale:
        print(f"check-ratchet-staleness: {len(stale)} stale entry(ies) older than {args.max_age_days} days:")
        for s in stale:
            print(s)
        return 1

    print(f"check-ratchet-staleness: all {len(entries)} entries within {args.max_age_days}-day threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
