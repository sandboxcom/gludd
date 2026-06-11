"""Run-history and artifact API — flight recorder for agent operations.

Provides a unified timeline of job execution, including model calls,
test output, commits, and decisions. Also exposes per-job artifacts
from the filestore.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RunHistoryRecorder:
    def __init__(self) -> None:
        self._timeline: dict[str, list[dict[str, Any]]] = {}
        self._artifacts: dict[str, dict[str, str]] = {}

    def record_event(
        self,
        job_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        if job_id not in self._timeline:
            self._timeline[job_id] = []
        self._timeline[job_id].append({
            "event_type": event_type,
            "data": data,
        })

    def record_artifact(
        self, job_id: str, name: str, content: str,
    ) -> None:
        if job_id not in self._artifacts:
            self._artifacts[job_id] = {}
        self._artifacts[job_id][name] = content

    def get_timeline(self, job_id: str) -> list[dict[str, Any]]:
        return list(self._timeline.get(job_id, []))

    def get_artifacts(self, job_id: str) -> dict[str, str]:
        return dict(self._artifacts.get(job_id, {}))

    def get_summary(self, todo_id: str) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        for job_id, job_events in self._timeline.items():
            if todo_id in job_id:
                events.extend(job_events)
        return {
            "todo_id": todo_id,
            "event_count": len(events),
            "events": events,
        }
