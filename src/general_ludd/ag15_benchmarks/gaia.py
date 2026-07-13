"""GAIA benchmark loader, runner, and scorer.

Loads GAIA Level 1 validation questions, dispatches to a runner agent,
and scores by normalized string comparison against ground truth answers.
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
    dataset_path = base / "gaia" / "gaia_validation.jsonl"
    if not dataset_path.exists():
        return []
    tasks: list[BenchmarkTask] = []
    for line in dataset_path.read_text().strip().splitlines():
        if not line.strip():
            continue
        instance = json.loads(line)
        tasks.append(BenchmarkTask(
            task_id=instance.get("task_id", str(instance.get("question", "")[:40])),
            description=instance.get("question", ""),
            metadata={
                "level": instance.get("Level", ""),
                "ground_truth": instance.get("Final answer", ""),
                "annotator_metadata": instance.get("Annotator Metadata", {}),
            },
        ))
    return tasks


def score_result(agent_answer: str, task: BenchmarkTask) -> float:
    ground_truth = str(task.metadata.get("ground_truth", ""))
    if not ground_truth:
        return 0.0
    normalized_truth = _normalize(ground_truth)
    normalized_answer = _normalize(agent_answer)
    if normalized_answer == normalized_truth:
        return 1.0
    if normalized_truth in normalized_answer or normalized_answer in normalized_truth:
        return 0.5
    return 0.0


def _normalize(text: str) -> str:
    return text.strip().lower().rstrip(".")
