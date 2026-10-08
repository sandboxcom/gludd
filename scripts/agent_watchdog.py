#!/usr/bin/env python3
"""gludd agent unjamming watchdog — detects compulsive-stop patterns and resets.

Polled by a background Makefile target (`make watchdog-start`). Every check cycle:
1. Reads /tmp/gludd-mainthread-streak.json — if streak >= 3, resets to 0
2. Reads /tmp/gludd-todowrite-state.json — reports pending items
3. Reads .gludd-session-fix-needed.txt — confirms restart status

When a reset fires, it also writes /tmp/gludd-auto-reset.log with timestamp so
the orchestrator can see how often the agent gets jammed.

STOP DETECTION (per direct user mandate):
- Reads TASKS.md for `- [ ]` / `* [ ]` unchecked items (same pattern as enforce-stop.ts)
- Reads config/ratchet.yml for non-comment, non-empty entries
- Reads .gate-status for FAIL lines
- If ANY pending work exists AND /tmp/gludd-mainthread-streak.json hasn't been
  updated in >15 seconds, the agent is probably sending a text-only response
- Logs "STOP DETECTED: agent idle with pending work", resets streak, writes directive
- Tracks repeated stops in /tmp/gludd-watchdog-stop-count.json; escalates at 3+

Also provides a tail-classification API used by floor_controller.py and tested
in tests/unit/test_agent_watchdog.py:
- State enum: ACTIVE, LIKELY_STALLED_INCOMPLETE, DONE
- classify_tail(tail, age_seconds, window_seconds) -> (State, reason)
- scan_tasks_dir(tasks_dir, window_seconds) -> [(name, State, reason), ...]
- DEFAULT_WINDOW_SECS = 90.0

Usage:
    make watchdog-start    — launch in background (nohup, PID tracked)
    make watchdog-status   — last 20 lines of log + PID status
    make watchdog-stop     — kill the background watchdog
    make watchdog-log      — full auto-reset log
"""

from __future__ import annotations

import glob
import hashlib as hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid as uuid
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts import gludd_env_defaults as gludd_env_defaults
else:
    try:
        from scripts import gludd_env_defaults as gludd_env_defaults
    except ModuleNotFoundError:  # pragma: no cover - direct launch from scripts/
        import gludd_env_defaults
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:  # Direct script execution requires the project root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.resource_arbiter import (
    project_namespace as project_namespace,
)
from scripts.resource_arbiter import (
    resource_path as resource_path,
)
from scripts.watchdog_components import cli as _watchdog_cli
from scripts.watchdog_components import enforcement as _watchdog_enforcement
from scripts.watchdog_components import lease as _watchdog_lease
from scripts.watchdog_components import release as _watchdog_release
from scripts.watchdog_components import task_health as _watchdog_task_health
from scripts.watchdog_components.state_store import (
    _as_float,
    _as_int,
    _as_record,
    _as_text,
    _read_json_record,
)
from scripts.watchdog_components.types import (
    DEFAULT_WINDOW_SECS as DEFAULT_WINDOW_SECS,
)
from scripts.watchdog_components.types import (
    DONE_MARKERS as DONE_MARKERS,
)
from scripts.watchdog_components.types import (
    STALL_MARKERS as STALL_MARKERS,
)
from scripts.watchdog_components.types import (
    AnomalyFindings,
    ContinueDirective,
    DeadlineRecord,
    DurationFinding,
    DurationStatsRecord,
    OperationTiming,
    ReleaseData,
    TaskHistory,
    TaskSeenRecord,
    TaskStateRecord,
    TaskTimingRecord,
)
from scripts.watchdog_components.types import (
    State as State,
)
from scripts.watchdog_components.types import (
    classify_tail as classify_tail,
)
from scripts.watchdog_components.types import (
    scan_tasks_dir as scan_tasks_dir,
)

WatchdogLease = _watchdog_lease.WatchdogLease


class _FacadeRuntime:
    """Resolve every component dependency against this facade at call time."""

    __slots__ = ()

    def __getattr__(self, name: str) -> object:
        return globals()[name]

    def __setattr__(self, name: str, value: object) -> None:
        globals()[name] = value


_WATCHDOG_RUNTIME = _FacadeRuntime()


# -- Classification API -------------------------------------------------------


# -- Streak-reset watchdog ----------------------------------------------------

STREAK_FILE = "/tmp/gludd-mainthread-streak.json"
MULTITASK_STATE_FILE = "/tmp/gludd-multitask-state.json"
WATCHDOG_ACTIVITY_FILE = "/tmp/gludd-watchdog-last-activity.json"
TODOWRITE_STATE = os.environ.get("GLUDD_TODOWRITE_STATE", gludd_env_defaults.TODOWRITE_STATE_DEFAULT)
RESET_LOG = "/tmp/gludd-auto-reset.log"
HIBERNATION_MARKER = Path("/tmp/gludd-watchdog-hibernating")
POLL_SECS = 10

STREAK_THRESHOLD = 3  # with 10s polling, threshold is reached in ~30s of sustained grinding
STOP_IDLE_SECS = 15  # streak file mtime older than this + pending work = text-only stop
AUTO_REENGAGE_AGENT_ACTIVE_SECS = 60  # agent active < this → eligible for auto-re-engage
AUTO_REENGAGE_DISENGAGE_AGE_SECS = 120  # disengage > this old → auto-re-engage when agent active
STOP_COUNT_FILE = "/tmp/gludd-watchdog-stop-count.json"
STOP_ESCALATE_THRESHOLD = 3

FORCE_DISPATCH_FILE = "/tmp/gludd-force-dispatch.json"
FORCE_DISPATCH_MAX_AGE = 120  # seconds — ignore stale force-dispatch flags
FORCE_DISPATCH_IDLE_SECS = 5  # lower idle threshold when force-dispatch is active

PURE_IDLE_SECS = 15
LAST_FLAG_FILE = "/tmp/gludd-watchdog-last-flag.json"
FLAG_COOLDOWN_SECS = 30
PURE_IDLE_DIRECTIVE = "/tmp/gludd-continue.txt"
HEARTBEAT_FILE = "/tmp/gludd-watchdog-heartbeat.json"
HEARTBEAT_VERBOSE = os.environ.get("GLUDD_WATCHDOG_VERBOSE", "0") == "1"

_PLAIN_DIRECTIVE_PRIORITIES: dict[str, int] = {
    "UNDER-FLOOR DETECTED": 10,
    "WATCHDOG CONTINUE DIRECTIVE": 20,
    "TASK ANOMALY": 30,
    "CI STALLED": 40,
    "PUSH STALLED": 50,
}

_CHECK_COOLDOWN_FILE = "/tmp/gludd-watchdog-check-cooldowns.json"
_CHECK_COOLDOWN_SECS = 60

STOP_STATE = os.environ.get("GLUDD_STOP_STATE", "/tmp/gludd-stop-state.json")
FALSE_DONE_BLOCKS = os.environ.get("GLUDD_FALSE_DONE_BLOCKS", "/tmp/gludd-false-done-blocks.json")
FALSE_DONE_MAXOUT = os.environ.get("GLUDD_FALSE_DONE_MAXOUT", "/tmp/gludd-false-done-maxout.json")
CONTINUE_DIRECTIVE = os.environ.get("GLUDD_CONTINUE_DIRECTIVE", "/tmp/gludd-continue-directive.json")
STALLED_TASKS_FILE = os.environ.get("GLUDD_STALLED_TASKS_FILE", "/tmp/gludd-stalled-tasks.txt")
TASK_DEADLINES_FILE = os.environ.get("GLUDD_TASK_DEADLINES_FILE", "/tmp/gludd-task-deadlines.json")

EXPECTED_DURATIONS: dict[str, int] = {
    "git-push": 30,
    "git_push": 30,
    "push": 30,
    "git-status": 5,
    "ci-verdict": 10,
    "ci-run": 1800,
    "lint": 30,
    "typecheck": 30,
    "collect-check": 60,
    "test-specific": 120,
    "test-unit": 600,
    "gate": 2400,
    "gate-run": 2400,
    "test": 30,
    "commit": 10,
    "git-commit": 10,
    "git-add": 5,
    "test-iso": 60,
    "research": 120,
    "subagent-task": 300,
    "general": 300,
    "default": 300,
}

_alerted_anomalies: dict[str, float] = {}
_ALERTED_PRUNE_SECS = 1800  # 30 minutes
_POLL_CYCLE_COUNT = 0
_POLL_CYCLE_PRUNE_INTERVAL = 100  # prune every ~17 min (100 * 10s)

LOAD_THROTTLE_FILE = "/tmp/gludd-load-throttle"
LOAD_WARN = 8
LOAD_THROTTLE = 12
LOAD_HARD = 20
MAX_CHILD_PROCESSES = 30

WATCHDOG_LOG_ROTATION_MB = 10
WATCHDOG_LOG_KEEP_MB = 1
WATCHDOG_LOG_DIR = Path("/tmp")
WATCHDOG_LOG_ROTATE_INTERVAL_SECS = 600
_WATCHDOG_LAST_LOG_ROTATE: float = 0.0
WATCHDOG_LOG_ROTATE_SKIP_PATTERNS = ("gludd-stderr-", "gludd-stdout-", "gludd-stdio-")


def _rotate_watchdog_logs() -> None:
    """Truncate /tmp/gludd-*.log files that exceed WATCHDOG_LOG_ROTATION_MB.

    Keeps only the last WATCHDOG_LOG_KEEP_MB of content, so a runaway log
    (e.g. a plugin debug log writing to /tmp) never fills the drive.
    Called from the watchdog main loop at most every WATCHDOG_LOG_ROTATE_INTERVAL_SECS.
    """
    global _WATCHDOG_LAST_LOG_ROTATE
    now = time.time()
    if now - _WATCHDOG_LAST_LOG_ROTATE < WATCHDOG_LOG_ROTATE_INTERVAL_SECS:
        return
    _WATCHDOG_LAST_LOG_ROTATE = now

    for pattern in ("/tmp/gludd-*.log", "/tmp/gludd-*.warnings.log"):
        for log_path_str in glob.glob(pattern):
            log_path = Path(log_path_str)
            if not log_path.is_file():
                continue
            name = log_path.name
            if any(name.startswith(p) for p in WATCHDOG_LOG_ROTATE_SKIP_PATTERNS):
                continue
            try:
                sz = log_path.stat().st_size
                if sz < WATCHDOG_LOG_ROTATION_MB * 1024 * 1024:
                    continue
                keep_bytes = WATCHDOG_LOG_KEEP_MB * 1024 * 1024
                with log_path.open("rb") as f:
                    f.seek(max(0, sz - keep_bytes))
                    f.readline()
                    tail = f.read()
                log_path.write_bytes(tail)
                new_sz = log_path.stat().st_size
                _log(
                    f"LOG ROTATION: {name} {sz / (1024 * 1024):.1f}MB → "
                    f"{new_sz / (1024 * 1024):.1f}MB "
                    f"(threshold {WATCHDOG_LOG_ROTATION_MB}MB)"
                )
            except Exception:
                pass


def _prune_alerted_anomalies(now_epoch: float | None = None) -> None:
    """Remove entries older than _ALERTED_PRUNE_SECS from _alerted_anomalies."""
    global _alerted_anomalies
    if now_epoch is None:
        now_epoch = time.time()
    cutoff = now_epoch - _ALERTED_PRUNE_SECS
    _alerted_anomalies = {k: v for k, v in _alerted_anomalies.items() if v > cutoff}


ANOMALY_COUNT_FILE = "/tmp/gludd-watchdog-anomaly-count.json"
TASK_ANOMALIES_FILE = "/tmp/gludd-task-anomalies.json"
ANOMALY_ESCALATE_THRESHOLD = 5

TASK_TIMING_FILE = "/tmp/gludd-task-timings.json"
ANOMALY_MULTIPLIER = 3.0
TASK_HISTORY_FILE = "/tmp/gludd-task-history.json"
MAX_HISTORY_PER_TYPE = 5
TASK_STALL_TIMEOUT = 120
TASK_STATE_FILE = "/tmp/gludd-task-state.json"
TASK_STATE_SNAPSHOT = "/tmp/gludd-task-state-snapshot.json"

CI_CACHE_FILE = "/tmp/gludd-watchdog-ci.json"
DURATIONS_FILE = "/tmp/gludd-watchdog-durations.json"
CI_CHECK_INTERVAL = 300
CI_STALL_MINUTES = 10
CI_VERDICT_TIMEOUT = 15
VERIFY_REMOTE_TIMEOUT = 10

ORCHESTRATOR_STATE_FILE = "/tmp/gludd-orchestrator-state.json"
DISENGAGE_FILE = "/tmp/gludd-watchdog-disengage.json"
BLOCK_COUNTER_FILE = "/tmp/gludd-block-counter.json"
DISENGAGE_MAX_SECS_CI_NOT_GREEN = 300  # 5 min cap when CI is pending/red
HEALTH_SCORE_FILE = "/tmp/gludd-health-score.json"
PUSH_LOOP_FILE = "/tmp/gludd-watchdog-push-timestamps.json"
CI_LOOP_THRESHOLD_PUSHES = 3
CI_LOOP_THRESHOLD_MINUTES = 10
CI_TRUE_STALL_MINUTES = 45
CI_TRUE_STALL_NO_PUSH_MINUTES = 15

_WORKSPACE = Path(os.environ.get("GLUDD_WORKSPACE_ROOT", os.getcwd()))
GATE_PID_FILE = _WORKSPACE / ".gate-background.pid"
GATE_MAX_RUNTIME_SECS = int(os.environ.get("GATE_WATCHDOG_TIMEOUT", "21600"))
_TASKS_MD = _WORKSPACE / "TASKS.md"
_RATCHET_YML = _WORKSPACE / "config" / "ratchet.yml"
_GATE_STATUS = _WORKSPACE / ".gate-status"
_CI_STATUS = _WORKSPACE / ".ci-status"

# A watchdog is a singleton per project namespace.  The lock contains enough
# identity to reject PID reuse and enough version information for an upgraded
# daemon to retire an older implementation safely.
WATCHDOG_VERSION = os.environ.get("GLUDD_WATCHDOG_VERSION", "1.0")
WATCHDOG_LOCK_RESOURCE = "agent-watchdog"


def watchdog_lock_path(workspace: Path | str | None = None) -> Path:
    """Return the project-namespaced singleton lock path."""
    return _watchdog_lease.watchdog_lock_path(_WATCHDOG_RUNTIME, workspace)


def _process_start_time(pid: int) -> str | None:
    """Read a process start token where the host exposes one.

    Linux exposes a monotonic start tick in ``/proc``.  macOS and other hosts
    fall back to ``ps``'s start-date string.  A missing token is acceptable:
    liveness is still checked with ``kill(pid, 0)`` and stale owners recover
    once their process exits.
    """
    return _watchdog_lease._process_start_time(_WATCHDOG_RUNTIME, pid)


def _version_key(version: str) -> tuple[tuple[int, ...], str]:
    """Compare semantic-ish watchdog versions without requiring packaging."""
    return _watchdog_lease._version_key(_WATCHDOG_RUNTIME, version)


def _owner_is_alive(owner: dict[str, object]) -> bool:
    return _watchdog_lease._owner_is_alive(_WATCHDOG_RUNTIME, owner)


def _read_lock_owner(path: Path) -> dict[str, object] | None:
    return _watchdog_lease._read_lock_owner(_WATCHDOG_RUNTIME, path)


def _unlink_if_token_matches(path: Path, token: str | None) -> None:
    """Remove a lock only if it still refers to the owner we inspected."""
    return _watchdog_lease._unlink_if_token_matches(_WATCHDOG_RUNTIME, path, token)


def acquire_watchdog_lock(
    *, lock_path: Path | str | None = None, version: str | None = None, pid: int | None = None
) -> WatchdogLease | None:
    """Acquire the singleton watchdog lease, recovering stale owners.

    A live owner with the same or newer version wins.  A newer caller sends a
    polite ``SIGTERM`` to an older live owner, then replaces its record.  The
    token check in :func:`release_watchdog_lock` prevents an old process from
    deleting the replacement lock during shutdown.
    """
    return _watchdog_lease.acquire_watchdog_lock(_WATCHDOG_RUNTIME, lock_path=lock_path, version=version, pid=pid)


def release_watchdog_lock(lease: WatchdogLease | None) -> None:
    """Release a lease without touching a newer owner's lock record."""
    return _watchdog_lease.release_watchdog_lock(_WATCHDOG_RUNTIME, lease)


def stop_watchdog(*, lock_path: Path | str | None = None) -> bool:
    """Request shutdown of this project's watchdog without global ``pkill``.

    A live owner's lock is intentionally left in place for its ``finally``
    block to release.  Dead or malformed records are removed immediately.
    """
    return _watchdog_lease.stop_watchdog(_WATCHDOG_RUNTIME, lock_path=lock_path)


_UNCHECKED_PATTERN = re.compile(r"-\s+\[\s*\]|\*\s+\[\s*\]", re.IGNORECASE)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    ts = _now()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with Path(RESET_LOG).open("a") as f:
        f.write(line + "\n")


def _max_out_false_done() -> None:
    """Increment false-done anti-wedge counter with wrapping to prevent saturation.

    The counter wraps at 100 so the escalation gradient never collapses:
    0 → 1 → ... → 100 → 0 → 1 → ... (cycling, not stuck at cap).
    Counter is reset to 0 when the agent is active (mtime_age < PURE_IDLE_SECS),
    which means the agent made a recent tool call and the wedge is clearing.
    """
    try:
        p = Path(FALSE_DONE_MAXOUT)
        count = 0
        if p.exists():
            try:
                data = json.loads(p.read_text())
                count = int(data.get("count", 0))
            except Exception:
                count = 0
        mtime_age = _streak_mtime_age_seconds()
        count = 0 if mtime_age is not None and mtime_age < PURE_IDLE_SECS else count % 100 + 1
        p.write_text(json.dumps({"count": count, "ts": time.time()}))
    except Exception:
        pass


def _read_streak() -> int | None:
    try:
        data = json.loads(Path(STREAK_FILE).read_text())
        return int(data.get("count", 0))
    except Exception:
        return None


def _reset_streak() -> None:
    Path(STREAK_FILE).write_text('{"count":0,"last_tool":"reset_by_watchdog"}')


def _pending_todos() -> list[str]:
    try:
        todos = json.loads(Path(TODOWRITE_STATE).read_text())
        return [t.get("content", "?") for t in todos if t.get("status") in ("pending", "in_progress")]
    except Exception:
        return []


# -- Task duration anomaly detection ------------------------------------------

DEFAULT_STALL_MINUTES = 5.0
DEFAULT_ANOMALY_MULTIPLIER = 5.0
ANOMALY_DIRECTIVE = "/tmp/gludd-continue.txt"


def _read_deadlines() -> list[DeadlineRecord]:
    try:
        p = Path(TASK_DEADLINES_FILE)
        if not p.exists():
            return []
        decoded: object = json.loads(p.read_text())
        data = _as_record(decoded)
        entries = data.get("tasks", data) if data is not None else decoded
        if not isinstance(entries, list):
            return []
        now = time.time()
        result: list[DeadlineRecord] = []
        for raw_entry in entries:
            entry = _as_record(raw_entry)
            if entry is None:
                continue
            task_id = _as_text(entry.get("task_id", entry.get("id", "?")), "?")
            start_ts = _as_float(entry.get("dispatched_at", entry.get("start_ts", entry.get("start", 0))))
            if start_ts <= 0:
                continue
            elapsed = now - start_ts
            result.append(
                {
                    "id": task_id,
                    "task_id": task_id,
                    "type": _as_text(entry.get("type"), _guess_task_type(task_id)),
                    "description": _as_text(entry.get("description"), task_id),
                    "dispatched_at": start_ts,
                    "start_ts": start_ts,
                    "elapsed": elapsed,
                }
            )
        return result
    except Exception:
        return []


def _detect_stalled_tasks(
    deadlines: list[DeadlineRecord],
    max_minutes: float = DEFAULT_STALL_MINUTES,
) -> list[DurationFinding]:
    max_seconds = max_minutes * 60.0
    stalled: list[DurationFinding] = []
    for d in deadlines:
        if d["elapsed"] > max_seconds:
            stalled.append(
                {
                    "task_id": d["task_id"],
                    "elapsed_seconds": round(d["elapsed"], 1),
                    "elapsed_minutes": round(d["elapsed"] / 60.0, 1),
                }
            )
    return stalled


def _detect_anomalies(
    deadlines: list[DeadlineRecord],
    multiplier: float = DEFAULT_ANOMALY_MULTIPLIER,
) -> list[DurationFinding]:
    if len(deadlines) < 3:
        return []
    elapsed_values = sorted(d["elapsed"] for d in deadlines if d["elapsed"] > 0)
    if not elapsed_values:
        return []
    n = len(elapsed_values)
    median = (elapsed_values[n // 2 - 1] + elapsed_values[n // 2]) / 2.0 if n % 2 == 0 else elapsed_values[n // 2]
    if median <= 0:
        return []
    anomalies: list[DurationFinding] = []
    for d in deadlines:
        if d["elapsed"] > median * multiplier:
            anomalies.append(
                {
                    "task_id": d["task_id"],
                    "elapsed_seconds": round(d["elapsed"], 1),
                    "elapsed_minutes": round(d["elapsed"] / 60.0, 1),
                    "median_seconds": round(median, 1),
                    "ratio": round(d["elapsed"] / median, 2) if median > 0 else 0,
                }
            )
    return anomalies


def _read_task_history() -> TaskHistory:
    empty: TaskHistory = {"durations": {}, "last_seen": {}}
    try:
        p = Path(TASK_HISTORY_FILE)
        if not p.exists():
            return empty
        raw: object = json.loads(p.read_text())
    except Exception:
        return empty

    record = _as_record(raw)
    if record is None:
        return empty

    durations: dict[str, list[float]] = {}
    raw_durations = _as_record(record.get("durations")) or {}
    for task_type, values in raw_durations.items():
        if isinstance(values, list):
            durations[task_type] = [_as_float(value) for value in values if isinstance(value, (int, float, str))]

    last_seen: dict[str, TaskSeenRecord] = {}
    raw_last_seen = _as_record(record.get("last_seen")) or {}
    for task_id, value in raw_last_seen.items():
        seen = _as_record(value)
        if seen is None:
            continue
        last_seen[task_id] = {
            "dispatched_at": _as_float(seen.get("dispatched_at")),
            "type": _as_text(seen.get("type"), "unknown"),
            "seen_at": _as_float(seen.get("seen_at")),
        }
    return {"durations": durations, "last_seen": last_seen}


def _write_task_history(history: TaskHistory) -> None:
    with suppress(Exception):
        Path(TASK_HISTORY_FILE).write_text(json.dumps(history, indent=2))


def _rolling_avg_by_type(history: TaskHistory, task_type: str) -> float | None:
    durations = history["durations"].get(task_type, [])
    if not durations:
        return None
    recent = durations[-MAX_HISTORY_PER_TYPE:]
    return sum(recent) / len(recent)


def _update_task_history(deadlines: list[DeadlineRecord]) -> None:
    history = _read_task_history()
    now = time.time()
    current_ids = {d.get("id", d.get("task_id", "")) for d in deadlines}

    last_seen = history["last_seen"]
    durations_by_type = history["durations"]

    for task_id, seen_info in list(last_seen.items()):
        if task_id not in current_ids:
            dispatched_at = seen_info["dispatched_at"]
            task_type = seen_info["type"]
            if dispatched_at > 0:
                duration = now - dispatched_at
                if task_type not in durations_by_type:
                    durations_by_type[task_type] = []
                durations_by_type[task_type].append(duration)
                if len(durations_by_type[task_type]) > MAX_HISTORY_PER_TYPE:
                    durations_by_type[task_type] = durations_by_type[task_type][-MAX_HISTORY_PER_TYPE:]
            del last_seen[task_id]

    for d in deadlines:
        task_id = d["id"] or d["task_id"]
        if task_id and task_id not in last_seen:
            last_seen[task_id] = {
                "dispatched_at": d["dispatched_at"],
                "type": d["type"],
                "seen_at": now,
            }

    history["durations"] = durations_by_type
    history["last_seen"] = last_seen
    _write_task_history(history)


def _detect_history_anomalies(
    deadlines: list[DeadlineRecord],
    timeout_secs: float = 300.0,
    multiplier: float = 3.0,
) -> list[DurationFinding]:
    history = _read_task_history()
    anomalies: list[DurationFinding] = []

    for d in deadlines:
        elapsed = d["elapsed"]
        task_id = d["id"] or d["task_id"]
        task_type = d["type"]
        description = d["description"]
        dispatched_at = d["dispatched_at"]

        if elapsed <= 0:
            continue

        hard_timeout = elapsed > timeout_secs
        rolling_avg = _rolling_avg_by_type(history, task_type)
        history_anomaly = rolling_avg is not None and elapsed > rolling_avg * multiplier

        if hard_timeout or history_anomaly:
            entry: DurationFinding = {
                "id": task_id,
                "type": task_type,
                "description": description,
                "elapsed_s": round(elapsed, 1),
                "dispatched_at": dispatched_at,
                "hard_timeout": hard_timeout,
                "rolling_avg_s": round(rolling_avg, 1) if rolling_avg else None,
                "threshold_3x_s": round(rolling_avg * multiplier, 1) if rolling_avg else None,
                "reason": "hard_timeout_300s" if hard_timeout else "history_3x_average",
            }
            anomalies.append(entry)

    return anomalies


# -- Stop-detection helpers (mirror enforce-stop.ts logic) --------------------


def _tasks_md_has_unchecked() -> bool:
    try:
        if not _TASKS_MD.exists():
            return False
        content = _TASKS_MD.read_text(encoding="utf-8")
        return bool(_UNCHECKED_PATTERN.search(content))
    except Exception:
        return False


def _ratchet_has_entries() -> int:
    try:
        if not _RATCHET_YML.exists():
            return 0
        content = _RATCHET_YML.read_text(encoding="utf-8")
        count = 0
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" in stripped:
                count += 1
        return count
    except Exception:
        return 0


def _gate_status_is_red() -> bool:
    try:
        if not _GATE_STATUS.exists():
            return False
        content = _GATE_STATUS.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("==="):
                continue
            if "FAIL" in line:
                return True
        return False
    except Exception:
        return False


def _pending_work_exists() -> bool:
    return _tasks_md_has_unchecked() or _ratchet_has_entries() > 0 or _gate_status_is_red()


def _ci_is_pending_or_red() -> tuple[bool, str | None]:
    """Check if CI is pending (in-flight work) or red (broken work).

    Returns (has_ci_work, run_id_or_None).
    CI-pending means the agent has work waiting on external validation.
    CI-red means the agent has broken work that needs fixing.
    """
    try:
        result = subprocess.run(
            ["make", "ci-verdict", "BRANCH=master"],
            capture_output=True,
            text=True,
            timeout=CI_VERDICT_TIMEOUT,
            cwd=str(_WORKSPACE),
        )
        output = (result.stdout + result.stderr).upper()
        if "SUCCESS" in output:
            return False, None
        run_id_match = re.search(r"run[_\s]?[\s:=]?\s*(\d+)", output, re.IGNORECASE)
        run_id = run_id_match.group(1) if run_id_match else None
        if any(status in output for status in ("PENDING", "IN_PROGRESS", "QUEUED", "WAITING")):
            return True, run_id
        if "RED" in output or "FAILURE" in output:
            return True, run_id
        return False, run_id
    except Exception:
        return False, None


def _ci_pending_for_too_long_minutes() -> float | None:
    """Return how many minutes CI has been pending, or None if CI is not pending."""
    try:
        p = Path(CI_CACHE_FILE)
        if not p.exists():
            return None
        data = _read_json_record(p)
        last_check = _as_float(data.get("last_ci_check"))
        if last_check > 0 and (time.time() - last_check) > 300:
            return 0.0
        first_seen = _as_float(data.get("pending_first_seen"))
        if first_seen <= 0:
            return None
        return (time.time() - first_seen) / 60.0
    except Exception:
        return None


def _write_watchdog_activity(ts: float | None = None) -> None:
    ts_value = ts if ts is not None else time.time()
    Path(WATCHDOG_ACTIVITY_FILE).write_text(json.dumps({"last_activity_ts": ts_value}))


def _read_watchdog_activity_age() -> float | None:
    p = Path(WATCHDOG_ACTIVITY_FILE)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        ts = float(data.get("last_activity_ts", 0))
        if ts <= 0:
            return None
        return time.time() - ts
    except Exception:
        return None


def _streak_mtime_age_seconds() -> float | None:
    streak_path = Path(STREAK_FILE)
    if not streak_path.exists():
        return _read_watchdog_activity_age()
    return time.time() - streak_path.stat().st_mtime


# -- Repeated stop escalation ------------------------------------------------


def _read_stop_count() -> int:
    try:
        p = Path(STOP_COUNT_FILE)
        if not p.exists():
            return 0
        data = json.loads(p.read_text())
        return int(data.get("count", 0))
    except Exception:
        return 0


def _write_stop_count(count: int) -> None:
    Path(STOP_COUNT_FILE).write_text(json.dumps({"count": count}))


def _increment_stop_count() -> int:
    new_count = _read_stop_count() + 1
    _write_stop_count(new_count)
    return new_count


def _clear_stop_count() -> None:
    Path(STOP_COUNT_FILE).write_text('{"count":0}')


def _read_anomaly_counts() -> dict[str, int]:
    try:
        p = Path(ANOMALY_COUNT_FILE)
        if not p.exists():
            return {}
        data = json.loads(p.read_text())
        counts: dict[str, int] = {}
        for k, v in data.items():
            counts[k] = int(v)
        return counts
    except Exception:
        return {}


def check_running_tasks() -> None:
    try:
        p = Path(TASK_DEADLINES_FILE)
        if not p.exists():
            return
        data = json.loads(p.read_text())
    except Exception:
        return

    if not isinstance(data, list):
        return

    now = time.time()
    stalled_names: list[str] = []

    for task in data:
        if not isinstance(task, dict):
            continue
        start_ts = task.get("start_ts")
        task_name = task.get("task_name", "")
        task_id = task.get("task_id", "")
        if not start_ts:
            continue
        try:
            start_ts = float(start_ts)
        except (TypeError, ValueError):
            continue

        elapsed = now - start_ts
        expected = EXPECTED_DURATIONS.get(task_name, EXPECTED_DURATIONS["default"])
        anomaly_key = task_name or task_id or "unknown"

        if elapsed > expected * 10:
            _log(f"TASK STALLED: {task_name} running {elapsed:.0f}s vs expected {expected}s (task_id={task_id})")
            stalled_names.append(task_name or task_id)
            counts = _read_anomaly_counts()
            cnt = _increment_anomaly_count(f"stalled:{anomaly_key}", counts)
            if cnt >= ANOMALY_ESCALATE_THRESHOLD:
                _log(f"TASK ANOMALY ESCALATED: {anomaly_key} stalled {cnt}x — may need intervention")
        elif elapsed > expected * 3:
            _log(f"TASK ANOMALY: {task_name} running {elapsed:.0f}s vs expected {expected}s (task_id={task_id})")
            counts = _read_anomaly_counts()
            cnt = _increment_anomaly_count(f"anomaly:{anomaly_key}", counts)
            if cnt >= ANOMALY_ESCALATE_THRESHOLD:
                _log(f"TASK ANOMALY ESCALATED: {anomaly_key} anomaly {cnt}x — may need intervention")

    if stalled_names:
        Path(STALLED_TASKS_FILE).write_text("\n".join(stalled_names) + "\n")


def check_push_status() -> None:
    import subprocess as _subprocess

    try:
        result = _subprocess.run(
            ["make", "git-status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout + result.stderr
        if "ahead of" in output.strip():
            _log("PUSH ANOMALY: local branch ahead of remote — push may have stalled")
            counts = _read_anomaly_counts()
            cnt = _increment_anomaly_count("push:ahead_of_remote", counts)
            if cnt >= ANOMALY_ESCALATE_THRESHOLD:
                _log(f"PUSH ANOMALY ESCALATED: ahead-of-remote detected {cnt}x")
    except Exception:
        pass

    try:
        streak_path = Path(STREAK_FILE)
        if streak_path.exists():
            data = json.loads(streak_path.read_text())
            count = int(data.get("count", 0))
            if count > 5:
                _log(f"PUSH STATUS: mainthread streak={count} — push may not have occurred recently")
    except Exception:
        pass


def _get_local_head() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(_WORKSPACE),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _should_check_ci() -> bool:
    try:
        p = Path(CI_CACHE_FILE)
        if p.exists():
            data = _read_json_record(p)
            last_check = _as_float(data.get("last_ci_check"))
            if time.time() - last_check < CI_CHECK_INTERVAL:
                return False
    except Exception:
        pass
    return True


def _update_ci_cache(**kwargs: object) -> None:
    p = Path(CI_CACHE_FILE)
    existing: dict[str, object] = {}
    try:
        if p.exists():
            existing = _read_json_record(p)
    except Exception:
        pass
    existing.update(kwargs)
    existing["last_ci_check"] = time.time()
    p.write_text(json.dumps(existing))


def _check_ci_stall() -> None:
    if not _should_check_ci():
        return

    try:
        result = subprocess.run(
            ["make", "ci-verdict", "BRANCH=master"],
            capture_output=True,
            text=True,
            timeout=CI_VERDICT_TIMEOUT,
            cwd=str(_WORKSPACE),
        )
        output = result.stdout + result.stderr
        _update_ci_cache(last_output=output[:200])

        status_match = re.search(r"conclusion:\s*(\S+)", output)
        run_id_match = re.search(r"run[_\s]?id[:=]?\s*(\d+)", output, re.IGNORECASE)

        status = status_match.group(1) if status_match else "UNKNOWN"
        run_id = run_id_match.group(1) if run_id_match else None

        ci_data: dict[str, object] = {}
        try:
            p = Path(CI_CACHE_FILE)
            if p.exists():
                ci_data = _read_json_record(p)
        except Exception:
            pass

        last_run_id = ci_data.get("last_ci_run_id")
        first_seen = _as_float(ci_data.get("pending_first_seen"))

        if status.upper() == "FAILURE":
            if run_id and run_id != last_run_id:
                _log(f"CI FAILED: run {run_id} — {output[:200]}")
                _update_ci_cache(last_ci_run_id=run_id)
            elif status != ci_data.get("last_ci_status"):
                _log(f"CI FAILED: {output[:200]}")
            _update_ci_cache(last_ci_status=status)

        elif status.upper() in ("PENDING", "IN_PROGRESS", "QUEUED", "WAITING"):
            if run_id and run_id != last_run_id:
                first_seen = time.time()
                _update_ci_cache(last_ci_run_id=run_id, pending_first_seen=first_seen, last_ci_status=status)
            elif first_seen and time.time() - first_seen > CI_STALL_MINUTES * 60:
                _log(f"CI STALLED: run {run_id} pending >{CI_STALL_MINUTES}min")
        else:
            _update_ci_cache(last_ci_status=status)

    except subprocess.TimeoutExpired:
        _log("CI CHECK TIMEOUT: ci-verdict took >15s")
    except Exception as e:
        _log(f"CI check error: {e}")


def _check_gate_background() -> None:
    """Kill background gate if it has been running for > GATE_MAX_RUNTIME_SECS.

    Also checks for stale .gate-status when no gate is running.
    Runs every watchdog poll cycle (default 10s).
    """
    gate_running = False
    pid_str = ""
    pid = 0

    try:
        if GATE_PID_FILE.exists():
            pid_str = GATE_PID_FILE.read_text().strip()
            if pid_str:
                pid = int(pid_str)
                os.kill(pid, 0)
                gate_running = True
    except (ValueError, ProcessLookupError):
        GATE_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    if gate_running:
        try:
            elapsed = time.time() - GATE_PID_FILE.stat().st_mtime
        except Exception:
            return

        if elapsed > GATE_MAX_RUNTIME_SECS:
            _log(
                "GATE STALLED: background gate "
                f"pid={pid_str} running {elapsed:.0f}s "
                f"(>{GATE_MAX_RUNTIME_SECS}s) - auto-killing"
            )
            with suppress(Exception):
                _GATE_STATUS.write_text("GATE_TIMEOUT\n=== GATE: ABORTED (watchdog timeout) ===\n")
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(10)
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
                GATE_PID_FILE.unlink(missing_ok=True)
                _log(f"GATE KILLED: pid={pid_str} after {elapsed:.0f}s")
            except Exception as exc:
                _log(f"GATE KILL ERROR: pid={pid_str} {exc}")

    elif _GATE_STATUS.exists():
        try:
            mtime = _GATE_STATUS.stat().st_mtime
            age = time.time() - mtime
            if age > 3600:
                _log(f"GATE STATUS STALE: .gate-status is {age:.0f}s old (>1h) with no gate running")
        except Exception:
            pass


def _check_push_health() -> None:
    try:
        local_head = _get_local_head()
        if not local_head:
            return

        result = subprocess.run(
            ["sh", "-c", "git log --oneline @{u}..HEAD 2>&1 | wc -l"],
            capture_output=True,
            text=True,
            timeout=VERIFY_REMOTE_TIMEOUT,
            cwd=str(_WORKSPACE),
        )
        output = result.stdout.strip()
        unpushed_count = -1
        try:
            unpushed_count = int(output)
        except ValueError:
            unpushed_count = 0

        if unpushed_count > 0:
            _log(f"PUSH NEEDED: {unpushed_count} unpushed commit(s)")
            counts = _read_anomaly_counts()
            cnt = _increment_anomaly_count("push:unpushed_commits", counts)
            if cnt >= ANOMALY_ESCALATE_THRESHOLD:
                _log(f"PUSH ANOMALY ESCALATED: unpushed commits detected {cnt}x")
        elif unpushed_count < 0:
            _log(f"PUSH VERIFICATION FAILED: {output[:200]}")
    except subprocess.TimeoutExpired:
        _log("NETWORK STALL: push health check timed out")
    except Exception as e:
        _log(f"push health check error: {e}")


def track_task_duration(task_name: str, duration_seconds: float) -> None:
    p = Path(DURATIONS_FILE)
    data: dict[str, DurationStatsRecord] = {}
    try:
        if p.exists():
            raw = _read_json_record(p)
            for name, value in raw.items():
                raw_entry = _as_record(value)
                if raw_entry is None:
                    continue
                data[name] = {
                    "last_duration": _as_float(raw_entry.get("last_duration")),
                    "avg_duration": _as_float(raw_entry.get("avg_duration")),
                    "count": _as_int(raw_entry.get("count")),
                }
    except Exception:
        pass

    stats = data.get(task_name, {"last_duration": 0.0, "avg_duration": 0.0, "count": 0})
    count = stats["count"] + 1
    avg = stats["avg_duration"]
    new_avg = (avg * (count - 1) + duration_seconds) / count

    data[task_name] = {
        "last_duration": duration_seconds,
        "avg_duration": new_avg,
        "count": count,
    }
    p.write_text(json.dumps(data))

    if avg > 0 and duration_seconds > avg * 3 and count > 2:
        _log(f"ANOMALY: {task_name} took {duration_seconds:.1f}s (avg {avg:.1f}s)")


def _read_last_flag_time() -> float:
    try:
        p = Path(LAST_FLAG_FILE)
        if not p.exists():
            return 0.0
        data = json.loads(p.read_text())
        return float(data.get("last_flag_ts", 0))
    except Exception:
        return 0.0


def _write_last_flag_time(ts: float) -> None:
    Path(LAST_FLAG_FILE).write_text(json.dumps({"last_flag_ts": ts}))


def _read_check_cooldowns() -> dict[str, float]:
    try:
        p = Path(_CHECK_COOLDOWN_FILE)
        if not p.exists():
            return {}
        raw = _read_json_record(p)
        return {name: _as_float(value) for name, value in raw.items()}
    except Exception:
        return {}


def _should_run_check(check_name: str, cooldown_secs: float = _CHECK_COOLDOWN_SECS) -> bool:
    cooldowns = _read_check_cooldowns()
    last_run = float(cooldowns.get(check_name, 0))
    return time.time() - last_run >= cooldown_secs


def _mark_check_run(check_name: str) -> None:
    cooldowns = _read_check_cooldowns()
    cooldowns[check_name] = time.time()
    Path(_CHECK_COOLDOWN_FILE).write_text(json.dumps(cooldowns))


def _parse_etime_to_seconds(etime: str) -> float:
    etime = etime.strip()
    try:
        parts = etime.split("-")
        if len(parts) == 2:
            days = int(parts[0])
            time_parts = parts[1].split(":")
        else:
            days = 0
            time_parts = etime.split(":")
        if len(time_parts) == 3:
            h, m, s = int(time_parts[0]), int(time_parts[1]), int(time_parts[2])
        elif len(time_parts) == 2:
            h, m, s = 0, int(time_parts[0]), int(time_parts[1])
        else:
            return float(etime)
        return days * 86400 + h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        return 0


def _plain_directive_priority(text: str) -> int:
    """Return the highest known operational priority present in a directive."""
    return max(
        (priority for marker, priority in _PLAIN_DIRECTIVE_PRIORITIES.items() if marker in text),
        default=0,
    )


def _write_prioritized_plain_directive(directive: str) -> bool:
    """Write a directive unless a higher-priority signal is already visible."""
    path = Path(PURE_IDLE_DIRECTIVE)
    try:
        existing = path.read_text() if path.exists() else ""
        existing_priority = _plain_directive_priority(existing)
        directive_priority = _plain_directive_priority(directive)
        if existing_priority > directive_priority:
            _log(
                "DIRECTIVE PRESERVED: existing priority "
                f"{existing_priority} exceeds candidate priority {directive_priority}"
            )
            return False
        path.write_text(directive)
        return True
    except Exception as exc:
        _log(f"ERROR writing prioritized directive: {exc}")
        return False


def _check_push_stalled() -> None:
    if not _should_run_check("push_stall"):
        return
    try:
        push_lock = _WORKSPACE / ".git" / "push.lock"
        if push_lock.exists():
            try:
                lock_age = time.time() - push_lock.stat().st_mtime
                if lock_age > 60:
                    _log(f"PUSH STALLED: .git/push.lock exists for {lock_age:.0f}s")
                    directive = f"[{_now()}] PUSH STALLED: push.lock present >60s\n"
                    _write_prioritized_plain_directive(directive)
            except Exception:
                pass

        result = subprocess.run(
            ["ps", "-eo", "pid,etime,command"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            if "git push" in line and "grep" not in line and "ps -eo" not in line:
                parts = line.strip().split(None, 2)
                if len(parts) >= 2:
                    etime = parts[1]
                    elapsed = _parse_etime_to_seconds(etime)
                    if elapsed > 60:
                        _log(f"PUSH STALLED: git push process running {elapsed:.0f}s")
                        directive = f"[{_now()}] PUSH STALLED: git push running >60s\n"
                        _write_prioritized_plain_directive(directive)
    except Exception as e:
        _log(f"push stall check error: {e}")
    finally:
        _mark_check_run("push_stall")


def _check_task_anomaly_300s() -> None:
    if not _should_run_check("task_anomaly"):
        return
    try:
        deadlines = _read_deadlines()
        for d in deadlines:
            elapsed = d.get("elapsed", 0)
            if elapsed > 300:
                task_id = d.get("task_id", "?")
                _log(f"TASK ANOMALY: task {task_id} running >5min ({elapsed:.0f}s)")
                directive = f"[{_now()}] TASK ANOMALY: task {task_id} running >5min\n"
                _write_prioritized_plain_directive(directive)
    except Exception as e:
        _log(f"task anomaly check error: {e}")
    finally:
        _mark_check_run("task_anomaly")


def _check_ci_pending_stall() -> None:
    if not _should_run_check("ci_stall"):
        return
    try:
        head = _get_local_head()
        if not head:
            return
        result = subprocess.run(
            ["gh", "run", "list", f"--commit={head}", "--json", "status,createdAt", "--jq", ".[0]"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if not result.stdout.strip():
            return
        data = json.loads(result.stdout)
        if isinstance(data, dict) and data.get("status") in ("pending", "in_progress", "queued"):
            created = data.get("createdAt")
            if created:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age = (datetime.now(UTC) - created_dt).total_seconds()
                if age > 1800:
                    _log(f"CI STALLED: run on {head[:8]} pending >30min (created {created})")
                    directive = f"[{_now()}] CI STALLED: pending >30min\n"
                    _write_prioritized_plain_directive(directive)
    except Exception as e:
        _log(f"ci stall check error: {e}")
    finally:
        _mark_check_run("ci_stall")


# -- Task anomaly detection --------------------------------------------------


def _read_task_deadlines() -> dict[str, object]:
    return _watchdog_task_health._read_task_deadlines(_WATCHDOG_RUNTIME)


def _find_expected_duration(command: str) -> int | None:
    return _watchdog_task_health._find_expected_duration(_WATCHDOG_RUNTIME, command)


def _load_stalled_tasks() -> set[str]:
    return _watchdog_task_health._load_stalled_tasks(_WATCHDOG_RUNTIME)


def _record_stalled(task_id: str) -> None:
    return _watchdog_task_health._record_stalled(_WATCHDOG_RUNTIME, task_id)


def kill_stalled_task(pid: int) -> None:
    return _watchdog_task_health.kill_stalled_task(_WATCHDOG_RUNTIME, pid)


# -- Task timing anomaly detection --------------------------------------------


def _read_task_timings() -> dict[str, TaskTimingRecord]:
    return _watchdog_task_health._read_task_timings(_WATCHDOG_RUNTIME)


def _write_task_timings(data: dict[str, TaskTimingRecord]) -> None:
    return _watchdog_task_health._write_task_timings(_WATCHDOG_RUNTIME, data)


def _normalize_task_state(value: object) -> TaskStateRecord | None:
    return _watchdog_task_health._normalize_task_state(_WATCHDOG_RUNTIME, value)


def _read_task_state() -> list[TaskStateRecord]:
    return _watchdog_task_health._read_task_state(_WATCHDOG_RUNTIME)


def _write_task_state(data: list[TaskStateRecord]) -> None:
    return _watchdog_task_health._write_task_state(_WATCHDOG_RUNTIME, data)


def _read_previous_state() -> list[TaskStateRecord]:
    return _watchdog_task_health._read_previous_state(_WATCHDOG_RUNTIME)


def _update_timing(task_name: str, duration_secs: float) -> None:
    return _watchdog_task_health._update_timing(_WATCHDOG_RUNTIME, task_name, duration_secs)


def _flag_anomaly(task_name: str, expected_secs: float, actual_secs: float) -> None:
    return _watchdog_task_health._flag_anomaly(_WATCHDOG_RUNTIME, task_name, expected_secs, actual_secs)


def _kill_stalled_task(task_name: str, pid: int | None) -> None:
    return _watchdog_task_health._kill_stalled_task(_WATCHDOG_RUNTIME, task_name, pid)


def check_task_timings() -> None:
    return _watchdog_task_health.check_task_timings(_WATCHDOG_RUNTIME)


# -- check_agent_stalled (existing) ------------------------------------------


def check_agent_stalled(stop_state_path: Path | None = None, false_done_path: Path | None = None) -> bool:
    return _watchdog_task_health.check_agent_stalled(_WATCHDOG_RUNTIME, stop_state_path, false_done_path)


# -- Main check loop ---------------------------------------------------------


def _guess_task_type(task_id: str) -> str:
    return _watchdog_task_health._guess_task_type(_WATCHDOG_RUNTIME, task_id)


def _expected_duration(task_id: str) -> int:
    return _watchdog_task_health._expected_duration(_WATCHDOG_RUNTIME, task_id)


def _read_anomaly_count() -> int:
    return _watchdog_task_health._read_anomaly_count(_WATCHDOG_RUNTIME)


def _write_anomaly_count(count: int) -> None:
    return _watchdog_task_health._write_anomaly_count(_WATCHDOG_RUNTIME, count)


def _increment_anomaly_count(key: str | None = None, counts: dict[str, int] | None = None) -> int:
    return _watchdog_task_health._increment_anomaly_count(_WATCHDOG_RUNTIME, key, counts)


def _gate_pid_elapsed_seconds() -> float | None:
    return _watchdog_task_health._gate_pid_elapsed_seconds(_WATCHDOG_RUNTIME)


def _detect_task_type(task_id: str, tasks_dir: Path | None = None) -> str:
    return _watchdog_task_health._detect_task_type(_WATCHDOG_RUNTIME, task_id, tasks_dir)


EX_TASKS_DIR = os.environ.get("GLUDD_TASKS_DIR", "/tmp/gludd-tasks")
EX_ANOMALIES_FILE = os.environ.get("GLUDD_TASK_ANOMALIES", "/tmp/gludd-task-anomalies.json")
EX_STALLED_TASKS_FILE = os.environ.get("GLUDD_STALLED_TASKS", "/tmp/gludd-stalled-tasks.txt")


def check_task_anomalies() -> AnomalyFindings:
    """Read task deadlines, detect duration anomalies against EXPECTED_DURATIONS.

    Supports two file formats:
      - {task_id: epoch_ms}  (plugin format, command deduced from task_id)
      - {task_id: {start_ts, command, pid}}  (enforce-deadline format)

    Thresholds: >2x expected = ANOMALY, >5x expected = STALLED.
    Returns findings dict for integration by check_and_reset().
    """
    return _watchdog_task_health.check_task_anomalies(_WATCHDOG_RUNTIME)


# -- Timing anomaly detection (/tmp/gludd-watchdog-timing.json) ------------

TIMING_DATA_FILE = "/tmp/gludd-watchdog-timing.json"
PUSH_FLAG = "/tmp/gludd-push-in-progress"

TIMING_ANOMALY_MULTIPLIER = 2.0
STALLED_PUSH_SECS = 60


def _read_timing_data() -> dict[str, OperationTiming]:
    try:
        p = Path(TIMING_DATA_FILE)
        if not p.exists():
            return {}
        raw = _read_json_record(p)
        timing: dict[str, OperationTiming] = {}
        for operation, value in raw.items():
            entry = _as_record(value)
            if entry is None:
                continue
            timing[operation] = {
                "started_at": _as_float(entry.get("started_at")),
                "last_check": _as_float(entry.get("last_check")),
                "duration": _as_float(entry.get("duration")),
                "status": _as_text(entry.get("status")),
            }
        return timing
    except Exception:
        return {}


def _write_timing_data(data: dict[str, OperationTiming]) -> None:
    with suppress(Exception):
        Path(TIMING_DATA_FILE).write_text(json.dumps(data))


def _detect_operations() -> dict[str, float]:
    now = time.time()
    ops: dict[str, float] = {}

    push_flag = Path(PUSH_FLAG)
    if push_flag.exists():
        ops["git-push"] = push_flag.stat().st_mtime

    if GATE_PID_FILE.exists():
        ops["gate-run"] = GATE_PID_FILE.stat().st_mtime

    tasks_dir = Path(EX_TASKS_DIR)
    if tasks_dir.is_dir():
        oldest: float | None = None
        for entry in tasks_dir.iterdir():
            if not entry.is_file() or not entry.name.endswith(".output"):
                continue
            try:
                mtime = entry.stat().st_mtime
                if now - mtime < EXPECTED_DURATIONS["subagent-task"] and (oldest is None or mtime < oldest):
                    oldest = mtime
            except Exception:
                continue
        if oldest is not None:
            ops["subagent-task"] = oldest

    return ops


def _check_timing_anomalies() -> list[str]:
    anomalies: list[str] = []
    now = time.time()

    timing = _read_timing_data()
    detected = _detect_operations()

    for op_type, detected_time in detected.items():
        if op_type not in timing or timing[op_type].get("status") != "running":
            timing[op_type] = {
                "started_at": detected_time,
                "last_check": now,
                "duration": 0,
                "status": "running",
            }
        else:
            timing[op_type]["last_check"] = now
            timing[op_type]["duration"] = now - timing[op_type]["started_at"]

    for op_type, entry in list(timing.items()):
        if entry.get("status") != "running":
            continue
        duration = now - entry["started_at"]
        expected = EXPECTED_DURATIONS.get(op_type, 300)
        if duration > expected * TIMING_ANOMALY_MULTIPLIER:
            entry["status"] = "timed_out"
            entry["duration"] = duration
            msg = f"TIMING ANOMALY: {op_type} expected {expected}s, running for {duration:.0f}s"
            _log(msg)
            anomalies.append(op_type)

    for op_type in list(timing.keys()):
        entry = timing[op_type]
        if entry.get("status") == "running" and op_type not in detected:
            entry["status"] = "completed"
            entry["duration"] = now - entry["started_at"]
            entry["last_check"] = now

    _write_timing_data(timing)
    return anomalies


def _detect_stalled_push() -> str | None:
    push_flag = Path(PUSH_FLAG)
    if not push_flag.exists():
        return None

    try:
        mtime = push_flag.stat().st_mtime
    except Exception:
        return None

    duration = time.time() - mtime
    if duration <= STALLED_PUSH_SECS:
        return None

    unpushed_count = -1
    try:
        result = subprocess.run(
            ["sh", "-c", "git log --oneline @{u}..HEAD 2>&1 | wc -l"],
            capture_output=True,
            text=True,
            timeout=VERIFY_REMOTE_TIMEOUT,
            cwd=str(_WORKSPACE),
        )
        unpushed = result.stdout.strip()
        try:
            unpushed_count = int(unpushed)
        except ValueError:
            unpushed_count = 0
        if unpushed_count == 0:
            return None
    except Exception:
        pass

    msg = (
        f"\u26d4 TIMING ANOMALY: git-push running for {duration:.0f}s "
        f"(expected 30s, {unpushed_count} unpushed). Check for network issues."
    )
    _log(msg)
    return msg


# -- Items 9-13: CI loop detection, health score, unified state, disengage ------


def _compute_health_score(
    tasks_unchecked: bool,
    ratchet_count: int,
    gate_red: bool,
    ci_pending: bool,
    repo_pending: bool,
    agent_active: bool,
) -> int:
    score = 100
    if tasks_unchecked:
        score -= 30
    if ratchet_count > 0:
        score -= 20
    if gate_red:
        score -= 40
    if ci_pending:
        score -= 15
    if repo_pending:
        score -= 10
    if not agent_active:
        score -= 10
    return max(0, score)


def _record_push_timestamp() -> None:
    try:
        p = Path(PUSH_LOOP_FILE)
        data: list[float] = []
        if p.exists():
            data = json.loads(p.read_text())
        data.append(time.time())
        cutoff = time.time() - (CI_LOOP_THRESHOLD_MINUTES + 5) * 60
        data = [ts for ts in data if ts > cutoff]
        if len(data) > 50:
            data = data[-50:]
        p.write_text(json.dumps(data))
    except Exception:
        pass


def _detect_ci_loop() -> bool:
    try:
        p = Path(PUSH_LOOP_FILE)
        if not p.exists():
            return False
        timestamps: list[float] = json.loads(p.read_text())
        cutoff = time.time() - CI_LOOP_THRESHOLD_MINUTES * 60
        recent = [ts for ts in timestamps if ts > cutoff]
        return len(recent) >= CI_LOOP_THRESHOLD_PUSHES
    except Exception:
        return False


def _detect_ci_true_stall() -> bool:
    ci_minutes = _ci_pending_for_too_long_minutes()
    if ci_minutes is None or ci_minutes < CI_TRUE_STALL_MINUTES:
        return False
    try:
        p = Path(PUSH_LOOP_FILE)
        if not p.exists():
            return True
        timestamps: list[float] = json.loads(p.read_text())
        cutoff = time.time() - CI_TRUE_STALL_NO_PUSH_MINUTES * 60
        recent = [ts for ts in timestamps if ts > cutoff]
        return len(recent) == 0
    except Exception:
        return True


def _write_orchestrator_state(
    tasks_unchecked: bool,
    ratchet_count: int,
    gate_red: bool,
    ci_pending: bool,
    repo_pending: bool,
    agent_active: bool,
    ci_run_id: str | None = None,
    stop_detected: bool = False,
) -> None:
    return _watchdog_enforcement._write_orchestrator_state(
        _WATCHDOG_RUNTIME,
        tasks_unchecked,
        ratchet_count,
        gate_red,
        ci_pending,
        repo_pending,
        agent_active,
        ci_run_id,
        stop_detected,
    )


def _write_disengage_signal(minutes: int = 5, reason: str = "") -> None:
    return _watchdog_enforcement._write_disengage_signal(_WATCHDOG_RUNTIME, minutes, reason)


def _clear_disengage_signal() -> None:
    return _watchdog_enforcement._clear_disengage_signal(_WATCHDOG_RUNTIME)


def _check_plugin_hashes() -> None:
    """Run check_plugin_hashes.py --quiet to detect stale plugin code.

    Called every 100 watchdog cycles (~17 min). If plugin .ts files have been
    modified since the last manifest write, the script writes the disengage
    signal — the same effect as `make disengage-enforcement`.
    """
    return _watchdog_enforcement._check_plugin_hashes(_WATCHDOG_RUNTIME)


def _is_disengage_active() -> bool:
    return _watchdog_enforcement._is_disengage_active(_WATCHDOG_RUNTIME)


def _is_push_running() -> bool:
    return _watchdog_enforcement._is_push_running(_WATCHDOG_RUNTIME)


def _auto_reengage_enforcement(mtime_age: float | None) -> None:
    """Auto-re-engage enforcement after push completes when disengage is active.

    Called every poll cycle from check_and_reset(). Re-engages under three rules:
    1. Push completed + agent active + CI green → re-engage immediately.
    2. Disengage >2 min + agent active (<60s mtime) → re-engage regardless of push.
    3. Hard cap: >5 min → re-engage regardless of agent state.
    Also reads block-counter.json directly so a stale disengage file alone does
    not block re-engagement.
    """
    return _watchdog_enforcement._auto_reengage_enforcement(_WATCHDOG_RUNTIME, mtime_age)


def _write_continue_directive(
    work_sources: list[str],
    stop_count: int,
    tasks_unchecked: bool,
    ratchet_count: int,
    gate_red: bool,
    ci_pending: bool,
    ci_run_id: str | None = None,
    work_hint: str = "",
    extra_message: str = "",
) -> None:
    """Write the continue directive to BOTH JSON (for plugins) and plain-text (for visibility)."""
    return _watchdog_enforcement._write_continue_directive(
        _WATCHDOG_RUNTIME,
        work_sources,
        stop_count,
        tasks_unchecked,
        ratchet_count,
        gate_red,
        ci_pending,
        ci_run_id,
        work_hint,
        extra_message,
    )


def _build_continue_directive(
    work_sources: list[str],
    stop_count: int,
    tasks_unchecked: bool,
    ratchet_count: int,
    gate_red: bool,
    ci_pending: bool,
    ci_run_id: str | None = None,
    work_hint: str = "",
    extra_message: str = "",
) -> ContinueDirective:
    return _watchdog_enforcement._build_continue_directive(
        _WATCHDOG_RUNTIME,
        work_sources,
        stop_count,
        tasks_unchecked,
        ratchet_count,
        gate_red,
        ci_pending,
        ci_run_id,
        work_hint,
        extra_message,
    )


LIVENESS_CHECK_COOLDOWN_SECS = 300
LIVENESS_STARTUP_BACKOFF_FILE = "/tmp/gludd-watchdog-liveness-backoff.json"
LIVENESS_STARTUP_BACKOFF_SECS = 60
_last_liveness_check: float = 0.0


def _liveness_startup_in_backoff() -> bool:
    """Check if startup liveness check should be skipped due to recent run.

    File-based backoff persists across watchdog restarts. If liveness was
    checked in the last LIVENESS_STARTUP_BACKOFF_SECS, skip the check to
    prevent a tight crash-restart loop from hammering make check-plugin-liveness.
    """
    return _watchdog_enforcement._liveness_startup_in_backoff(_WATCHDOG_RUNTIME)


def _liveness_write_backoff_ts() -> None:
    return _watchdog_enforcement._liveness_write_backoff_ts(_WATCHDOG_RUNTIME)


def _check_plugin_liveness_on_startup() -> None:
    """Run the plugin liveness check once at startup and log the result.

    Skips the check if it was already run within LIVENESS_STARTUP_BACKOFF_SECS
    (file-based backoff persists across watchdog restarts).
    """
    return _watchdog_enforcement._check_plugin_liveness_on_startup(_WATCHDOG_RUNTIME)


def _check_plugin_liveness_periodic() -> None:
    """Run plugin liveness check every LIVENESS_CHECK_COOLDOWN_SECS."""
    return _watchdog_enforcement._check_plugin_liveness_periodic(_WATCHDOG_RUNTIME)


def _is_force_dispatch_active() -> bool:
    return _watchdog_enforcement._is_force_dispatch_active(_WATCHDOG_RUNTIME)


def _check_force_dispatch() -> bool:
    """Read /tmp/gludd-force-dispatch.json from enforce-stop.ts escalation level 3+.

    Builds specific task dispatch commands for each unchecked TASKS.md item,
    ratchet entry, and red gate.  Writes to CONTINUE_DIRECTIVE with
    action=FORCE_DISPATCH.

    Returns True if force-dispatch is active (lower idle threshold).
    """
    return _watchdog_enforcement._check_force_dispatch(_WATCHDOG_RUNTIME)


def _read_multitask_state() -> dict[str, object]:
    return _watchdog_enforcement._read_multitask_state(_WATCHDOG_RUNTIME)


def _check_under_floor_dispatch() -> None:
    return _watchdog_enforcement._check_under_floor_dispatch(_WATCHDOG_RUNTIME)


SecretsCheck = Callable[[], dict[str, object] | None]


def check_and_reset(*, secrets_check: SecretsCheck | None = None) -> dict[str, object]:
    """Run one watchdog cycle.

    ``secrets_check`` is an explicit dependency seam for deterministic unit
    cycles. Production callers omit it and retain the fail-closed, periodic
    repository-wide scan.
    """
    return _watchdog_enforcement.check_and_reset(_WATCHDOG_RUNTIME, secrets_check=secrets_check)


# -- New detection checks: CI red after tag, release completeness, secrets, stale releases --

RELEASE_CHECK_COOLDOWN_SECS = 600
SECRETS_SCAN_COOLDOWN_SECS = 300
STALE_RELEASE_MINUTES = 30

RELEASE_COMPLETENESS_FILE = "/tmp/gludd-release-completeness.json"
SECRETS_VIOLATION_FILE = "/tmp/gludd-secrets-violation.json"
STALE_RELEASE_FILE = "/tmp/gludd-stale-release.json"


def _get_tags() -> list[str]:
    """Return all annotated/lightweight tags in the repo, newest first."""
    return _watchdog_release._get_tags(_WATCHDOG_RUNTIME)


def _get_tags_with_commits() -> list[tuple[str, str]]:
    """Return list of (tag, commit_hash) for all tags, newest first."""
    return _watchdog_release._get_tags_with_commits(_WATCHDOG_RUNTIME)


def _gh_release_exists(tag: str) -> tuple[bool, ReleaseData]:
    """Check if a GitHub Release exists for the given tag.

    Returns (exists, release_data). release_data contains keys:
      - isDraft, isPrerelease, assetCount, publishedAt
    """
    return _watchdog_release._gh_release_exists(_WATCHDOG_RUNTIME, tag)


def _check_ci_red_after_tag_push() -> dict[str, object] | None:
    """Detect when a tag push exists but CI is red (release blocked).

    If a recent tag has a CI run that is FAILURE, the release pipeline is
    blocked. Returns a findings dict or None.
    """
    return _watchdog_release._check_ci_red_after_tag_push(_WATCHDOG_RUNTIME)


def _check_release_completeness() -> dict[str, object] | None:
    """Verify that the latest tag has a complete GitHub Release with expected artifacts.

    Writes to /tmp/gludd-release-completeness.json for enforce-stop.ts consumption.
    Returns a findings dict or None.
    """
    return _watchdog_release._check_release_completeness(_WATCHDOG_RUNTIME)


def _check_secrets_committed() -> dict[str, object] | None:
    """Periodically scan for secrets committed to tracked files.

    Runs `make secrets-scan` which checks against `.secrets.baseline`.
    Writes findings to /tmp/gludd-secrets-violation.json.
    """
    return _watchdog_release._check_secrets_committed(_WATCHDOG_RUNTIME)


def _check_stale_release() -> dict[str, object] | None:
    """Detect tags that exist but have no GitHub Release after a timeout.

    A tag pushed more than STALE_RELEASE_MINUTES ago that still has no
    release is stale — the CI release pipeline either failed or was never
    triggered. Writes to /tmp/gludd-stale-release.json.
    """
    return _watchdog_release._check_stale_release(_WATCHDOG_RUNTIME)


def _check_load_average() -> None:
    """Check 1-min load average and throttle/submit dispatch accordingly.

    - LOAD_WARN (8): log warning
    - LOAD_THROTTLE (12): write /tmp/gludd-load-throttle → floor=3
    - LOAD_HARD (20): write /tmp/gludd-load-throttle → floor=0
    - Below LOAD_WARN: remove throttle file if present

    Also monitors child-process count of the parent (opencode) process.
    """
    try:
        load: float = os.getloadavg()[0]
    except OSError:
        return

    now_epoch: float = time.time()

    if load >= LOAD_HARD:
        _log(f"LOAD HARD: {load:.1f} >= {LOAD_HARD} — stopping all dispatches")
        Path(LOAD_THROTTLE_FILE).write_text(json.dumps({"floor": 0, "load": round(load, 2), "ts": now_epoch}))
    elif load >= LOAD_THROTTLE:
        _log(f"LOAD THROTTLE: {load:.1f} >= {LOAD_THROTTLE} — reducing dispatch floor to 3")
        Path(LOAD_THROTTLE_FILE).write_text(json.dumps({"floor": 3, "load": round(load, 2), "ts": now_epoch}))
    elif load >= LOAD_WARN:
        _log(f"LOAD WARN: {load:.1f} >= {LOAD_WARN}")
        if Path(LOAD_THROTTLE_FILE).exists():
            Path(LOAD_THROTTLE_FILE).unlink()
    else:
        if Path(LOAD_THROTTLE_FILE).exists():
            Path(LOAD_THROTTLE_FILE).unlink()

    # Process count: count direct children of the opencode parent process
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(os.getppid())],
            capture_output=True,
            text=True,
            timeout=5,
        )
        child_pids = [p for p in result.stdout.strip().splitlines() if p]
        child_count = len(child_pids)
        if child_count > MAX_CHILD_PROCESSES:
            _log(f"CHILD PROCESS WARN: {child_count} children of opencode > {MAX_CHILD_PROCESSES}")
    except Exception:
        pass


# -- CLI ----------------------------------------------------------------------


def _cli_classification(argv: list[str]) -> int:
    """Handle --once, --count-stalled, --list-stalled, --all flags."""
    return _watchdog_cli._cli_classification(_WATCHDOG_RUNTIME, argv)


def main(argv: list[str] | None = None) -> int:
    return _watchdog_cli.main(_WATCHDOG_RUNTIME, argv)


if __name__ == "__main__":
    raise SystemExit(main())
