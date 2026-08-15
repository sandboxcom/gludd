#!/usr/bin/env python3
"""Seed the remediation blocker-scan schedule entry on a gludd install host.

Invoked by `make init` AFTER the rest of the install is in place. Idempotent —
skips registration if an entry with `work_type=blocker_scan` already exists.

Behavior:
  1. POST /api/todos/scheduled with cron="0 * * * *" (hourly),
     work_type=blocker_scan, title="Remediation blocker scan".
  2. Skipped if an entry already exists (idempotent).
  3. Daemon-unreachable is non-fatal (returns 0) — first-time init often
     runs before the daemon is up.

The scan itself is implemented by the event loop: when a QUEUED child of
this schedule is dispatched, the agent role performs
`POST /admin/remediation/remediate`. The schedule entry merely ensures
the work is generated every hour.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

try:
    from scripts import gludd_env_defaults as gludd_env_defaults
except ModuleNotFoundError:  # pragma: no cover - direct launch from scripts/
    import gludd_env_defaults


def _existing_entries(daemon_url: str, psk: str) -> list[dict]:
    req = urllib.request.Request(
        f"{daemon_url.rstrip('/')}/api/todos/scheduled",
        method="GET",
        headers={"Authorization": f"Bearer {psk}"} if psk else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                return []
            return list(json.loads(resp.read().decode()))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--daemon-url",
        default=os.environ.get("GLUDD_DAEMON_URL", gludd_env_defaults.DAEMON_URL_DEFAULT),
    )
    parser.add_argument("--psk", default=os.environ.get("GLUDD_AUTH_PSK", "").strip())
    parser.add_argument("--cron", default="0 * * * *")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    existing = _existing_entries(args.daemon_url, args.psk)
    if any(e.get("work_type") == "blocker_scan" for e in existing):
        print("seed_blocker_scan_schedule: entry already registered — skipping")
        return 0

    body = json.dumps(
        {
            "title": "Remediation blocker scan",
            "description": (
                "Hourly scan for stalled todos / human-todos. The dispatched "
                "agent calls POST /admin/remediation/remediate to apply the "
                "suggested remediation (dispatch_retry / schedule_cron / "
                "file_human_todo)."
            ),
            "queue": "core",
            "priority": "medium",
            "work_type": "blocker_scan",
            "cron": args.cron,
            "schedule_timezone": "UTC",
        }
    ).encode()

    if args.dry_run:
        print(f"seed_blocker_scan_schedule: dry-run; would POST {body!r}")
        return 0

    req = urllib.request.Request(
        f"{args.daemon_url.rstrip('/')}/api/todos/scheduled",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {args.psk}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"seed_blocker_scan_schedule: registered ({resp.status})")
            return 0
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(
            f"seed_blocker_scan_schedule: daemon unreachable ({exc}); skipping",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
