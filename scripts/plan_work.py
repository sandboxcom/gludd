#!/usr/bin/env python3
"""CLI for the OrchestrationPlanner.

Reads a JSON work-list (from a file path argument or stdin) and prints which
items can run in parallel NOW (batch 0) plus the full ordered batch plan.

Usage::

    # From a JSON file:
    make plan WORK=/tmp/example.json

    # From stdin:
    echo '[{"id": "a", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": false}]' \
        | uv run python scripts/plan_work.py

Work-list JSON format — a list of objects with these keys:
    id          (str)       unique task identifier
    files       (list[str]) files this task will touch (exclusive resources)
    depends_on  (list[str]) ids of tasks that must complete before this one
    is_greenfield (bool)    if true and files is empty, can always run in parallel

Exit 0 on success, 1 on error (cycle detected, bad JSON, etc.).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    # --- Read the work-list from a file arg or stdin ---
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        try:
            raw = path.read_text()
        except OSError as exc:
            print(f"ERROR: cannot read '{path}': {exc}", file=sys.stderr)
            return 1
    else:
        raw = sys.stdin.read()

    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON: {exc}", file=sys.stderr)
        return 1

    if not isinstance(items, list):
        print("ERROR: work-list must be a JSON array of objects.", file=sys.stderr)
        return 1

    # --- Run the planner ---
    try:
        from general_ludd.scheduling.planner import OrchestrationPlanner
        from general_ludd.scheduling.scheduler import CycleError
    except ImportError as exc:
        print(f"ERROR: import failed: {exc}", file=sys.stderr)
        return 1

    planner = OrchestrationPlanner()
    try:
        result = planner.plan_work(items)
    except CycleError as exc:
        print(f"ERROR: dependency cycle detected — {exc}", file=sys.stderr)
        return 1
    except (ValueError, KeyError) as exc:
        print(f"ERROR: invalid work-list — {exc}", file=sys.stderr)
        return 1

    # --- Print the plan ---
    batches: list[list[str]] = result["batches"]
    parallel_now: list[str] = result["parallel_now"]
    serialized: list[tuple[str, str, str]] = result["serialized"]
    explanation: str = result["explanation"]

    total = sum(len(b) for b in batches)
    print(f"Planned {total} item(s) across {len(batches)} batch(es).\n")

    print("=== Batches (ordered; later batches depend on earlier ones) ===")
    for idx, batch in enumerate(batches):
        tag = " <-- run NOW" if idx == 0 else ""
        print(f"  Batch {idx}: {batch}{tag}")

    print(f"\n=== Parallel NOW (batch 0) ===")
    if parallel_now:
        print(f"  Can start immediately: {parallel_now}")
    else:
        print("  (no items)")

    if serialized:
        print("\n=== Serializations (file conflicts) ===")
        for a_id, b_id, shared_file in serialized:
            print(f"  '{a_id}' <-> '{b_id}'  [shared file: {shared_file}]")

    print(f"\n=== Explanation ===")
    print(f"  {explanation}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
