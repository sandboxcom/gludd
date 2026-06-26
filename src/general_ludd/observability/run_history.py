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
# Cap the NUMBER of distinct jobs retained. The per-job deque bounds events
# within one job, but without this the dicts below grow one entry per job_id
# forever — an unbounded leak in a long-running daemon. Oldest job (by first
# sighting) is evicted from all three maps together.
_DEFAULT_MAX_JOBS = 1_000


class RunHistoryRecorder:
    def __init__(
        self,
        max_events_per_job: int = _DEFAULT_MAX_EVENTS_PER_JOB,
        max_jobs: int = _DEFAULT_MAX_JOBS,
    ) -> None:
        self.max_events_per_job = max_events_per_job
        self.max_jobs = max_jobs
        self._timeline: dict[str, collections.deque[dict[str, Any]]] = {}
        self._artifacts: dict[str, dict[str, str]] = {}
        # Optional per-job todo_id override, set when a caller provides todo_id.
        self._job_todo: dict[str, str] = {}
        # FIFO order of job_ids (first-seen) + a membership set, for bounded
        # eviction once the tracked-job count exceeds max_jobs.
        self._job_order: collections.deque[str] = collections.deque()
        self._known_jobs: set[str] = set()

    def _touch_job(self, job_id: str) -> None:
        """Register a (possibly new) job_id and evict the oldest jobs once the
        tracked-job count exceeds ``max_jobs``. FIFO by first sighting — a
        re-recorded job keeps its original position. Evicting drops the job's
        timeline, artifacts, and todo override together."""
        if job_id in self._known_jobs:
            return
        self._known_jobs.add(job_id)
        self._job_order.append(job_id)
        while len(self._job_order) > self.max_jobs:
            oldest = self._job_order.popleft()
            self._known_jobs.discard(oldest)
            self._timeline.pop(oldest, None)
            self._artifacts.pop(oldest, None)
            self._job_todo.pop(oldest, None)

    def record_event(
        self,
        job_id: str,
        event_type: str,
        data: dict[str, Any],
        todo_id: str | None = None,
    ) -> None:
        self._touch_job(job_id)
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
        self._touch_job(job_id)
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
