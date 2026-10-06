"""Shared records and transcript classification for the watchdog."""

from __future__ import annotations

import enum
import time
from pathlib import Path
from typing import TypedDict


class DeadlineRecord(TypedDict):
    """Normalized task deadline consumed by anomaly checks."""

    id: str
    task_id: str
    type: str
    description: str
    dispatched_at: float
    start_ts: float
    elapsed: float


class DurationFinding(TypedDict, total=False):
    """Duration anomaly evidence emitted by watchdog checks."""

    id: str
    task_id: str
    type: str
    description: str
    elapsed_s: float
    elapsed_seconds: float
    elapsed_minutes: float
    dispatched_at: float
    expected_s: int
    median_seconds: float
    ratio: float
    hard_timeout: bool
    rolling_avg_s: float | None
    threshold_3x_s: float | None
    reason: str


class TaskSeenRecord(TypedDict):
    """Historical state for one observed task."""

    dispatched_at: float
    type: str
    seen_at: float


class TaskHistory(TypedDict):
    """Persisted rolling task-duration history."""

    durations: dict[str, list[float]]
    last_seen: dict[str, TaskSeenRecord]


class TaskTimingRecord(TypedDict):
    """Persisted rolling timing for one named operation."""

    average_duration_seconds: float
    count: int


class DurationStatsRecord(TypedDict):
    """Persisted duration statistics for one tracked task name."""

    last_duration: float
    avg_duration: float
    count: int


class TaskStateRecord(TypedDict, total=False):
    """Normalized running-task state used by the timing monitor."""

    name: str
    started: float
    ended: float
    pid: int


class OperationTiming(TypedDict):
    """Persisted state for one monitored operation."""

    started_at: float
    last_check: float
    duration: float
    status: str


class AnomalyFindings(TypedDict, total=False):
    """Structured findings returned by the task anomaly check."""

    tasks: list[DurationFinding]
    anomalies: list[DurationFinding]
    stalled: list[DurationFinding]
    ts: str
    escalated: bool


class ReleaseData(TypedDict, total=False):
    """Normalized GitHub release metadata."""

    isDraft: bool
    isPrerelease: bool
    assetCount: int
    publishedAt: str
    url: str
    _error: str


class ContinueDirective(TypedDict):
    """Machine-readable watchdog continuation request."""

    action: str
    pending_items: list[str]
    required_tool: str
    dispatch_count: int
    dispatch_commands: list[dict[str, object]]
    message: str
    stop_count: int
    source: str
    ts: str


DEFAULT_WINDOW_SECS: float = 90.0


DONE_MARKERS = ("result:", "summary:", "complete", "finished", "passed", "failed:")


STALL_MARKERS = ("continuing", "let me", "next")


class State(enum.Enum):
    ACTIVE = "ACTIVE"
    LIKELY_STALLED_INCOMPLETE = "LIKELY_STALLED_INCOMPLETE"
    DONE = "DONE"


def classify_tail(tail: str, age_seconds: float, window_seconds: float = DEFAULT_WINDOW_SECS) -> tuple[State, str]:
    if age_seconds < window_seconds:
        return (State.ACTIVE, f"age {age_seconds:.1f}s < window {window_seconds}s")
    tail_lower = tail.lower()
    for marker in DONE_MARKERS:
        if marker in tail_lower:
            return (State.DONE, f"result: found '{marker}' in tail")
    stripped = tail.strip()
    if not stripped:
        return (State.LIKELY_STALLED_INCOMPLETE, "empty tail")
    if stripped.isspace():
        return (State.LIKELY_STALLED_INCOMPLETE, "whitespace-only tail")
    for line in tail_lower.splitlines():
        for marker in STALL_MARKERS:
            if marker in line.strip():
                return (State.LIKELY_STALLED_INCOMPLETE, f"let me / continuing: '{marker}' in tail")
    last_line = stripped.splitlines()[-1].rstrip()
    if last_line.endswith(":"):
        return (State.LIKELY_STALLED_INCOMPLETE, "last line ends with ':'")
    return (State.LIKELY_STALLED_INCOMPLETE, "no completion marker")


def scan_tasks_dir(tasks_dir: Path, window_seconds: float = DEFAULT_WINDOW_SECS) -> list[tuple[str, State, str]]:
    if not tasks_dir.is_dir():
        return []
    results: list[tuple[str, State, str]] = []
    for entry in sorted(tasks_dir.iterdir()):
        if not entry.is_file() or not entry.name.endswith(".output"):
            continue
        try:
            tail = entry.read_text(encoding="utf-8")
        except Exception:
            continue
        mtime = entry.stat().st_mtime
        age = time.time() - mtime
        state, reason = classify_tail(tail, age, window_seconds)
        name = entry.name.removesuffix(".output")
        results.append((name, state, reason))
    return results
