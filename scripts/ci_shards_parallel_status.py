#!/usr/bin/env python3
"""Report status for the background local CI shard replica."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

STATE_FILE = Path(".gate-logs/ci-shards-parallel-state.json")


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lines", type=int, default=80)
    args = parser.parse_args()

    if not STATE_FILE.exists():
        print(f"CI-SHARDS-STATUS missing state={STATE_FILE}")
        return 1

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    pid = int(state.get("pid") or 0)
    log_path = Path(str(state.get("log") or ""))
    alive = _alive(pid) if pid else False
status = "running" if alive else "finished"
    shards = state.get("shards")
    print(
        f"CI-SHARDS-STATUS status={status} pid={pid} log={log_path} shards={shards}",
        flush=True,
    )
    print("--- ci shard log tail ---")
    tail_lines = _tail(log_path, max(1, args.lines))
    for line in tail_lines:
        print(line)

    if alive:
        return 0
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    if _completed_failed(log_text):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
