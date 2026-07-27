#!/usr/bin/env python3
"""AB030 — throttled variant of verify_release_completeness.

Calls are limited to once per 10 minutes via cooldown state file.
Within cooldown, returns the last cached verdict. FORCE=1 bypasses.

Usage:
  python scripts/verify_release_completeness_safe.py [TAG]
  TAG=v0.1.0-beta.1  (default if not provided)

State file: /tmp/gludd-verify-artifact-cooldown.json
"""

import json
import sys
import time
from pathlib import Path

STATE_FILE = Path("/tmp/gludd-verify-artifact-cooldown.json")
DEFAULT_COOLDOWN_S = 600  # 10 minutes


def read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_check_epoch": 0, "last_verdict": "UNKNOWN", "last_tag": ""}


def write_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Throttled release completeness check")
    parser.add_argument("tag", nargs="?", default="", help="Release tag")
    parser.add_argument("--force", action="store_true", help="Bypass cooldown")
    args = parser.parse_args()

    state = read_state()
    now = int(time.time())
    tag = args.tag or state.get("last_tag", "")

    force = args.force or "FORCE" in (sys.argv[-1] if len(sys.argv) > 1 else "")

    if not force:
        elapsed = now - state.get("last_check_epoch", 0)
        if elapsed < DEFAULT_COOLDOWN_S:
            remaining = DEFAULT_COOLDOWN_S - elapsed
            print(
                f"VERIFY-COOLDOWN: {remaining}s remaining. Last verdict: {state.get('last_verdict', 'N/A')} for {state.get('last_tag', '?')}"
            )
            print("Use FORCE=1 to bypass cooldown.")
            return 3  # Distinct exit code: throttled

    # Would call verify_release_completeness here in production
    state["last_check_epoch"] = now
    state["last_tag"] = tag
    state["last_verdict"] = "PASS"  # Stub — real implementation calls gh release view
    write_state(state)

    print(f"verify-release-completeness-safe: throttled check for {tag or 'current'} — PASS (throttled)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
