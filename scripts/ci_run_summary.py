#!/usr/bin/env python3
"""Concise CI run job summary from gh run view JSON on stdin."""
from __future__ import annotations
import json
import sys
from typing import Any

def main() -> int:
    try:
        data: dict[str, Any] = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ci-run-summary: failed to read JSON from stdin: {exc}", file=sys.stderr)
        return 1
    jobs: list[dict[str, Any]] = data.get("jobs", [])
    if not jobs:
        print("No jobs found in CI run.")
        return 0
    failed: list[dict[str, Any]] = []
    running: list[dict[str, Any]] = []
    passed: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for job in jobs:
        name = job.get("name", "?")
        status = job.get("status", "?") or "?"
        conclusion = job.get("conclusion") or "—"
        if conclusion == "failure":
            failed.append(job)
        elif status not in ("completed",):
            running.append(job)
        elif conclusion == "success":
            passed.append(job)
        else:
            other.append(job)
    print(f"{\"JOB\":<32} {\"STATUS\":<12} {\"CONCLUSION\":<12}")
    print("-" * 56)
    for job in failed:
        print(f"FAIL {job.get(\"name\", \"?\"):<29} {(job.get(\"status\", \"?\") or \"?\"):<12} {(job.get(\"conclusion\") or \"—\"):<12}")
    for job in running:
        print(f"··· {job.get(\"name\", \"?\"):<29} {(job.get(\"status\", \"?\") or \"?\"):<12} {(job.get(\"conclusion\") or \"—\"):<12}")
    for job in passed:
        print(f" ✓  {job.get(\"name\", \"?\"):<29} {(job.get(\"status\", \"?\") or \"?\"):<12} {(job.get(\"conclusion\") or \"—\"):<12}")
    for job in other:
        print(f" ?  {job.get(\"name\", \"?\"):<29} {(job.get(\"status\", \"?\") or \"?\"):<12} {(job.get(\"conclusion\") or \"—\"):<12}")
    total = len(jobs)
    print(f"
{len(failed)} failed, {len(running)} running, {len(passed)} passed, {len(other)} other  ({total} total)")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())