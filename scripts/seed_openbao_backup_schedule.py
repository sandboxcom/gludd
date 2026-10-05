#!/usr/bin/env python3
"""Seed the OpenBao break-glass backup schedule entry on a gludd install host.

Invoked by `make init` AFTER the rest of the install is in place. Idempotent —
skips registration if an entry with `work_type=openbao_break_glass` already
exists.

Behavior:
  1. Build a SecretsManager from the gludd config.
  2. If OpenBao is NOT configured / not initialized, exit 0 (no schedule entry
     created — a backup timer for a non-existent backend is a broken timer).
  3. POST /api/todos/scheduled with cron="0 3 * * *", work_type=openbao_break_glass.
     Skipped if an entry already exists (idempotent).

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


def _openbao_configured() -> bool:
    try:
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager
    except ImportError as exc:
        print(f"seed_openbao_backup_schedule: gludd import failed ({exc}); skipping", file=sys.stderr)
        return False
    cfg = OpenBaoConfig()
    sm = SecretsManager(config=cfg)
    return sm.is_external_configured()


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
        # Daemon not running yet — first-time init; nothing to seed against.
        return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon-url", default=os.environ.get("GLUDD_DAEMON_URL", gludd_env_defaults.DAEMON_URL_DEFAULT))
    parser.add_argument("--psk", default=os.environ.get("GLUDD_AUTH_PSK", "").strip())
    parser.add_argument("--cron", default="0 3 * * *")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not _openbao_configured():
        print("seed_openbao_backup_schedule: OpenBao not configured — skipping (no schedule entry created)")
        return 0

    existing = _existing_entries(args.daemon_url, args.psk)
    if any(e.get("work_type") == "openbao_break_glass" for e in existing):
        print("seed_openbao_backup_schedule: entry already registered — skipping")
        return 0

    body = json.dumps({
        "title": "OpenBao break-glass backup",
        "description": "Daily encrypted snapshot of the OpenBao raft store.",
        "queue": "core",
        "priority": "high",
        "work_type": "openbao_break_glass",
        "cron": args.cron,
        "schedule_timezone": "UTC",
    }).encode()

    if args.dry_run:
        print(f"seed_openbao_backup_schedule: dry-run; would POST {body!r}")
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
            print(f"seed_openbao_backup_schedule: registered ({resp.status})")
            return 0
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        # Daemon not running at install time is common — the entry will be
        # registered on a later `make init` once the daemon is up.
        print(f"seed_openbao_backup_schedule: daemon unreachable ({exc}); skipping", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
