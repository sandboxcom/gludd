"""Sprint parser — extracts objectives, tasks, and acceptance criteria from sprint markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SprintItem:
    objective_number: int
    title: str
    status: str
    tasks: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)


def parse_sprint_markdown(sprint_path: str) -> list[SprintItem]:
    with open(sprint_path) as f:
        content = f.read()

    items: list[SprintItem] = []
    objective_pattern = re.compile(r"^##\s+Objective\s+(\d+)(?:\s*:\s*(.+))?$", re.MULTILINE)
    status_pattern = re.compile(r"^\s*Status\s*:\s*(.+)$", re.MULTILINE)
    unchecked_pattern = re.compile(r"^\s*-\s*\[\s*\]\s*(.+)$", re.MULTILINE)
    ac_pattern = re.compile(r"^\s*-\s*AC\d+\s*:\s*(.+)$", re.MULTILINE)

    objectives = list(objective_pattern.finditer(content))

    for idx, match in enumerate(objectives):
        obj_num = int(match.group(1))
        title = match.group(2).strip() if match.group(2) else ""
        start = match.end()
        end = objectives[idx + 1].start() if idx + 1 < len(objectives) else len(content)
        section = content[start:end]

        status_match = status_pattern.search(section)
        status = status_match.group(1).strip() if status_match else "unknown"

        tasks = [m.group(1).strip() for m in unchecked_pattern.finditer(section)]
        acceptance_criteria = [m.group(1).strip() for m in ac_pattern.finditer(section)]

        items.append(SprintItem(
            objective_number=obj_num,
            title=title,
            status=status,
            tasks=tasks,
            acceptance_criteria=acceptance_criteria,
        ))

    return items
