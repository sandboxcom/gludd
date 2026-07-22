#!/usr/bin/env python3
"""Start the local CI shard replica in the background."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

STATE_FILE = Path(".gate-logs/ci-shards-parallel-state.json")
LOG_DIR = Path(".gate-logs")


def _parse_shards(raw: str) -> list[str]:
    return [item for item in raw.replace(",", " ").split() if item]


def _quote(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", required=True, help="space or comma separated shard names")
    parser.add_argument("--pytest-args", default="")
    parser.add_argument("--workers-per-shard", type=int, default=1)
    args = parser.parse_args()

    shards = _parse_shards(args.shards)
    if not shards:
        print("no shards supplied", file=sys.stderr)
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"ci-shards-parallel-{timestamp}.log"
    command = [
        sys.executable,
        "scripts/run_ci_shards_parallel.py",
        "--shards",
        " ".join(shards),
        "--pytest-args",
        args.pytest_args,
        "--workers-per-shard",
        str(args.workers_per_shard),
    ]

    with log_path.open("wb") as log_file:
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    payload = {
        "pid": process.pid,
        "log": str(log_path),
        "shards": shards,
        "workers_per_shard": args.workers_per_shard,
        "pytest_args": args.pytest_args,
        "command": command,
        "command_text": _quote(command),
        "started_at": datetime.now(UTC).isoformat(),
        "cwd": os.getcwd(),
    }
STATE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CI-SHARDS-BG pid={process.pid} log={log_path} state={STATE_FILE}", flush=True)
    command_text = payload["command_text"]
    print(f"CI-SHARDS-BG command={command_text}", flush=True)
    print("Poll with: make test-ci-shards-parallel-status", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
