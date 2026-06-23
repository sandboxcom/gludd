#!/usr/bin/env python3
"""Tests for workflow-subagent liveness counting in scripts/agent_liveness.py."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

# Add scripts dir to path so we can import agent_liveness directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_liveness

_FAIL = 0
_CASES: list[tuple] = []


def check(num: int | str, label: str, condition: bool, detail: str = "") -> None:
    global _FAIL
    result = "PASS" if condition else "FAIL"
    if not condition:
        _FAIL += 1
    print(f"Case {str(num):<4} | {result} | {label}" + (f": {detail}" if detail else ""))
    _CASES.append((num, result))


def write_jsonl(path: str, lines: list) -> None:
    with open(path, "w") as f:
        for obj in lines:
            f.write(json.dumps(obj) + "\n")


with tempfile.TemporaryDirectory(prefix="liveness_wf_test_") as d:
    wf_dir = os.path.join(d, "workflows")
    os.makedirs(wf_dir)

    # Workflow file 1: active (recent mtime, no terminal marker)
    wf1 = os.path.join(wf_dir, "wf_active.jsonl")
    write_jsonl(wf1, [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working..."}]}},
    ])

    # Workflow file 2: terminal (has result marker)
    wf2 = os.path.join(wf_dir, "wf_done.jsonl")
    write_jsonl(wf2, [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
        {"type": "result", "subtype": "result"},
    ])

    # Workflow file 3: stale (mtime 120s ago, no terminal marker)
    wf3 = os.path.join(wf_dir, "wf_stale.jsonl")
    write_jsonl(wf3, [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "old"}]}},
    ])
    old_time = time.time() - 120
    os.utime(wf3, (old_time, old_time))

    # Case W1-W4: active workflow counted; terminal + stale excluded
    os.environ["GLUDD_TASKS_DIR"] = os.path.join(d, "nonexistent_tasks")
    os.environ["GLUDD_WORKFLOW_DIRS"] = wf_dir
    os.environ["GLUDD_LIVENESS_WINDOW_SEC"] = "25"

    live, total, _ = agent_liveness.live_count()
    check("W1", "active workflow transcript counted as live", live == 1, f"live={live}")
    check("W2", "workflow transcripts appear in total", total >= 1, f"total={total}")
    check("W3", "terminal workflow NOT counted as live (live==1)", live == 1, f"live={live}")
    check("W4", "stale workflow NOT counted as live (live==1)", live == 1, f"live={live}")

    # Case W5-W6: invalid GLUDD_WORKFLOW_DIRS -> fail-open
    os.environ["GLUDD_WORKFLOW_DIRS"] = "/nonexistent/path/xyz"
    os.environ["GLUDD_TASKS_DIR"] = os.path.join(d, "nonexistent_tasks")
    try:
        live2, total2, _ = agent_liveness.live_count()
        check("W5", "invalid workflow dir: no exception raised", True)
        check("W6", "invalid workflow dir: live=0", live2 == 0, f"live={live2}")
    except Exception as exc:
        check("W5", "invalid workflow dir: no exception raised", False, str(exc))
        check("W6", "invalid workflow dir: live=0", False, "exception raised")

    for k in ("GLUDD_TASKS_DIR", "GLUDD_WORKFLOW_DIRS", "GLUDD_LIVENESS_WINDOW_SEC"):
        os.environ.pop(k, None)

print(f"--- Results: {len(_CASES) - _FAIL} passed, {_FAIL} failed ---")
sys.exit(0 if _FAIL == 0 else 1)
