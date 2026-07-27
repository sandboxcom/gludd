#!/usr/bin/env python3
"""AB023 — prevent dispatching two subagents to edit the same file.

Tracks recently-dispatched file targets via /tmp/gludd-subagent-files.json.
A file dispatched within the last 90 seconds is rejected for repeat dispatch.
Prevents waste from concurrent edits to the same file.

State file: /tmp/gludd-subagent-files.json
  {"files": {"<hash>": <epoch>}, "max_age_s": 90}
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

STATE_FILE = Path("/tmp/gludd-subagent-files.json")
DEFAULT_MAX_AGE_S = 90


def read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"files": {}, "max_age_s": DEFAULT_MAX_AGE_S}


def write_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def file_hash(path: str) -> str:
    resolved = os.path.realpath(os.path.expanduser(path))
    return hashlib.sha256(resolved.encode()).hexdigest()[:16]


def is_file_locked(hash_key: str, state: dict) -> bool:
    now = int(time.time())
    max_age = state.get("max_age_s", DEFAULT_MAX_AGE_S)
    files = state.get("files", {})

    if hash_key not in files:
        return False

    dispatched_at = files[hash_key]
    if now - dispatched_at < max_age:
        return True

    del files[hash_key]
    return False


def lock_file(hash_key: str, state: dict) -> None:
    state.setdefault("files", {})[hash_key] = int(time.time())
    write_state(state)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check/prevent subagent file dedup")
    parser.add_argument("--check", type=str, help="Check if a file path is locked")
    parser.add_argument("--lock", type=str, help="Lock a file path after dispatch")
    parser.add_argument("--clean", action="store_true", help="Clean expired entries")
    args = parser.parse_args()

    state = read_state()
    now = int(time.time())
    max_age = state.get("max_age_s", DEFAULT_MAX_AGE_S)

    # Clean expired entries
    files = state.get("files", {})
    expired = [k for k, v in files.items() if now - v > max_age]
    for k in expired:
        del files[k]

    if args.clean:
        write_state(state)
        print(f"check-subagent-file-dedup: cleaned {len(expired)} expired entries, {len(files)} active")
        return 0

    if args.check:
        h = file_hash(args.check)
        if is_file_locked(h, state):
            remaining = max_age - (now - files.get(h, 0))
            print(f"BLOCKED: {args.check} was dispatched {remaining}s ago — wait {max_age}s before re-dispatching")
            return 1
        print(f"CLEAR: {args.check} not locked")
        return 0

    if args.lock:
        h = file_hash(args.lock)
        lock_file(h, state)
        print(f"LOCKED: {args.lock} dispatched at {int(time.time())}")
        return 0

    # Default: report state
    active = len(files)
    print(f"check-subagent-file-dedup: {active} active file lock(s), {len(expired)} expired cleaned")
    return 0 if active == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
