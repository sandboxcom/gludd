#!/usr/bin/env python3
"""AB024 — detect unchecked TASKS.md items older than 24 hours.

Scans TASKS.md for unchecked items with dispatched timestamps older than
86400 seconds. Reports age and exits non-zero if any found.

Also supports --max-age SECONDS override for testing.
"""

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_MD = ROOT / "TASKS.md"
DEFAULT_MAX_AGE_S = 86400  # 24 hours

UNCHECKED_RE = re.compile(r"^\s*- \[ \]")
DISPATCH_TS_RE = re.compile(r"dispatched:\s*(\d{4}-\d{2}-\d{2}T[\d:Z+-]+|\d{10})")


def parse_iso_epoch(ts: str) -> int:
    ts = ts.strip()
    if ts.isdigit() and len(ts) >= 10:
        return int(ts[:10])
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return int(time.mktime(time.strptime(ts.replace("Z", "+0000"), fmt)))
        except ValueError:
            continue
    return 0


def check_stale_tasks(max_age_s: int = DEFAULT_MAX_AGE_S) -> list[str]:
    if not TASKS_MD.exists():
        return []

    now = int(time.time())
    stale: list[tuple[str, int]] = []

    content = TASKS_MD.read_text()
    lines = content.split("\n")

    for i, line in enumerate(lines):
        if not UNCHECKED_RE.match(line):
            continue

        # Look backward and forward for a dispatched timestamp
        context_start = max(0, i - 5)
        context_end = min(len(lines), i + 5)
        context = "\n".join(lines[context_start:context_end])

        m = DISPATCH_TS_RE.search(context)
        if m:
            epoch = parse_iso_epoch(m.group(1))
            if epoch and now - epoch > max_age_s:
                age_h = (now - epoch) / 3600
                desc = line.strip().lstrip("- [ ]").strip()
                stale.append((f"  {desc} (age: {age_h:.1f}h)", age_h))

    return [s[0] for s in sorted(stale, key=lambda x: -x[1])]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check for stale TASKS.md items")
    parser.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_S, help="Max age in seconds")
    args = parser.parse_args()

    stale = check_stale_tasks(args.max_age)
    if stale:
        print(f"check-stale-tasks: {len(stale)} unchecked item(s) older than {args.max_age // 3600}h:")
        for s in stale:
            print(s)
        return 1

    print("check-stale-tasks: all unchecked items within age threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
