#!/usr/bin/env python3
"""Agent activity dashboard — reads /tmp/gludd-agent-results.jsonl and prints summary."""
import json
import os
import sys
from pathlib import Path

LOG_PATH = Path(os.environ.get("GLUDD_AGENT_LOG", "/tmp/gludd-agent-results.jsonl"))


def run():
    if not LOG_PATH.exists():
        print(f"No agent log at {LOG_PATH}")
        sys.exit(0)

    entries = []
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not entries:
        print(f"No entries found in {LOG_PATH}")
        sys.exit(0)

    total = len(entries)
    succeeded = sum(1 for e in entries if e.get("status") == "completed" or e.get("exit_code") == 0)
    failed = sum(1 for e in entries if e.get("status") == "failed" or (e.get("exit_code") is not None and e.get("exit_code") != 0))
    success_rate = f"{(succeeded / total * 100):.1f}%" if total else "0.0%"

    tasks_completed = sum(1 for e in entries if e.get("status") == "completed")

    print(f"Total agents:   {total}")
    print(f"Succeeded:      {succeeded}")
    print(f"Failed:         {failed}")
    print(f"Success rate:   {success_rate}")
    print(f"Tasks complete: {tasks_completed}")

    models = {}
    for e in entries:
        model = e.get("model") or "unknown"
        models[model] = models.get(model, 0) + 1
    if models:
        print(f"Models:         {', '.join(f'{m}: {c}' for m, c in sorted(models.items()))}")


if __name__ == "__main__":
    run()
