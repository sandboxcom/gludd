#!/usr/bin/env python3
"""Parse the active release milestone separately from repository backlog work."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from validate_task_ledger import task_is_effectively_complete
elif __package__:
    from .validate_task_ledger import task_is_effectively_complete
else:  # pragma: no cover - direct script import
    from validate_task_ledger import task_is_effectively_complete

TASK_LINE_RE = re.compile(
    r"^\s*-\s*\[(?P<marker>[ x])\]\s+(?P<task_id>[^ —|]+)(?P<body>.*)$",
    re.MULTILINE,
)
STATUS_RE = re.compile(r"status:\s*(\S+)")
_MILESTONE_RANGE_RE = re.compile(
    r"\b(?P<label>v\d+\.\d+\.\d+)\s+milestone\s+is\s+the\s+exact\s+task\s+set\s+"
    r"(?P<prefix>[A-Za-z]+\d+)\.(?P<start>\d+)\s*[-\u2013]\s*"
    r"(?P<end_prefix>[A-Za-z]+\d+)\.(?P<end>\d+)",
    re.IGNORECASE,
)


class TaskScope(TypedDict):
    """Normalized active-milestone and backlog counters."""

    kind: str
    label: str
    range: str
    ledger: str
    defined_task_count: int
    open_count: int
    backlog_open_count: int
    total_open_count: int


class TaskInventory(TypedDict):
    """Open task identifiers paired with their normalized ownership scope."""

    open_task_ids: list[str]
    task_scope: TaskScope


def _repository_task_inventory(open_task_ids: list[str]) -> TaskInventory:
    """Return the fail-safe unscoped task view when no milestone is declared."""
    return {
        "open_task_ids": open_task_ids,
        "task_scope": {
            "kind": "repository",
            "label": "",
            "range": "",
            "ledger": "TASKS.md",
            "defined_task_count": 0,
            "open_count": len(open_task_ids),
            "backlog_open_count": 0,
            "total_open_count": len(open_task_ids),
        },
    }


def task_inventory(tasks: str) -> TaskInventory:
    """Return active milestone work and repository backlog as distinct counts.

    ``TASKS.md`` is both an evidence ledger and a backlog. The first explicit
    ``<version> milestone is the exact task set <range>`` declaration is the
    active scope because current sessions are kept at the top of the ledger.
    Ledgers without a valid declaration fail safely to repository-wide scope.
    """
    all_open_task_ids: list[str] = []
    for task_match in TASK_LINE_RE.finditer(tasks):
        status_match = STATUS_RE.search(task_match.group("body"))
        task = {
            "status": status_match.group(1) if status_match is not None else None,
        }
        if not task_is_effectively_complete(
            task,
            checked=task_match.group("marker") == "x",
        ):
            task_id = task_match.group("task_id")
            if task_id not in all_open_task_ids:
                all_open_task_ids.append(task_id)
    milestone = _MILESTONE_RANGE_RE.search(tasks)
    if milestone is None or milestone.group("prefix") != milestone.group(
        "end_prefix"
    ):
        return _repository_task_inventory(all_open_task_ids)

    prefix = milestone.group("prefix")
    start = int(milestone.group("start"))
    end = int(milestone.group("end"))
    if end < start:
        return _repository_task_inventory(all_open_task_ids)

    scoped_id = re.compile(rf"{re.escape(prefix)}\.(\d+)", re.IGNORECASE)
    open_task_ids: list[str] = []
    backlog_task_ids: list[str] = []
    for task_id in all_open_task_ids:
        scoped_match = scoped_id.fullmatch(task_id)
        if scoped_match is not None and start <= int(scoped_match.group(1)) <= end:
            open_task_ids.append(task_id)
        else:
            backlog_task_ids.append(task_id)

    return {
        "open_task_ids": open_task_ids,
        "task_scope": {
            "kind": "milestone",
            "label": milestone.group("label"),
            "range": f"{prefix}.{start}-{prefix}.{end}",
            "ledger": "TASKS.md",
            "defined_task_count": end - start + 1,
            "open_count": len(open_task_ids),
            "backlog_open_count": len(backlog_task_ids),
            "total_open_count": len(all_open_task_ids),
        },
    }
