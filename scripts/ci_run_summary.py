#!/usr/bin/env python3
"""Print a concise GitHub Actions job summary from JSON on standard input."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable

Job = dict[str, object]


def _value(job: Job, key: str, default: str = "?") -> str:
    value = job.get(key)
    if value is None or value == "":
        return default
    return str(value)


def _print_jobs(marker: str, jobs: Iterable[Job]) -> None:
    for job in jobs:
        name = _value(job, "name")
        status = _value(job, "status")
        conclusion = _value(job, "conclusion", "—")
        print(f"{marker} {name:<29} {status:<12} {conclusion:<12}")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"ci-run-summary: failed to read JSON from stdin: {exc}",
            file=sys.stderr,
        )
        return 1

    raw_jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    jobs = [job for job in raw_jobs if isinstance(job, dict)]
    if not jobs:
        print("No jobs found in CI run.")
        return 0

    failed: list[Job] = []
    running: list[Job] = []
    passed: list[Job] = []
    other: list[Job] = []
    for job in jobs:
        status = _value(job, "status")
        conclusion = _value(job, "conclusion", "—")
        if conclusion == "failure":
            failed.append(job)
        elif status != "completed":
            running.append(job)
        elif conclusion == "success":
            passed.append(job)
        else:
            other.append(job)

    print(f"{'JOB':<34} {'STATUS':<12} {'CONCLUSION':<12}")
    print("-" * 60)
    _print_jobs("FAIL", failed)
    _print_jobs("··· ", running)
    _print_jobs("PASS", passed)
    _print_jobs("?   ", other)
    print()
    print(
        f"{len(failed)} failed, {len(running)} running, "
        f"{len(passed)} passed, {len(other)} other  ({len(jobs)} total)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
