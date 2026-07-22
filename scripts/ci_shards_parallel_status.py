#!/usr/bin/env python3
"""Report status for the background local CI shard replica."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

STATE_FILE = Path(".gate-logs/ci-shards-parallel-state.json")
HEARTBEAT_MARKERS = ("SHARD-HEARTBEAT", "SHARD-PASS", "SHARD-FAIL", "SHARD-SIGNAL", "SHARD-SUMMARY")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tail(path: Path, line_count: int) -> list[str]:
    if not path.exists():
        return [f"log missing: {path}"]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-line_count:]


def _completed_failed(log_text: str) -> bool:
    failure_markers = ("SHARD-FAIL", "SHARD-SIGNAL", "failed=1", "failed=2", "failed=3", "failed=4")
    return any(marker in log_text for marker in failure_markers)


def _age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    return max(0.0, time.time() - path.stat().st_mtime)


def _latest_marker(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if any(marker in line for marker in HEARTBEAT_MARKERS):
            return line
    return None


def _format_age(age: float | None) -> str:
    if age is None:
        return "missing"
    return str(int(age))


def _status_once(line_count: int) -> tuple[bool, bool]:
    if not STATE_FILE.exists():
        print(f"CI-SHARDS-STATUS missing state={STATE_FILE}")
        return False, True

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    pid = int(state.get("pid") or 0)
    log_path = Path(str(state.get("log") or ""))
    alive = _alive(pid) if pid else False
    status = "running" if alive else "finished"
    shards = state.get("shards")
    log_lines = _tail(log_path, 10000)
    log_age = _age_seconds(log_path)
    marker = _latest_marker(log_lines)
    marker_age = log_age if marker else None
    print(
        f"CI-SHARDS-STATUS status={status} pid={pid} log={log_path} shards={shards} "
        f"log_age_seconds={_format_age(log_age)} last_heartbeat_age_seconds={_format_age(marker_age)}",
        flush=True,
    )
    if marker:
        print(f"last_heartbeat={marker}", flush=True)
    else:
        print("last_heartbeat=missing", flush=True)
    print("--- ci shard log tail ---")
    for line in log_lines[-max(1, line_count):]:
        print(line)

    if alive:
        return True, False
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    return False, _completed_failed(log_text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lines", type=int, default=80)
    parser.add_argument("--watch", action="store_true", help="poll until the background run exits")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-polls", type=int, default=0, help="test hook: stop after N polls while still running")
    args = parser.parse_args()

    poll = 0
    while True:
        alive, failed = _status_once(max(1, args.lines))
        poll += 1
        if not args.watch or not alive:
            return 1 if failed else 0
        print(
            f"CI-SHARDS-WATCH heartbeat poll={poll} sleeping={args.interval_seconds:g}s",
            flush=True,
        )
        if args.max_polls and poll >= args.max_polls:
            return 0
        time.sleep(max(0.01, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
