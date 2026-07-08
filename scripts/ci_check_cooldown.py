#!/usr/bin/env python3
"""Enforce a minimum interval between CI status checks.

Anti-pattern this prevents: an agent dispatches a "push + poll CI until
terminal" subagent that loops ``make ci-verdict`` every 60-90s for 30-40
minutes, holding a subagent slot and the orchestrator's attention while
producing zero value. CI runs on its own schedule; polling it does not
make it finish faster.

Usage::

    make ci-verdict-safe           # checks cooldown, then runs ci-verdict
    make ci-verdict-safe FORCE=1   # bypass cooldown (for release-cut only)
    make deploy-and-forget         # push + record timestamp + print checkback
    make ci-cooldown-status        # show remaining cooldown seconds

State file: ``/tmp/gludd-ci-check-state.json``
    {"last_check_epoch": <float>, "last_push_epoch": <float>,
     "last_head_sha": "<sha>", "check_count": <int>}

Default cooldown: 600s (10 min). Override via ``CI_CHECK_COOLDOWN_SEC``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE_FILE = Path(os.environ.get("GLUDD_CI_STATE_FILE", "/tmp/gludd-ci-check-state.json"))
DEFAULT_COOLDOWN_SEC = int(os.environ.get("CI_CHECK_COOLDOWN_SEC", "600"))


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_check_epoch": 0.0, "last_push_epoch": 0.0, "last_head_sha": "", "check_count": 0}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def remaining_cooldown_sec(state: dict, cooldown: int = DEFAULT_COOLDOWN_SEC) -> float:
    elapsed = time.time() - state.get("last_check_epoch", 0.0)
    return max(0.0, cooldown - elapsed)


def record_check(head_sha: str) -> None:
    state = load_state()
    state["last_check_epoch"] = time.time()
    state["last_head_sha"] = head_sha
    state["check_count"] = state.get("check_count", 0) + 1
    save_state(state)


def record_push(head_sha: str) -> None:
    state = load_state()
    state["last_push_epoch"] = time.time()
    state["last_head_sha"] = head_sha
    save_state(state)


def get_head_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return ""


def cmd_check(cooldown: int, force: bool) -> int:
    state = load_state()
    head_sha = get_head_sha()
    remaining = remaining_cooldown_sec(state, cooldown)
    if remaining > 0 and not force:
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        print(
            f"CI-COOLDOWN: {mins}m{secs}s remaining since last check "
            f"(check #{state.get('check_count', 0)}). "
            f"CI runs on its own schedule; polling does not speed it up. "
            f"Resume real work; check back later with `make ci-verdict-safe`. "
            f"(override with FORCE=1 ONLY for release-cut)",
            file=sys.stderr,
        )
        return 3
    record_check(head_sha)
    print(f"CI-CHECK-{state.get('check_count', 0) + 1}: running ci-verdict for {head_sha[:12]}")
    return 0


def cmd_deploy() -> int:
    head_sha = get_head_sha()
    record_push(head_sha)
    cooldown = DEFAULT_COOLDOWN_SEC
    print(f"DEPLOY-RECORDED: pushed {head_sha[:12]} at epoch {time.time():.0f}")
    print(f"CHECKBACK: run `make ci-verdict-safe` in at least {cooldown // 60} minutes.")
    print("In the meantime: dispatch real work. Do NOT poll.")
    return 0


def cmd_status(cooldown: int) -> int:
    state = load_state()
    remaining = remaining_cooldown_sec(state, cooldown)
    last_check = state.get("last_check_epoch", 0.0)
    last_push = state.get("last_push_epoch", 0.0)
    count = state.get("check_count", 0)
    head = state.get("last_head_sha", "")
    if remaining > 0:
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        print(f"COOLDOWN-ACTIVE: {mins}m{secs}s remaining (check #{count})")
    else:
        print(f"COOLDOWN-EXPIRED: may check now (last check was {int(time.time() - last_check)}s ago)")
    if last_push:
        print(f"  last push: {int(time.time() - last_push)}s ago ({head[:12]})")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ci_check_cooldown.py {check|deploy|status} [cooldown_sec]", file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    cooldown = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_COOLDOWN_SEC
    force = os.environ.get("FORCE", "") == "1"
    if cmd == "check":
        return cmd_check(cooldown, force)
    if cmd == "deploy":
        return cmd_deploy()
    if cmd == "status":
        return cmd_status(cooldown)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
