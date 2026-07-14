#!/usr/bin/env python3
"""Log subagent results to a JSONL file so they survive text blanking.

Usage:
    make log-agent-result AGENT_ID=agent-foo RESULT_SUMMARY="completed: fixed X"

Writes one JSON line to /tmp/gludd-agent-results.jsonl:
    {"ts": 1720800000.123, "agent_id": "agent-foo", "result_summary": "completed: fixed X"}

The file is append-only JSONL — each line is a standalone JSON object.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

RESULTS_FILE = os.environ.get(
    "GLUDD_AGENT_RESULTS_FILE", "/tmp/gludd-agent-results.jsonl"
)

MAX_FILE_SIZE_MB = int(os.environ.get("GLUDD_AGENT_RESULTS_MAX_MB", "10"))


def _rotate_if_needed(path: Path, max_mb: int) -> None:
    if not path.exists():
        return
    sz = path.stat().st_size
    if sz < max_mb * 1024 * 1024:
        return
    keep_bytes = max(0, (max_mb // 2) * 1024 * 1024)
    with path.open("rb") as f:
        f.seek(max(0, sz - keep_bytes))
        f.readline()
        tail = f.read()
    path.write_bytes(tail)


def main() -> None:
    agent_id = os.environ.get("AGENT_ID", "").strip()
    result_summary = os.environ.get("RESULT_SUMMARY", "").strip()

    if not agent_id:
        print("ERROR: AGENT_ID is required", file=sys.stderr)
        sys.exit(1)

    if not result_summary:
        result_summary = "(empty result)"

    entry = {
        "ts": time.time(),
        "agent_id": agent_id,
        "result_summary": result_summary,
    }

    results_path = Path(RESULTS_FILE)
    _rotate_if_needed(results_path, MAX_FILE_SIZE_MB)

    with results_path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
