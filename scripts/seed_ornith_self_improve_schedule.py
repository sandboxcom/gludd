#!/usr/bin/env python3
"""Seed the Ornith self-improvement weekly schedule entry on a gludd install host.

Invoked by ``make init`` AFTER the rest of the install is in place. Idempotent
— skips registration if an entry with ``work_type=ornith_self_improve`` already
exists. Exits 0 without seeding when Ornith is not enabled in the config (a
Monday-04:00 improvement timer for a disabled integration is a broken timer).

Behavior:
  1. Load the gludd UserConfig.
  2. If ``config.ornith_enabled`` is False, exit 0 (no schedule entry created).
  3. POST /api/todos/scheduled with cron="0 4 * * 1" (Mondays 04:00 UTC),
     work_type=ornith_self_improve. Skipped if an entry already exists.

The daemon scheduler itself is NOT modified — this is just an API registration.
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


def _ornith_enabled() -> bool:
    try:
        from general_ludd.config.user_config import UserConfig
    except ImportError as exc:
        print(f"seed_ornith_self_improve_schedule: gludd import failed ({exc}); skipping",
              file=sys.stderr)
        return False
    try:
        cfg = UserConfig()
    except Exception:
        return False
    return bool(getattr(cfg, "ornith_enabled", False))


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
    parser.add_argument("--daemon-url",
                        default=os.environ.get("GLUDD_DAEMON_URL", gludd_env_defaults.DAEMON_URL_DEFAULT))
    parser.add_argument("--psk", default=os.environ.get("GLUDD_AUTH_PSK", "").strip())
    parser.add_argument("--cron", default="0 4 * * 1",
                        help="Monday 04:00 UTC by default (low-impact window)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not _ornith_enabled():
        print("seed_ornith_self_improve_schedule: Ornith not enabled in config "
              "— skipping (no schedule entry created)")
        return 0

    existing = _existing_entries(args.daemon_url, args.psk)
    if any(e.get("work_type") == "ornith_self_improve" for e in existing):
        print("seed_ornith_self_improve_schedule: entry already registered — skipping")
        return 0

    body = json.dumps({
        "title": "Ornith self-improvement pass",
        "description": (
            "Weekly Ornith self-improvement loop: pulls the most-recently-"
            "rejected training pairs, proposes improvements, opens PRs, "
            "files human-todos for review."
        ),
        "queue": "core",
        "priority": "medium",
        "work_type": "ornith_self_improve",
        "cron": args.cron,
        "schedule_timezone": "UTC",
    }).encode()

    if args.dry_run:
        print(f"seed_ornith_self_improve_schedule: dry-run; would POST {body!r}")
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
            print(f"seed_ornith_self_improve_schedule: registered ({resp.status})")
            return 0
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"seed_ornith_self_improve_schedule: daemon unreachable ({exc}); skipping",
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
