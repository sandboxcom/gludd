#!/usr/bin/env python3
"""AB037 — track collection error count trend.

Records `make collect-check` results in /tmp/gludd-collect-trend.json.
Three consecutive runs with increasing error count exits non-zero.

State file: /tmp/gludd-collect-trend.json
  {"runs": [{"epoch": N, "errors": N, "total": N}, ...], "max_runs": 10}
"""

import json
import sys
import time
from pathlib import Path

STATE_FILE = Path("/tmp/gludd-collect-trend.json")
MAX_RUNS = 10


def read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"runs": []}


def write_state(state: dict) -> None:
    state["runs"] = state.get("runs", [])[-MAX_RUNS:]
    STATE_FILE.write_text(json.dumps(state))


def record(collect_errors: int, total_tests: int) -> None:
    state = read_state()
    state.setdefault("runs", []).append(
        {
            "epoch": int(time.time()),
            "errors": collect_errors,
            "total": total_tests,
        }
    )
    write_state(state)


def check_trend() -> tuple[bool, list[dict]]:
    state = read_state()
    runs = state.get("runs", [])

    if len(runs) < 3:
        return False, runs

    last_3 = runs[-3:]
    errors = [r.get("errors", 0) for r in last_3]

    increasing = errors[0] < errors[1] < errors[2]
    return increasing, runs


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Track collection error trend")
    parser.add_argument("--record", type=int, help="Record collection error count")
    parser.add_argument("--total", type=int, default=0, help="Total tests collected")
    parser.add_argument("--check", action="store_true", help="Check trend")
    args = parser.parse_args()

    if args.record is not None:
        record(args.record, args.total)
        print(f"check-collect-error-trend: recorded {args.record} errors / {args.total} tests")
        return 0

    if args.check:
        increasing, runs = check_trend()
        if increasing and len(runs) >= 3:
            e = [r["errors"] for r in runs[-3:]]
            print(f"check-collect-error-trend: collection errors INCREASING: {e[0]} → {e[1]} → {e[2]}")
            print("  BLOCKED: fix collection errors before committing.")
            return 1

        last = runs[-1] if runs else {"errors": 0, "total": 0}
        print(
            f"check-collect-error-trend: trend stable ({len(runs)} runs, last: {last.get('errors', 0)} errors / {last.get('total', 0)} tests)"
        )
        return 0

    # Default: show status
    state = read_state()
    runs = state.get("runs", [])
    last = runs[-1] if runs else {"errors": 0, "total": 0}
    print(f"check-collect-error-trend: {len(runs)} runs tracked, last: {last.get('errors', 0)} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
