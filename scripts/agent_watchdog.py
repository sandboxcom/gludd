#!/usr/bin/env python3
"""gludd agent unjamming watchdog — detects compulsive-stop patterns and resets.

Polled by a background Makefile target (`make watchdog-start`). Every check cycle:
1. Reads /tmp/gludd-mainthread-streak.json — if streak ≥ 3, resets to 0
2. Reads /tmp/gludd-todowrite-state.json — reports pending items
3. Reads .gludd-session-fix-needed.txt — confirms restart status

When a reset fires, it also writes /tmp/gludd-auto-reset.log with timestamp so
the orchestrator can see how often the agent gets jammed.

Usage:
    make watchdog-start    — launch in background (nohup, PID tracked)
    make watchdog-status   — last 20 lines of log + PID status
    make watchdog-stop     — kill the background watchdog
    make watchdog-log      — full auto-reset log
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STREAK_FILE = "/tmp/gludd-mainthread-streak.json"
TODOWRITE_STATE = os.environ.get("GLUDD_TODOWRITE_STATE", "/tmp/gludd-todowrite-state.json")
RESET_LOG = "/tmp/gludd-auto-reset.log"
HIBERNATION_MARKER = Path("/tmp/gludd-watchdog-hibernating")
POLL_SECS = 60

STREAK_THRESHOLD = 3  # reset streak when ≥ this many consecutive non-dispatch calls


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    ts = _now()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with Path(RESET_LOG).open("a") as f:
        f.write(line + "\n")


def _read_streak() -> int | None:
    try:
        data = json.loads(Path(STREAK_FILE).read_text())
        return int(data.get("count", 0))
    except Exception:
        return None


def _reset_streak() -> None:
    Path(STREAK_FILE).write_text('{"count":0,"last_tool":"reset_by_watchdog"}')
    _log("RESET streak → 0 (unjammed)")


def _pending_todos() -> list[str]:
    try:
        todos = json.loads(Path(TODOWRITE_STATE).read_text())
        return [
            t.get("content", "?")
            for t in todos
            if t.get("status") in ("pending", "in_progress")
        ]
    except Exception:
        return []


def check_and_reset() -> dict:
    result = {
        "ts": _now(),
        "streak": _read_streak(),
        "pending_todos": [],
        "reset_applied": False,
        "hibernating": HIBERNATION_MARKER.exists(),
    }

    pending = _pending_todos()
    result["pending_todos"] = pending

    streak = result["streak"]
    if streak is not None and streak >= STREAK_THRESHOLD:
        _reset_streak()
        result["reset_applied"] = True
        if pending:
            _log(f"UNJAMMED: streak={streak}, pending={len(pending)} todos: {pending[:3]}")
        else:
            _log(f"UNJAMMED: streak={streak}, no pending todos detected but resetting anyway")
    elif streak is not None:
        # low streak, nothing to do
        pass
    else:
        _log("streak file missing — enforcement may not be tracking")

    return result


def main():
    if "--once" in sys.argv:
        result = check_and_reset()
        print(json.dumps(result, indent=2))
        return

    _log(f"watchdog started — poll={POLL_SECS}s, threshold={STREAK_THRESHOLD}")
    while True:
        if HIBERNATION_MARKER.exists():
            _log("hibernation marker present — sleeping")
            time.sleep(POLL_SECS)
            continue
        try:
            check_and_reset()
        except Exception as exc:
            _log(f"error: {exc}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
