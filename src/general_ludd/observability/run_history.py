"""Run-history and artifact API — flight recorder for agent operations.

Provides a unified timeline of job execution, including model calls,
test output, commits, and decisions. Also exposes per-job artifacts
from the filestore.
"""

from __future__ import annotations

import collections
import copy
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_EVENTS_PER_JOB = 10_000


class RunHistoryRecorder:
    def __init__(self, max_events_per_job: int = _DEFAULT_MAX_EVENTS_PER_JOB) -> None:
        self.max_events_per_job = max_events_per_job
        self._timeline: dict[str, collections.deque[dict[str, Any]]] = {}
        self._artifacts: dict[str, dict[str, str]] = {}
        # Optional per-job todo_id override, set when a caller provides todo_id.
        self._job_todo: dict[str, str] = {}

    def record_event(
        self,
        job_id: str,
        event_type: str,
        data: dict[str, Any],
        todo_id: str | None = None,
    ) -> None:
        if job_id not in self._timeline:
            self._timeline[job_id] = collections.deque(maxlen=self.max_events_per_job)
        if todo_id is not None:
            self._job_todo[job_id] = todo_id
        # Deep-copy on store: the caller may keep and mutate `data` after the
        # call; recorded history must be an immutable snapshot, not an alias.
        self._timeline[job_id].append({
            "event_type": event_type,
            "data": copy.deepcopy(data),
            "ts": time.time(),
        })

    def record_artifact(
        self, job_id: str, name: str, content: str,
    ) -> None:
        if job_id not in self._artifacts:
            self._artifacts[job_id] = {}
        self._artifacts[job_id][name] = content

    def get_timeline(self, job_id: str) -> list[dict[str, Any]]:
        # Deep-copy on return: a shallow list() copy still shares the inner
        # event dicts by reference, so a caller mutating a returned record
        # would corrupt stored history.
        raw = self._timeline.get(job_id)
        if raw is None:
            return []
        return copy.deepcopy(list(raw))

    def get_artifacts(self, job_id: str) -> dict[str, str]:
        return dict(self._artifacts.get(job_id, {}))

    def get_summary(self, todo_id: str) -> dict[str, Any]:
        # Reject empty todo_id — it would otherwise match via the override map
        # or the prefix logic and aggregate everything.
        if not todo_id:
            return {"todo_id": todo_id, "event_count": 0, "events": []}
        events: list[dict[str, Any]] = []
        # Exact id, or the id followed by the ":subjob" separator — a proper
        # prefix boundary. Plain `todo_id in job_id` wrongly matched
        # "TODO-1" against "TODO-12" (and "TODO-42" against "TODO-420").
        prefix = f"{todo_id}:"
        for job_id, job_events in self._timeline.items():
            # An explicit todo_id override takes precedence over job_id parsing.
            effective_todo = self._job_todo.get(job_id)
            if effective_todo is not None:
                if effective_todo == todo_id:
                    events.extend(copy.deepcopy(list(job_events)))
            elif job_id == todo_id or job_id.startswith(prefix):
                events.extend(copy.deepcopy(list(job_events)))
        return {
            "todo_id": todo_id,
            "event_count": len(events),
            "events": events,
        }
