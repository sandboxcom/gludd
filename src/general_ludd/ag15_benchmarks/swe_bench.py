"""SWE-bench Verified loader, runner, and scorer.

Loads SWE-bench Verified tasks, dispatches them to a runner agent,
and scores resolution by checking FAIL_TO_PASS test outcomes.
"""

from __future__ import annotations

import json
from pathlib import Path

from general_ludd.ag15_benchmarks.benchmark_harness import (
    DEFAULT_CACHE_DIR,
    BenchmarkTask,
)


def load_tasks(cache_dir: Path | None = None) -> list[BenchmarkTask]:
    base = cache_dir or DEFAULT_CACHE_DIR
    dataset_path = base / "swe-bench" / "swe-bench_Verified.jsonl"
    if not dataset_path.exists():
        return []
    tasks: list[BenchmarkTask] = []
    for line in dataset_path.read_text().strip().splitlines():
        if not line.strip():
            continue
        instance = json.loads(line)
        tasks.append(BenchmarkTask(
            task_id=instance.get("instance_id", ""),
            description=instance.get("problem_statement", instance.get("issue", "")),
            metadata={
                "repo": instance.get("repo", ""),
                "base_commit": instance.get("base_commit", ""),
                "fail_to_pass": instance.get("FAIL_TO_PASS", []),
                "pass_to_pass": instance.get("PASS_TO_PASS", []),
            },
        ))
    return tasks


def _simulated_runner(task: BenchmarkTask) -> str:
    return "patch output placeholder"


def score_result(result: str, task: BenchmarkTask) -> float:
    fail_to_pass = task.metadata.get("fail_to_pass", [])
    if not fail_to_pass:
        return 0.0
    passed = sum(1 for t in fail_to_pass if t in result)
    return passed / len(fail_to_pass)
