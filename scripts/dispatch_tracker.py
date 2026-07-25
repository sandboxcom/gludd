#!/usr/bin/env python3
"""Session-wide cumulative dispatch tracker.

Reinforces the 10-agent floor by tracking cumulative dispatched-vs-completed
counts across an entire session, not per-message.  When floor deficit exists
(= 10 - (dispatched - completed) > 0), the caller should block non-dispatch
tools until replacements are dispatched.

State file: /tmp/gludd-dispatch-state.json (see DISPATCH_STATE_FILE env override)
Format:        {"dispatched": N, "completed": M, "last_updated": <epoch_s>}

Commands (exactly one positional argument):
  add N         Record N new dispatches (increment dispatched).
  complete N    Record N completions (increment completed).
  deficit       Print current floor deficit and exit 1 if >0.
  reset         Reset dispatched + completed to 0.
  status        Print full state as JSON.
"""

import json
import os
import sys
import time
from typing import Dict, Any


DISPATCH_STATE_FILE = os.environ.get(
    "GLUDD_DISPATCH_STATE_FILE", "/tmp/gludd-dispatch-state.json"
)
FLOOR = int(os.environ.get("GLUDD_DISPATCH_FLOOR", "10"))


def _read() -> Dict[str, Any]:
    try:
        if os.path.exists(DISPATCH_STATE_FILE):
            with open(DISPATCH_STATE_FILE, "r") as fh:
                data = json.load(fh)
            return data
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return {"dispatched": 0, "completed": 0, "last_updated": int(time.time())}


def _write(data: Dict[str, Any]) -> None:
    data["last_updated"] = int(time.time())
    tmp = f"{DISPATCH_STATE_FILE}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.rename(tmp, DISPATCH_STATE_FILE)
    except OSError:
        pass


def _deficit(state: Dict[str, Any]) -> int:
    in_flight = max(0, state["dispatched"] - state["completed"])
    return max(0, FLOOR - in_flight)


def cmd_add(n: int) -> None:
    state = _read()
    state["dispatched"] += n
    _write(state)
    deficit = _deficit(state)
    in_flight = max(0, state["dispatched"] - state["completed"])
    print(f"dispatched={state['dispatched']} completed={state['completed']} "
          f"in_flight={in_flight} deficit={deficit}")
    sys.exit(0)


def cmd_complete(n: int) -> None:
    state = _read()
    state["completed"] += n
    _write(state)
    deficit = _deficit(state)
    in_flight = max(0, state["dispatched"] - state["completed"])
    print(f"dispatched={state['dispatched']} completed={state['completed']} "
          f"in_flight={in_flight} deficit={deficit}")
    sys.exit(0)


def cmd_deficit() -> None:
    state = _read()
    deficit = _deficit(state)
    in_flight = max(0, state["dispatched"] - state["completed"])
    print(f"DEFICIT: {deficit} agents below floor (dispatched={state['dispatched']} "
          f"completed={state['completed']} in_flight={in_flight} floor={FLOOR})")
    sys.exit(1 if deficit > 0 else 0)


def cmd_reset() -> None:
    _write({"dispatched": 0, "completed": 0, "last_updated": int(time.time())})
    print("dispatch state reset to 0/0")
    sys.exit(0)


def cmd_status() -> None:
    state = _read()
    print(json.dumps(state))
    sys.exit(0)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: dispatch_tracker.py <add|complete|deficit|reset|status> [N]", file=sys.stderr)
        sys.exit(2)

    cmd = sys.argv[1]

    if cmd == "add":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        cmd_add(n)
    elif cmd == "complete":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        cmd_complete(n)
    elif cmd == "deficit":
        cmd_deficit()
    elif cmd == "reset":
        cmd_reset()
    elif cmd == "status":
        cmd_status()
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
