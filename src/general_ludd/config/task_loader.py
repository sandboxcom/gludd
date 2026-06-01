from __future__ import annotations

from pathlib import Path

import yaml

from general_ludd.schemas.task_definition import TaskDefinition


def load_task_definitions(path: str | Path) -> list[TaskDefinition]:
    p = Path(path)
    if not p.exists():
        return []
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    raw_tasks = data.get("tasks", [])
    if not raw_tasks:
        return []
    return [TaskDefinition(**item) for item in raw_tasks]


def discover_task_definitions(*search_paths: str | Path) -> list[TaskDefinition]:
    results: list[TaskDefinition] = []
    for search_path in search_paths:
        p = Path(search_path)
        if not p.is_dir():
            continue
        for yml_file in sorted(p.glob("task*.yml")):
            results.extend(load_task_definitions(yml_file))
    return results
