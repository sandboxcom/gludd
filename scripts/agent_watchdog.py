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

import enum
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from statistics import mean, stdev
except ImportError:
    def _simple_mean(values):
        if not values:
            return 0.0
        return sum(values) / len(values)

    def _simple_stdev(values):
        if len(values) < 2:
            return 0.0
        m = _simple_mean(values)
        return (sum((x - m) ** 2 for x in values) / (len(values) - 1)) ** 0.5

    mean = _simple_mean
    stdev = _simple_stdev

# -- Classification API -------------------------------------------------------

DEFAULT_WINDOW_SECS: float = 90.0

DONE_MARKERS = ("result:", "summary:", "complete", "finished", "passed", "failed:")
STALL_MARKERS = ("continuing", "let me", "next",)


class State(enum.Enum):
    ACTIVE = "ACTIVE"
    LIKELY_STALLED_INCOMPLETE = "LIKELY_STALLED_INCOMPLETE"
    DONE = "DONE"


def classify_tail(
    tail: str,
    age_seconds: float,
    window_seconds: float = DEFAULT_WINDOW_SECS,
) -> tuple[State, str]:
    if age_seconds < window_seconds:
        return State.ACTIVE, f"age {age_seconds:.1f}s < window {window_seconds}s"

    tail_lower = tail.lower()

    for marker in DONE_MARKERS:
        if marker in tail_lower:
            return State.DONE, f"result: found '{marker}' in tail"

    stripped = tail.strip()
    if not stripped:
        return State.LIKELY_STALLED_INCOMPLETE, "empty tail"
    if stripped.isspace():
        return State.LIKELY_STALLED_INCOMPLETE, "whitespace-only tail"

    for line in tail_lower.splitlines():
        for marker in STALL_MARKERS:
            if marker in line.strip():
                return State.LIKELY_STALLED_INCOMPLETE, f"let me / continuing: '{marker}' in tail"

    last_line = stripped.splitlines()[-1].rstrip()
    if last_line.endswith(":"):
        return State.LIKELY_STALLED_INCOMPLETE, "last line ends with ':'"

    return State.LIKELY_STALLED_INCOMPLETE, "no completion marker"


def scan_tasks_dir(
    tasks_dir: Path,
    window_seconds: float = DEFAULT_WINDOW_SECS,
) -> list[tuple[str, State, str]]:
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


# -- Streak-reset watchdog ----------------------------------------------------

STREAK_FILE = "/tmp/gludd-mainthread-streak.json"
WATCHDOG_ACTIVITY_FILE = "/tmp/gludd-watchdog-last-activity.json"
TODOWRITE_STATE = os.environ.get("GLUDD_TODOWRITE_STATE", "/tmp/gludd-todowrite-state.json")
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


def _prune_alerted_anomalies(now_epoch: float | None = None) -> None:
    """Remove entries older than _ALERTED_PRUNE_SECS from _alerted_anomalies."""
    global _alerted_anomalies
    if now_epoch is None:
        now_epoch = time.time()
    cutoff = now_epoch - _ALERTED_PRUNE_SECS
    _alerted_anomalies = {
        k: v for k, v in _alerted_anomalies.items() if v > cutoff
    }


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

_WORKSPACE = Path(os.environ.get("GLUDD_WORKSPACE", os.getcwd()))
GATE_PID_FILE = _WORKSPACE / ".gate-background.pid"
GATE_MAX_RUNTIME_SECS = int(os.environ.get("GATE_WATCHDOG_TIMEOUT", "3600"))
_TASKS_MD = _WORKSPACE / "TASKS.md"
_RATCHET_YML = _WORKSPACE / "config" / "ratchet.yml"
_GATE_STATUS = _WORKSPACE / ".gate-status"

_UNCHECKED_PATTERN = re.compile(r"-\s+\[\s*\]|\*\s+\[\s*\]", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        if mtime_age is not None and mtime_age < PURE_IDLE_SECS:
            count = 0
        else:
            count = (count % 100) + 1
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
        return [
            t.get("content", "?")
            for t in todos
            if t.get("status") in ("pending", "in_progress")
        ]
    except Exception:
        return []


# -- Task duration anomaly detection ------------------------------------------

DEFAULT_STALL_MINUTES = 5.0
DEFAULT_ANOMALY_MULTIPLIER = 5.0
ANOMALY_DIRECTIVE = "/tmp/gludd-continue.txt"


def _read_deadlines() -> list[dict]:
    try:
        p = Path(TASK_DEADLINES_FILE)
        if not p.exists():
            return []
        data = json.loads(p.read_text())
        entries = data.get("tasks", data) if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return []
        now = time.time()
        result: list[dict] = []
        for entry in entries:
            task_id = entry.get("task_id", entry.get("id", "?"))
            start_ts = float(entry.get("dispatched_at", entry.get("start_ts", entry.get("start", 0))))
            if start_ts <= 0:
                continue
            elapsed = now - start_ts
            result.append({
                "id": task_id,
                "task_id": task_id,
                "type": entry.get("type", _guess_task_type(str(task_id))),
                "description": entry.get("description", str(task_id)),
                "dispatched_at": start_ts,
                "start_ts": start_ts,
                "elapsed": elapsed,
            })
        return result
    except Exception:
        return []


def _detect_stalled_tasks(
    deadlines: list[dict],
    max_minutes: float = DEFAULT_STALL_MINUTES,
) -> list[dict]:
    max_seconds = max_minutes * 60.0
    stalled: list[dict] = []
    for d in deadlines:
        if d["elapsed"] > max_seconds:
            stalled.append({
                "task_id": d["task_id"],
                "elapsed_seconds": round(d["elapsed"], 1),
                "elapsed_minutes": round(d["elapsed"] / 60.0, 1),
            })
    return stalled


def _detect_anomalies(
    deadlines: list[dict],
    multiplier: float = DEFAULT_ANOMALY_MULTIPLIER,
) -> list[dict]:
    if len(deadlines) < 3:
        return []
    elapsed_values = sorted(d["elapsed"] for d in deadlines if d["elapsed"] > 0)
    if not elapsed_values:
        return []
    n = len(elapsed_values)
    if n % 2 == 0:
        median = (elapsed_values[n // 2 - 1] + elapsed_values[n // 2]) / 2.0
    else:
        median = elapsed_values[n // 2]
    if median <= 0:
        return []
    anomalies: list[dict] = []
    for d in deadlines:
        if d["elapsed"] > median * multiplier:
            anomalies.append({
                "task_id": d["task_id"],
                "elapsed_seconds": round(d["elapsed"], 1),
                "elapsed_minutes": round(d["elapsed"] / 60.0, 1),
                "median_seconds": round(median, 1),
                "ratio": round(d["elapsed"] / median, 2) if median > 0 else 0,
            })
    return anomalies


def _read_task_history() -> dict:
    try:
        p = Path(TASK_HISTORY_FILE)
        if not p.exists():
            return {"durations": {}, "last_seen": {}}
        return json.loads(p.read_text())
    except Exception:
        return {"durations": {}, "last_seen": {}}


def _write_task_history(history: dict) -> None:
    try:
        Path(TASK_HISTORY_FILE).write_text(json.dumps(history, indent=2))
    except Exception:
        pass


def _rolling_avg_by_type(history: dict, task_type: str) -> float | None:
    durations = history.get("durations", {}).get(task_type, [])
    if not durations:
        return None
    recent = durations[-MAX_HISTORY_PER_TYPE:]
    return sum(recent) / len(recent)


def _update_task_history(deadlines: list[dict]) -> None:
    history = _read_task_history()
    now = time.time()
    current_ids = {d.get("id", d.get("task_id", "")) for d in deadlines}

    last_seen = history.get("last_seen", {})
    durations_by_type: dict[str, list[float]] = history.get("durations", {})

    for task_id, seen_info in list(last_seen.items()):
        if task_id not in current_ids:
            dispatched_at = seen_info.get("dispatched_at", 0)
            task_type = seen_info.get("type", "unknown")
            if dispatched_at > 0:
                duration = now - dispatched_at
                if task_type not in durations_by_type:
                    durations_by_type[task_type] = []
                durations_by_type[task_type].append(duration)
                if len(durations_by_type[task_type]) > MAX_HISTORY_PER_TYPE:
                    durations_by_type[task_type] = durations_by_type[task_type][-MAX_HISTORY_PER_TYPE:]
            del last_seen[task_id]

    for d in deadlines:
        task_id = d.get("id", d.get("task_id", ""))
        if task_id and task_id not in last_seen:
            last_seen[task_id] = {
                "dispatched_at": d.get("dispatched_at", d.get("start_ts", 0)),
                "type": d.get("type", "unknown"),
                "seen_at": now,
            }

    history["durations"] = durations_by_type
    history["last_seen"] = last_seen
    _write_task_history(history)


def _detect_history_anomalies(
    deadlines: list[dict],
    timeout_secs: float = 300.0,
    multiplier: float = 3.0,
) -> list[dict]:
    history = _read_task_history()
    anomalies: list[dict] = []

    for d in deadlines:
        elapsed = d.get("elapsed", 0)
        task_id = d.get("id", d.get("task_id", "?"))
        task_type = d.get("type", "unknown")
        description = d.get("description", task_id)
        dispatched_at = d.get("dispatched_at", d.get("start_ts", 0))

        if elapsed <= 0:
            continue

        hard_timeout = elapsed > timeout_secs
        rolling_avg = _rolling_avg_by_type(history, task_type)
        history_anomaly = rolling_avg is not None and elapsed > rolling_avg * multiplier

        if hard_timeout or history_anomaly:
            entry = {
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
            capture_output=True, text=True, timeout=CI_VERDICT_TIMEOUT,
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
        data = json.loads(p.read_text())
        last_check = data.get("last_ci_check", 0)
        if last_check > 0 and (time.time() - last_check) > 300:
            return 0.0
        first_seen = data.get("pending_first_seen", 0)
        if first_seen <= 0:
            return None
        return (time.time() - first_seen) / 60.0
    except Exception:
        return None


def _write_watchdog_activity(ts: float | None = None) -> None:
    ts_value = ts if ts is not None else time.time()
    Path(WATCHDOG_ACTIVITY_FILE).write_text(
        json.dumps({"last_activity_ts": ts_value})
    )


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


def _increment_anomaly_count(key: str, counts: dict[str, int] | None = None) -> int:
    if counts is None:
        counts = _read_anomaly_counts()
    new_val = counts.get(key, 0) + 1
    counts[key] = new_val
    Path(ANOMALY_COUNT_FILE).write_text(json.dumps(counts))
    return new_val


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
            capture_output=True, text=True, timeout=5, cwd=str(_WORKSPACE),
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
            data = json.loads(p.read_text())
            last_check = float(data.get("last_ci_check", 0))
            if time.time() - last_check < CI_CHECK_INTERVAL:
                return False
    except Exception:
        pass
    return True


def _update_ci_cache(**kwargs) -> None:  # type: ignore[no-untyped-def]
    p = Path(CI_CACHE_FILE)
    existing: dict = {}
    try:
        if p.exists():
            existing = json.loads(p.read_text())
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
            capture_output=True, text=True, timeout=CI_VERDICT_TIMEOUT,
            cwd=str(_WORKSPACE),
        )
        output = result.stdout + result.stderr
        _update_ci_cache(last_output=output[:200])

        status_match = re.search(r"conclusion:\s*(\S+)", output)
        run_id_match = re.search(r"run[_\s]?id[:=]?\s*(\d+)", output, re.IGNORECASE)

        status = status_match.group(1) if status_match else "UNKNOWN"
        run_id = run_id_match.group(1) if run_id_match else None

        ci_data: dict = {}
        try:
            p = Path(CI_CACHE_FILE)
            if p.exists():
                ci_data = json.loads(p.read_text())
        except Exception:
            pass

        last_run_id = ci_data.get("last_ci_run_id")
        first_seen = ci_data.get("pending_first_seen", 0)

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
                f"GATE STALLED: background gate pid={pid_str} running "
                f"{elapsed:.0f}s (>1h) - auto-killing"
            )
            try:
                PATH_PARTS = Path(".gate-status").absolute()
                PATH_PARTS.write_text("GATE_TIMEOUT\n=== GATE: ABORTED (watchdog timeout) ===\n")
            except Exception:
                pass
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(10)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
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
            capture_output=True, text=True, timeout=VERIFY_REMOTE_TIMEOUT,
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
    data: dict = {}
    try:
        if p.exists():
            data = json.loads(p.read_text())
    except Exception:
        pass

    entry = data.get(task_name, {"last_duration": 0, "avg_duration": 0, "count": 0})
    count = entry.get("count", 0) + 1
    avg = entry.get("avg_duration", 0.0)
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


def _read_check_cooldowns() -> dict:
    try:
        p = Path(_CHECK_COOLDOWN_FILE)
        if not p.exists():
            return {}
        return json.loads(p.read_text())
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
                    Path(PURE_IDLE_DIRECTIVE).write_text(directive)
            except Exception:
                pass

        result = subprocess.run(
            ["ps", "-eo", "pid,etime,command"],
            capture_output=True, text=True, timeout=10,
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
                        Path(PURE_IDLE_DIRECTIVE).write_text(directive)
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
                Path(PURE_IDLE_DIRECTIVE).write_text(directive)
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
            capture_output=True, text=True, timeout=15,
        )
        if not result.stdout.strip():
            return
        data = json.loads(result.stdout)
        if isinstance(data, dict) and data.get("status") in ("pending", "in_progress", "queued"):
            created = data.get("createdAt")
            if created:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - created_dt).total_seconds()
                if age > 1800:
                    _log(f"CI STALLED: run on {head[:8]} pending >30min (created {created})")
                    directive = f"[{_now()}] CI STALLED: pending >30min\n"
                    Path(PURE_IDLE_DIRECTIVE).write_text(directive)
    except Exception as e:
        _log(f"ci stall check error: {e}")
    finally:
        _mark_check_run("ci_stall")


# -- Task anomaly detection --------------------------------------------------


def _read_task_deadlines() -> dict:
    try:
        return json.loads(Path(TASK_DEADLINES_FILE).read_text())
    except Exception:
        return {}


def _find_expected_duration(command: str) -> int | None:
    cmd_lower = command.lower()
    if "git-push" in cmd_lower:
        return EXPECTED_DURATIONS["git-push"]
    if "git-status" in cmd_lower:
        return EXPECTED_DURATIONS["git-status"]
    if "ci-verdict" in cmd_lower:
        return EXPECTED_DURATIONS["ci-verdict"]
    if "lint" in cmd_lower:
        return EXPECTED_DURATIONS["lint"]
    if "typecheck" in cmd_lower:
        return EXPECTED_DURATIONS["typecheck"]
    if "collect-check" in cmd_lower:
        return EXPECTED_DURATIONS["collect-check"]
    if "test-unit" in cmd_lower:
        return EXPECTED_DURATIONS["test-unit"]
    if "gate" in cmd_lower:
        return EXPECTED_DURATIONS["gate"]
    if "test" in cmd_lower:
        return EXPECTED_DURATIONS["test-specific"]
    return None


def _load_stalled_tasks() -> set:
    try:
        p = Path(STALLED_TASKS_FILE)
        if not p.exists():
            return set()
        return set(p.read_text(encoding="utf-8").splitlines())
    except Exception:
        return set()


def _record_stalled(task_id: str) -> None:
    already = _load_stalled_tasks()
    already.add(task_id)
    Path(STALLED_TASKS_FILE).write_text("\n".join(sorted(already)) + "\n")


def kill_stalled_task(pid: int) -> None:
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
        _log(f"TASK KILL: sent SIGTERM to pid={pid}")
        time.sleep(5)
        os.kill(pid, signal.SIGKILL)
        _log(f"TASK KILL: sent SIGKILL to pid={pid}")
    except ProcessLookupError:
        _log(f"TASK KILL: pid={pid} already gone")
    except Exception as exc:
        _log(f"TASK KILL: error killing pid={pid}: {exc}")


# -- Task timing anomaly detection --------------------------------------------


def _read_task_timings() -> dict:
    try:
        p = Path(TASK_TIMING_FILE)
        if not p.exists():
            return {}
        return json.loads(p.read_text())
    except Exception:
        return {}


def _write_task_timings(data: dict) -> None:
    Path(TASK_TIMING_FILE).write_text(json.dumps(data))


def _read_task_state() -> list:
    try:
        p = Path(TASK_STATE_FILE)
        if not p.exists():
            return []
        data = json.loads(p.read_text())
        if isinstance(data, dict) and "name" in data and "started" in data and "ended" not in data:
            return [data]
        return []
    except Exception:
        return []


def _write_task_state(data: list) -> None:
    Path(TASK_STATE_SNAPSHOT).write_text(json.dumps(data))


def _read_previous_state() -> list:
    try:
        p = Path(TASK_STATE_SNAPSHOT)
        if not p.exists():
            return []
        data = json.loads(p.read_text())
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _update_timing(task_name: str, duration_secs: float) -> None:
    timings = _read_task_timings()
    if task_name in timings:
        entry = timings[task_name]
        old_avg = entry.get("average_duration_seconds", duration_secs)
        old_count = entry.get("count", 0)
        new_count = old_count + 1
        new_avg = (old_avg * old_count + duration_secs) / new_count
        entry["average_duration_seconds"] = new_avg
        entry["count"] = new_count
    else:
        timings[task_name] = {"average_duration_seconds": duration_secs, "count": 1}
    _write_task_timings(timings)


def _flag_anomaly(task_name: str, expected_secs: float, actual_secs: float) -> None:
    ratio = actual_secs / expected_secs if expected_secs > 0 else 0
    _log(f"TASK TIMING ANOMALY: {task_name} took {actual_secs:.0f}s (expected ~{expected_secs:.0f}s, {ratio:.1f}x)")
    directive_p = Path("/tmp/gludd-continue.txt")
    existing = ""
    if directive_p.exists():
        try:
            existing = directive_p.read_text()
        except Exception:
            pass
    directive_p.write_text((existing + f"[{_now()}] TIMING ANOMALY: {task_name} took {actual_secs:.0f}s vs expected {expected_secs:.0f}s\n").strip() + "\n")


def _kill_stalled_task(task_name: str, pid: int | None) -> None:
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
            _log(f"KILLED STALLED TASK: {task_name} pid={pid} (SIGTERM)")
            time.sleep(2)
            os.kill(pid, signal.SIGKILL)
            _log(f"KILLED STALLED TASK: {task_name} pid={pid} (SIGKILL)")
        except ProcessLookupError:
            _log(f"STALLED TASK: {task_name} pid={pid} already exited")
        except Exception as exc:
            _log(f"STALLED TASK: {task_name} pid={pid} kill error: {exc}")
    else:
        _log(f"STALLED TASK KILLED: {task_name} (no pid available for kill)")


def check_task_timings() -> None:
    running = _read_task_state()
    previous = _read_previous_state()
    now = time.time()

    prev_names = {t.get("name") for t in previous}
    curr_names = {t.get("name") for t in running}
    completed_names = prev_names - curr_names
    for task in previous:
        name = task.get("name")
        if name in completed_names:
            started = task.get("started", 0)
            if started > 0:
                duration = now - started
                _update_timing(name, duration)

    for task in running:
        name = task.get("name", "unknown")
        started = task.get("started", 0)
        pid = task.get("pid")
        if started <= 0:
            continue
        elapsed = now - started

        if elapsed > TASK_STALL_TIMEOUT:
            _kill_stalled_task(name, pid)
            _log(f"STALLED TASK KILLED: {name} running {elapsed:.0f}s")

        timings = _read_task_timings()
        if name in timings:
            avg = timings[name].get("average_duration_seconds", 0)
            if avg > 0 and elapsed > avg * ANOMALY_MULTIPLIER:
                _flag_anomaly(name, avg, elapsed)

    _write_task_state(running)


# -- check_agent_stalled (existing) ------------------------------------------


def check_agent_stalled(
    stop_state_path: Path | None = None,
    false_done_path: Path | None = None,
) -> bool:
    sp = stop_state_path or Path(STOP_STATE)
    fp = false_done_path or Path(FALSE_DONE_BLOCKS)

    try:
        if sp.exists():
            data = json.loads(sp.read_text())
            if data.get("hasPendingWork"):
                return True
    except Exception:
        pass

    try:
        if fp.exists():
            data = json.loads(fp.read_text())
            if int(data.get("consecutive", 0)) > 0:
                return True
    except Exception:
        pass

    return False


# -- Main check loop ---------------------------------------------------------


def _guess_task_type(task_id: str) -> str:
    tid = task_id.lower()
    if "push" in tid:
        return "git-push"
    if "test" in tid:
        return "test"
    if "commit" in tid:
        return "commit"
    if "gate" in tid:
        return "gate"
    return "general"


def _expected_duration(task_id: str) -> int:
    return EXPECTED_DURATIONS.get(_guess_task_type(task_id), 300)


def _read_anomaly_count() -> int:
    try:
        p = Path(ANOMALY_COUNT_FILE)
        if not p.exists():
            return 0
        data = json.loads(p.read_text())
        return int(data.get("count", 0))
    except Exception:
        return 0


def _write_anomaly_count(count: int) -> None:
    Path(ANOMALY_COUNT_FILE).write_text(json.dumps({"count": count}))


def _increment_anomaly_count(key: str | None = None,
                           counts: dict[str, int] | None = None) -> int:
    if key is not None:
        if counts is None:
            counts = _read_anomaly_counts()
        new_val = counts.get(key, 0) + 1
        counts[key] = new_val
        Path(ANOMALY_COUNT_FILE).write_text(json.dumps(counts))
        return new_val
    new_count = _read_anomaly_count() + 1
    _write_anomaly_count(new_count)
    return new_count


def _gate_pid_elapsed_seconds() -> float | None:
    try:
        if not GATE_PID_FILE.exists():
            return None
        mtime = GATE_PID_FILE.stat().st_mtime
        return time.time() - mtime
    except Exception:
        return None


def _detect_task_type(task_id: str, tasks_dir: Path | None = None) -> str:
    tid = task_id.lower()
    if any(kw in tid for kw in ("gate", "marshal", "build")):
        return "gate"
    if any(kw in tid for kw in ("test", "pytest", "collect-check")):
        return "test"
    if any(kw in tid for kw in ("research", "read", "audit", "review", "explore", "find", "scan")):
        return "research"
    if any(kw in tid for kw in ("push", "ship")):
        return "push"
    return "default"


EX_TASKS_DIR = os.environ.get("GLUDD_TASKS_DIR", "/tmp/gludd-tasks")
EX_ANOMALIES_FILE = os.environ.get("GLUDD_TASK_ANOMALIES", "/tmp/gludd-task-anomalies.json")
EX_STALLED_TASKS_FILE = os.environ.get("GLUDD_STALLED_TASKS", "/tmp/gludd-stalled-tasks.txt")


def check_task_anomalies() -> dict:
    """Read task deadlines, detect duration anomalies against EXPECTED_DURATIONS.

    Supports two file formats:
      - {task_id: epoch_ms}  (plugin format, command deduced from task_id)
      - {task_id: {start_ts, command, pid}}  (enforce-deadline format)

    Thresholds: >2x expected = ANOMALY, >5x expected = STALLED.
    Returns findings dict for integration by check_and_reset().
    """
    global _alerted_anomalies
    findings: dict = {"tasks": [], "anomalies": [], "stalled": [], "ts": _now()}

    dl_path = Path(TASK_DEADLINES_FILE)
    if dl_path.exists():
        try:
            raw = json.loads(dl_path.read_text())
            if isinstance(raw, dict):
                now_epoch = time.time()
                stalled_set = _load_stalled_tasks()

                for task_id, value in raw.items():
                    if isinstance(value, (int, float)):
                        start_ts = value / 1000.0 if value > 1e11 else value
                        command = ""
                    elif isinstance(value, dict):
                        start_ts = value.get("start_ts", 0)
                        if not start_ts:
                            continue
                        command = value.get("command", "")
                    else:
                        continue

                    elapsed = now_epoch - start_ts

                    if command:
                        expected = _find_expected_duration(command)
                    else:
                        task_type = _detect_task_type(task_id)
                        expected = EXPECTED_DURATIONS.get(task_type, EXPECTED_DURATIONS["default"])

                    if expected is None:
                        continue

                    entry: dict = {
                        "task_id": task_id,
                        "elapsed_s": round(elapsed, 1),
                        "expected_s": expected,
                    }
                    findings["tasks"].append(entry)

                    if elapsed > expected * 5:
                        findings["stalled"].append(entry)
                        if task_id not in stalled_set:
                            _log(f"TASK STALLED: {task_id} ({command}) running {elapsed:.0f}s (expected {expected}s)")
                            _record_stalled(task_id)
                    elif elapsed > expected * 2:
                        findings["anomalies"].append(entry)
                        if task_id not in _alerted_anomalies:
                            _log(f"TASK ANOMALY: {task_id} ({command}) running {elapsed:.0f}s (expected {expected}s)")
                            _alerted_anomalies[task_id] = now_epoch

                try:
                    Path(EX_ANOMALIES_FILE).write_text(json.dumps(findings, indent=2))
                except Exception:
                    pass
        except Exception:
            pass

    # Check gate background process
    try:
        gp = GATE_PID_FILE
        if gp.exists():
            gate_elapsed = time.time() - gp.stat().st_mtime
            if gate_elapsed > 45 * 60:
                _log(f"GATE STALLED: background gate running {gate_elapsed:.0f}s (>45min)")
                findings.setdefault("stalled", []).append({
                    "task_id": "gate-process",
                    "elapsed_s": round(gate_elapsed, 1),
                    "expected_s": 2700,
                    "type": "gate",
                })
    except Exception:
        pass

    # Detect push stalled
    for t in findings.get("tasks", []):
        if "push" in t.get("task_id", "").lower() and t.get("elapsed_s", 0) > 60:
            _log(f"PUSH STALLED — possible network issue: {t['task_id']} elapsed={t['elapsed_s']:.0f}s")

    for a in findings.get("anomalies", []):
        task_id = a.get("task_id", "unknown")
        cnt = _increment_anomaly_count(f"anomaly:{task_id}")
        if cnt >= ANOMALY_ESCALATE_THRESHOLD:
            _log(f"ANOMALY ESCALATION: {task_id} anomaly {cnt}x (threshold={ANOMALY_ESCALATE_THRESHOLD})")
            findings["escalated"] = True
    for s in findings.get("stalled", []):
        task_id = s.get("task_id", "unknown")
        cnt = _increment_anomaly_count(f"stalled:{task_id}")
        if cnt >= ANOMALY_ESCALATE_THRESHOLD:
            _log(f"ANOMALY ESCALATION: {task_id} stalled {cnt}x (threshold={ANOMALY_ESCALATE_THRESHOLD})")
            findings["escalated"] = True

    return findings


# -- Timing anomaly detection (/tmp/gludd-watchdog-timing.json) ------------

TIMING_DATA_FILE = "/tmp/gludd-watchdog-timing.json"
PUSH_FLAG = "/tmp/gludd-push-in-progress"

TIMING_ANOMALY_MULTIPLIER = 2.0
STALLED_PUSH_SECS = 60


def _read_timing_data() -> dict:
    try:
        p = Path(TIMING_DATA_FILE)
        if not p.exists():
            return {}
        return json.loads(p.read_text())
    except Exception:
        return {}


def _write_timing_data(data: dict) -> None:
    try:
        Path(TIMING_DATA_FILE).write_text(json.dumps(data))
    except Exception:
        pass


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
                if now - mtime < EXPECTED_DURATIONS["subagent-task"]:
                    if oldest is None or mtime < oldest:
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
            capture_output=True, text=True, timeout=VERIFY_REMOTE_TIMEOUT,
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
    tasks_unchecked: bool, ratchet_count: int, gate_red: bool,
    ci_pending: bool, repo_pending: bool, agent_active: bool,
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
    tasks_unchecked: bool, ratchet_count: int, gate_red: bool,
    ci_pending: bool, repo_pending: bool, agent_active: bool,
    ci_run_id: str | None = None, stop_detected: bool = False,
) -> None:
    try:
        health = _compute_health_score(
            tasks_unchecked, ratchet_count, gate_red,
            ci_pending, repo_pending, agent_active,
        )
        ci_loop = _detect_ci_loop()
        ci_stall = _detect_ci_true_stall()
        state = {
            "ts": _now(),
            "epoch": time.time(),
            "health_score": health,
            "tasks_md_unchecked": tasks_unchecked,
            "ratchet_entries": ratchet_count,
            "gate_status_red": gate_red,
            "ci_pending_or_red": ci_pending,
            "ci_run_id": ci_run_id,
            "repo_pending": repo_pending,
            "agent_active": agent_active,
            "ci_loop_detected": ci_loop,
            "ci_true_stall": ci_stall,
            "stop_detected": stop_detected,
        }
        Path(ORCHESTRATOR_STATE_FILE).write_text(json.dumps(state, indent=2))
        Path(HEALTH_SCORE_FILE).write_text(json.dumps({"score": health, "ts": _now()}))
    except Exception:
        pass


def _write_disengage_signal(minutes: int = 5, reason: str = "") -> None:
    try:
        disengage_until = int(time.time() * 1000 + minutes * 60 * 1000)
        Path(DISENGAGE_FILE).write_text(json.dumps({
            "disengage_until": disengage_until,
            "disengage_until_epoch_ms": disengage_until,
            "reason": reason,
            "ts": _now(),
        }))
        _log(f"DISENGAGE: sent signal for {minutes}min — {reason}")
    except Exception:
        pass


def _clear_disengage_signal() -> None:
    try:
        p = Path(DISENGAGE_FILE)
        if p.exists():
            data = json.loads(p.read_text())
            if data.get("disengage_until", 0) < time.time() * 1000:
                p.unlink()
    except Exception:
        pass


def _check_plugin_hashes() -> None:
    """Run check_plugin_hashes.py --quiet to detect stale plugin code.

    Called every 100 watchdog cycles (~17 min). If plugin .ts files have been
    modified since the last manifest write, the script writes the disengage
    signal — the same effect as `make disengage-enforcement`.
    """
    try:
        manifest = _WORKSPACE / ".opencode" / "plugin-hashes.json"
        plugin_dir = _WORKSPACE / ".opencode" / "plugin"
        current = {}
        if plugin_dir.is_dir():
            for f in sorted(plugin_dir.glob("*.ts")):
                try:
                    current[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
                except Exception:
                    pass

        stored = {}
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    stored = {k: v for k, v in data.items() if isinstance(v, str)}
            except Exception:
                pass

        if not current:
            return

        if not stored:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return

        if current == stored:
            return

        changed = [f for f in set(current) & set(stored) if current[f] != stored[f]]
        new_f = sorted(set(current) - set(stored))
        removed = sorted(set(stored) - set(current))
        details = []
        if new_f:
            details.append(f"new: {', '.join(new_f)}")
        if removed:
            details.append(f"removed: {', '.join(removed)}")
        if changed:
            details.append(f"changed: {', '.join(changed)}")

        reason = " | ".join(details) if details else "plugin hashes changed"
        _write_disengage_signal(minutes=60, reason=f"plugin_version_mismatch: {reason}")

        try:
            Path(BLOCK_COUNTER_FILE).write_text(json.dumps({
                "consecutiveBlocks": 0,
                "totalBlocks": 0,
                "lastBlockTs": 0,
                "disengageUntil": 9999999999999,
            }))
        except Exception:
            pass

        _log(f"PLUGIN VERSION CHANGED: {reason} — disengage signal written")
    except Exception:
        pass


def _is_disengage_active() -> bool:
    try:
        p = Path(DISENGAGE_FILE)
        if not p.exists():
            return False
        data = json.loads(p.read_text())
        return data.get("disengage_until", 0) > time.time() * 1000
    except Exception:
        return False


def _is_push_running() -> bool:
    push_lock = _WORKSPACE / ".git" / "push.lock"
    if push_lock.exists():
        return True
    try:
        result = subprocess.run(
            ["ps", "-eo", "command"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "git push" in line and "grep" not in line and "ps -eo" not in line:
                return True
    except Exception:
        pass
    return False


def _auto_reengage_enforcement(mtime_age: float | None) -> None:
    """Auto-re-engage enforcement after push completes when disengage is active.

    Called every poll cycle from check_and_reset(). Re-engages under three rules:
    1. Push completed + agent active + CI green → re-engage immediately.
    2. Disengage >2 min + agent active (<60s mtime) → re-engage regardless of push.
    3. Hard cap: >5 min → re-engage regardless of agent state.
    Also reads block-counter.json directly so a stale disengage file alone does
    not block re-engagement.
    """
    # ── Read block-counter.json for disengageUntil ──
    block_disengage_active = False
    block_file_age_s = 0
    try:
        bp = Path(BLOCK_COUNTER_FILE)
        if bp.exists():
            block_data = json.loads(bp.read_text())
            du = block_data.get("disengageUntil", 0)
            if du > (time.time() * 1000):
                block_disengage_active = True
            block_file_age_s = time.time() - bp.stat().st_mtime
    except Exception:
        pass

    if not _is_disengage_active() and not block_disengage_active:
        return

    now_ms = int(time.time() * 1000)
    p = Path(DISENGAGE_FILE)
    disengage_until = 0
    try:
        if p.exists():
            data = json.loads(p.read_text())
            disengage_until = data.get("disengage_until", 0)
    except Exception:
        pass

    disengage_age_ms = now_ms - disengage_until  # negative = still active (until > now)
    disengage_ahead_ms = disengage_until - now_ms  # positive = time remaining

    try:
        file_age_ms = (time.time() - p.stat().st_mtime) * 1000 if p.exists() else 0
    except Exception:
        file_age_ms = 0

    # Use block-counter file age as fallback if disengage file missing
    effective_age_ms = file_age_ms if file_age_ms > 0 else block_file_age_s * 1000

    ci_pending, ci_run_id = _ci_is_pending_or_red()
    agent_active = mtime_age is not None and mtime_age < AUTO_REENGAGE_AGENT_ACTIVE_SECS
    push_running = _is_push_running()

    should_reengage = False
    reason = ""

    # Rule 1: push completed + agent active → re-engage immediately
    if not push_running and agent_active:
        rc = _ci_is_pending_or_red()
        if not rc[0]:
            should_reengage = True
            reason = "push completed, CI green, agent active"
        elif effective_age_ms > DISENGAGE_MAX_SECS_CI_NOT_GREEN * 1000:
            should_reengage = True
            reason = f"push completed, disengage capped at {DISENGAGE_MAX_SECS_CI_NOT_GREEN}s (CI pending/red)"
        # else: push done but CI still pending/red and within 5min cap — leave disengaged

    # Rule 2: disengage >2 min + agent active — re-engage regardless of push state
    if not should_reengage and agent_active:
        if effective_age_ms > AUTO_REENGAGE_DISENGAGE_AGE_SECS * 1000:
            should_reengage = True
            reason = (
                f"disengage >{AUTO_REENGAGE_DISENGAGE_AGE_SECS}s, agent active "
                f"(mtime_age={mtime_age:.0f}s, ci={'pending/red' if ci_pending else 'green'}, "
                f"push={'running' if push_running else 'done'})"
            )

    # Rule 3: 5-minute hard cap — regardless of push state or agent activity
    if not should_reengage and effective_age_ms > DISENGAGE_MAX_SECS_CI_NOT_GREEN * 1000:
        should_reengage = True
        reason = f"disengage_cap: {DISENGAGE_MAX_SECS_CI_NOT_GREEN}s max (ci={'pending/red' if ci_pending else 'green'}, agent={'active' if agent_active else 'idle'})"

    if not should_reengage:
        return

    # -- Re-engage: clear block counter and disengage file --
    try:
        Path(BLOCK_COUNTER_FILE).write_text(json.dumps({
            "consecutiveBlocks": 0,
            "totalBlocks": 0,
            "lastBlockTs": 0,
            "disengageUntil": 0,
        }))
    except Exception:
        pass

    try:
        if p.exists():
            p.unlink(missing_ok=True)
    except Exception:
        pass

    _log(f"watchdog: auto-re-engaged enforcement — {reason}")

    if ci_pending:
        _log(f"watchdog: CI still pending (run {ci_run_id}) — enforcement re-engaged; agent must fix CI")


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
    directive = _build_continue_directive(
        work_sources=work_sources,
        stop_count=stop_count,
        tasks_unchecked=tasks_unchecked,
        ratchet_count=ratchet_count,
        gate_red=gate_red,
        ci_pending=ci_pending,
        ci_run_id=ci_run_id,
        work_hint=work_hint,
        extra_message=extra_message,
    )

    # JSON for plugin consumption
    try:
        Path(CONTINUE_DIRECTIVE).write_text(json.dumps(directive, indent=2))
        _log(f"directive written to {CONTINUE_DIRECTIVE} (stop_count={stop_count})")
    except Exception as e:
        _log(f"ERROR writing JSON directive: {e}")

    # LOUD plain-text directive for agent context injection
    pending_items_str = "\n".join(f"  - {item}" for item in directive["pending_items"])
    txt = (
        "\n"
        "======================================================================\n"
        "⛔⛔⛔ WATCHDOG CONTINUE DIRECTIVE ⛔⛔⛔\n"
        "======================================================================\n"
        f"ACTION: {directive['action']}\n"
        f"STOP COUNT: {stop_count} (escalation threshold: {STOP_ESCALATE_THRESHOLD})\n"
        f"REQUIRED TOOL: {directive['required_tool']}\n"
        f"SOURCE: {directive['source']}\n"
        f"TS: {directive['ts']}\n"
        "----------------------------------------------------------------------\n"
        "PENDING WORK:\n"
        f"{pending_items_str}\n"
        "----------------------------------------------------------------------\n"
        "MESSAGE:\n"
        f"  {directive['message']}\n"
    )
    if extra_message:
        txt += (
            "\n----------------------------------------------------------------------\n"
            f"ESCALATION: {extra_message}\n"
        )
    txt += (
        "======================================================================\n"
        "YOU MUST DISPATCH SUBAGENTS NOW. DO NOT SEND TEXT-ONLY RESPONSES.\n"
        "======================================================================\n"
    )
    try:
        Path(PURE_IDLE_DIRECTIVE).write_text(txt)
        _log(f"loud directive written to {PURE_IDLE_DIRECTIVE}")
    except Exception as e:
        _log(f"ERROR writing plain-text directive: {e}")


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
) -> dict:
    pending_items: list[str] = []
    if tasks_unchecked:
        pending_items.append("TASKS.md has unchecked items")
    if ratchet_count > 0:
        pending_items.append(f"{ratchet_count} ratchet entries")
    if gate_red:
        pending_items.append(".gate-status is red")
    if ci_pending:
        suffix = f" (run {ci_run_id})" if ci_run_id else ""
        pending_items.append(f"CI pending{suffix}")

    # Build SPECIFIC dispatch commands from TASKS.md unchecked items,
    # ratchet entries, and gate status — so the CONTINUE directive lists
    # exact tasks to dispatch, not a generic "do work" nudge.
    dispatch_commands: list[dict] = []
    task_index = 1
    if tasks_unchecked and _TASKS_MD.exists():
        try:
            content = _TASKS_MD.read_text(encoding="utf-8")
            for line in content.splitlines():
                if _UNCHECKED_PATTERN.search(line):
                    item_text = line.strip()
                    dispatch_commands.append({
                        "index": task_index,
                        "task_item": item_text,
                        "tool": "task",
                        "command": f"dispatch subagent: {item_text}",
                    })
                    task_index += 1
        except Exception:
            pass

    if ratchet_count > 0:
        dispatch_commands.append({
            "index": task_index,
            "task_item": f"ratchet: {ratchet_count} entries",
            "tool": "task",
            "command": f"dispatch subagents to fix {ratchet_count} ratchet entries",
        })
        task_index += 1

    if gate_red:
        dispatch_commands.append({
            "index": task_index,
            "task_item": "gate: red — fix failures",
            "tool": "task",
            "command": "dispatch subagent to investigate and fix red gate",
        })
        task_index += 1

    msg_parts = [
        f"FORCE DISPATCH: {len(dispatch_commands)} specific tasks below. Dispatch ALL of them NOW."
    ]
    if work_hint.strip():
        msg_parts.append(work_hint.strip())
    if extra_message.strip():
        msg_parts.append(extra_message.strip())

    return {
        "action": "FORCE_DISPATCH",
        "pending_items": pending_items,
        "required_tool": "task",
        "dispatch_count": len(dispatch_commands),
        "dispatch_commands": dispatch_commands,
        "message": " ".join(msg_parts),
        "stop_count": stop_count,
        "source": ", ".join(work_sources) if work_sources else "unknown",
        "ts": _now(),
    }


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
    try:
        p = Path(LIVENESS_STARTUP_BACKOFF_FILE)
        if p.exists():
            data = json.loads(p.read_text())
            last_ts = float(data.get("last_check_ts", 0))
            if time.time() - last_ts < LIVENESS_STARTUP_BACKOFF_SECS:
                return True
    except Exception:
        pass
    return False


def _liveness_write_backoff_ts() -> None:
    try:
        Path(LIVENESS_STARTUP_BACKOFF_FILE).write_text(
            json.dumps({"last_check_ts": time.time()})
        )
    except Exception:
        pass


def _check_plugin_liveness_on_startup() -> None:
    """Run the plugin liveness check once at startup and log the result.

    Skips the check if it was already run within LIVENESS_STARTUP_BACKOFF_SECS
    (file-based backoff persists across watchdog restarts).
    """
    global _last_liveness_check
    if _liveness_startup_in_backoff():
        _log("plugin-liveness: backoff active — skipping startup check "
             f"(last check <{LIVENESS_STARTUP_BACKOFF_SECS}s ago)")
        _last_liveness_check = time.time()
        return
    _log("plugin-liveness: running startup check...")
    try:
        result = subprocess.run(
            ["make", "check-plugin-liveness"],
            capture_output=True, text=True, timeout=30,
            cwd=str(_WORKSPACE),
        )
        if result.returncode == 0:
            _log("plugin-liveness: PASSED — enforce-stop.ts structurally intact and firing")
        else:
            _log(f"plugin-liveness: FAILED (exit={result.returncode}) — "
                 f"enforce-stop.ts may be dead or silently disabled")
            _log(f"  stderr: {result.stderr.strip()[:300]}")
        _last_liveness_check = time.time()
        _liveness_write_backoff_ts()
    except subprocess.TimeoutExpired:
        _log("plugin-liveness: TIMEOUT — check took >30s")
        _liveness_write_backoff_ts()
    except Exception as e:
        _log(f"plugin-liveness: ERROR running check: {e}")
        _liveness_write_backoff_ts()


def _check_plugin_liveness_periodic() -> None:
    """Run plugin liveness check every LIVENESS_CHECK_COOLDOWN_SECS."""
    global _last_liveness_check
    now = time.time()
    if now - _last_liveness_check < LIVENESS_CHECK_COOLDOWN_SECS:
        return
    try:
        result = subprocess.run(
            ["make", "check-plugin-liveness"],
            capture_output=True, text=True, timeout=30,
            cwd=str(_WORKSPACE),
        )
        if result.returncode != 0:
            _log(f"plugin-liveness: periodic check FAILED (exit={result.returncode})")
    except Exception:
        pass
    _last_liveness_check = now


def _is_force_dispatch_active() -> bool:
    p = Path(FORCE_DISPATCH_FILE)
    if not p.exists():
        return False
    try:
        age = time.time() - p.stat().st_mtime
        return age <= FORCE_DISPATCH_MAX_AGE
    except Exception:
        return False


def _check_force_dispatch() -> bool:
    """Read /tmp/gludd-force-dispatch.json from enforce-stop.ts escalation level 3+.

    Builds specific task dispatch commands for each unchecked TASKS.md item,
    ratchet entry, and red gate.  Writes to CONTINUE_DIRECTIVE with
    action=FORCE_DISPATCH.

    Returns True if force-dispatch is active (lower idle threshold).
    """
    p = Path(FORCE_DISPATCH_FILE)
    if not p.exists():
        return False

    try:
        mtime = p.stat().st_mtime
        age = time.time() - mtime
        if age > FORCE_DISPATCH_MAX_AGE:
            p.unlink(missing_ok=True)
            return False

        data = json.loads(p.read_text())
        level = data.get("level", 3)

        tasks_unchecked = _tasks_md_has_unchecked()
        ratchet_count = _ratchet_has_entries()
        gate_red = _gate_status_is_red()

        dispatch_commands: list[dict] = []
        task_index = 1

        if tasks_unchecked and _TASKS_MD.exists():
            content = _TASKS_MD.read_text(encoding="utf-8")
            for line in content.splitlines():
                if _UNCHECKED_PATTERN.search(line):
                    item_text = line.strip()
                    dispatch_commands.append({
                        "index": task_index,
                        "task_item": item_text,
                        "tool": "task",
                        "command": f"dispatch subagent: {item_text}",
                    })
                    task_index += 1

        if ratchet_count > 0:
            dispatch_commands.append({
                "index": task_index,
                "task_item": f"ratchet: {ratchet_count} entries",
                "tool": "task",
                "command": f"dispatch subagents to fix {ratchet_count} ratchet entries",
            })

        if gate_red:
            dispatch_commands.append({
                "index": task_index + 1,
                "task_item": "gate: red — fix failures",
                "tool": "task",
                "command": "dispatch subagent to investigate and fix red gate",
            })

        if dispatch_commands:
            directive = {
                "action": "FORCE_DISPATCH",
                "level": level,
                "dispatch_count": len(dispatch_commands),
                "dispatch_commands": dispatch_commands,
                "message": (
                    f"FORCE DISPATCH (level {level}): "
                    f"Dispatch {len(dispatch_commands)} subagents NOW. "
                    f"Do NOT send text-only responses."
                ),
                "ts": _now(),
            }
            Path(CONTINUE_DIRECTIVE).write_text(json.dumps(directive, indent=2))
            _log(f"FORCE DISPATCH: level={level}, {len(dispatch_commands)} commands written")
        else:
            p.unlink(missing_ok=True)
            _log("FORCE DISPATCH: flag cleared — no pending work found")

        return bool(dispatch_commands)

    except Exception as e:
        _log(f"FORCE DISPATCH: error processing flag: {e}")
        return False


def check_and_reset() -> dict:
    global _POLL_CYCLE_COUNT
    result = {
        "ts": _now(),
        "streak": _read_streak(),
        "pending_todos": [],
        "reset_applied": False,
        "hibernating": HIBERNATION_MARKER.exists(),
        "stop_detected": False,
    }

    pending = _pending_todos()
    result["pending_todos"] = pending

    streak = result["streak"]

    reset_needed = False
    reason = ""

    # ── ALWAYS CHECK pending work on every poll cycle (not just on state changes) ──
    tasks_unchecked = _tasks_md_has_unchecked()
    ratchet_count = _ratchet_has_entries()
    gate_red = _gate_status_is_red()
    ci_pending, ci_run_id = _ci_is_pending_or_red()
    has_pending_work = tasks_unchecked or ratchet_count > 0 or gate_red or ci_pending
    has_any_work = has_pending_work or ci_pending

    # ── Gate-status injection: when CI is pending/red and .gate-status is otherwise
    #     clean, write a CI-FAIL line. The enforce-stop.ts plugin reads .gate-status
    #     to compute hasLocalWork. Without this, text-only responses pass through
    #     unblocked when CI is the only pending work. ──
    if ci_pending and not gate_red:
        try:
            gs = Path(_GATE_STATUS)
            gs.parent.mkdir(parents=True, exist_ok=True)
            gs.write_text(
                f"=== GATE {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} ===\n"
                f"CI FAIL pending (run {ci_run_id})\n"
                f"=== GATE: FAILED ===\n"
            )
            gate_red = True
            has_pending_work = True
        except Exception:
            pass
    mtime_age = _streak_mtime_age_seconds()

    # ── HEARTBEAT: write every poll cycle so operator can see watchdog is alive ──
    try:
        Path(HEARTBEAT_FILE).write_text(json.dumps({
            "ts": _now(),
            "epoch": time.time(),
            "poll_cycle": _POLL_CYCLE_COUNT + 1,
            "streak": streak,
            "mtime_age_s": round(mtime_age, 1) if mtime_age else None,
            "has_pending_work": has_pending_work,
            "tasks_md_unchecked": tasks_unchecked,
            "ratchet_entries": ratchet_count,
            "gate_status_red": gate_red,
            "ci_pending_or_red": ci_pending,
            "ci_run_id": ci_run_id,
            "pending_todo_count": len(pending),
            "stop_count": _read_stop_count(),
        }, indent=2))
    except Exception:
        pass

    if mtime_age is not None and mtime_age < PURE_IDLE_SECS:
        _write_watchdog_activity()

    idle_threshold = FORCE_DISPATCH_IDLE_SECS if _is_force_dispatch_active() else STOP_IDLE_SECS

    # ── Log pending-work status every cycle so we can observe what the watchdog sees ──
    if HEARTBEAT_VERBOSE and has_any_work:
        sources = []
        if tasks_unchecked:
            sources.append("TASKS.md")
        if ratchet_count > 0:
            sources.append("ratchet")
        if gate_red:
            sources.append("gate")
        if ci_pending:
            sources.append(f"CI(run={ci_run_id})")
        _log(f"watchdog: pending work detected — sources={sources} mtime_age={mtime_age:.0f}s" if mtime_age else f"watchdog: pending work detected — sources={sources}")

    # ── Stop detection via pending-work + streak mtime ───────────────────
    # Fire when: agent is silent (streak==0/None) + mtime old + work pending
    if has_any_work and (streak == 0 or streak is None) and mtime_age is not None and mtime_age > idle_threshold:
        reset_needed = True
        work_sources = []
        if has_pending_work:
            work_sources.append("local")
        if ci_pending:
            work_sources.append(f"CI (run {ci_run_id})")
        reason = f"STOP DETECTED: agent idle with pending work ({', '.join(work_sources)}) — {mtime_age:.0f}s since last tool (threshold={idle_threshold}s)"
        result["stop_detected"] = True
        _log(reason)

        stop_count = _increment_stop_count()
        extra_message = ""
        if stop_count >= STOP_ESCALATE_THRESHOLD:
            extra_message = f"REPEATED STOP DETECTED ({stop_count}x) — WORK OR FACE RESTART"

        work_hint = ""
        if ci_pending and not has_pending_work:
            ci_minutes = _ci_pending_for_too_long_minutes()
            if ci_minutes and ci_minutes > 10:
                work_hint = (
                    f"CI pending >{ci_minutes:.0f}min. "
                    "Stop pushing new commits — they reset CI. "
                    "Work on wiring/coding gaps while waiting."
                )
            else:
                work_hint = (
                    "CI pending. Work on wiring/coding gaps while waiting. "
                    "Do NOT push new commits until CI is green."
                )

        _write_continue_directive(
            work_sources=work_sources,
            stop_count=stop_count,
            tasks_unchecked=tasks_unchecked,
            ratchet_count=ratchet_count,
            gate_red=gate_red,
            ci_pending=ci_pending,
            ci_run_id=ci_run_id,
            work_hint=work_hint,
            extra_message=extra_message,
        )

        # Clear stop-state file if it exists, so plugin doesn't double-block
        sp = Path(STOP_STATE)
        if sp.exists():
            try:
                sp.unlink()
                _log(f"cleared stop-state: {sp}")
            except Exception:
                pass

    # ── ALSO detect grinding-in-place: agent has streak>0 (making calls) ──
    # but hasn't cleared pending work and streak file is very stale (>30s)
    elif has_pending_work and streak is not None and streak > 0 and mtime_age is not None and mtime_age > STOP_IDLE_SECS * 2:
        reset_needed = True
        work_sources = ["local"]
        reason = (
            f"STOP DETECTED (grinding): agent has streak={streak} but "
            f"mtime_age={mtime_age:.0f}s > {STOP_IDLE_SECS * 2}s with pending work — "
            f"likely stuck in a loop"
        )
        result["stop_detected"] = True
        _log(reason)

        stop_count = _increment_stop_count()
        extra_message = ""
        if stop_count >= STOP_ESCALATE_THRESHOLD:
            extra_message = f"REPEATED STOP DETECTED ({stop_count}x) — AGENT MAY BE LOOPING"

        _write_continue_directive(
            work_sources=work_sources,
            stop_count=stop_count,
            tasks_unchecked=tasks_unchecked,
            ratchet_count=ratchet_count,
            gate_red=gate_red,
            ci_pending=ci_pending,
            ci_run_id=ci_run_id,
            work_hint="Agent has streak but mtime is stale — likely grinding in a loop. Dispatch subagents to break out.",
            extra_message=extra_message,
        )

    # ── Existing: streak threshold ───────────────────────────────────────
    elif streak is not None and streak >= STREAK_THRESHOLD:
        reset_needed = True
        reason = f"streak={streak} >= threshold={STREAK_THRESHOLD}"

    # ── Existing: agent stalled on stop enforcement ──────────────────────
    elif check_agent_stalled():
        reset_needed = True
        reason = "agent stalled on stop enforcement"

    # ── Existing: text-only response with pending todos ──────────────────
    elif pending and streak is not None and streak > 0:
        if mtime_age is not None and mtime_age < POLL_SECS:
            reset_needed = True
            reason = "text-only response with pending todos"

    # ── Task anomaly detection (plugin format: {task_id: epoch_ms}) ──────
    task_result = check_task_anomalies()
    if task_result["anomalies"]:
        result["task_anomalies"] = task_result["anomalies"]
    if task_result["stalled"]:
        result["task_stalled"] = task_result["stalled"]
        Path(EX_STALLED_TASKS_FILE).write_text(
            json.dumps({"ts": _now(), "stalled": task_result["stalled"]}, indent=2)
        )
        _log(f"STALLED TASK DETECTED: {len(task_result['stalled'])} task(s) — writing {EX_STALLED_TASKS_FILE}")

    _check_push_stalled()
    _check_task_anomaly_300s()
    _check_ci_pending_stall()

    # ── ALWAYS: Max out false-done block counter to unjam agent ──────────
    # Item 11: Smart false-done maxout — only when CI-only pending + agent active
    if ci_pending and not has_pending_work and mtime_age is not None and mtime_age < PURE_IDLE_SECS:
        _max_out_false_done()
    elif has_pending_work:
        _max_out_false_done()
    # else: leave false-done blocks alone (agent may be genuinely stopped)
        # ── Pure idle detection (ANY idle >PURE_IDLE_SECS, regardless of pending work) ──
    if not reset_needed and mtime_age is not None and mtime_age > PURE_IDLE_SECS:
        last_flag = _read_last_flag_time()
        now = time.time()
        if now - last_flag > FLAG_COOLDOWN_SECS:
            _log(f"IDLE DETECTED: agent idle >{PURE_IDLE_SECS}s ({mtime_age:.0f}s since last tool)")
            _write_last_flag_time(now)
            _write_continue_directive(
                work_sources=["pure_idle"],
                stop_count=_read_stop_count(),
                tasks_unchecked=tasks_unchecked,
                ratchet_count=ratchet_count,
                gate_red=gate_red,
                ci_pending=ci_pending,
                ci_run_id=ci_run_id,
                work_hint="",
                extra_message=f"Pure idle detected — agent silent for {mtime_age:.0f}s",
            )
            reset_needed = True
            reason = f"pure idle detected ({mtime_age:.0f}s)"
            result["stop_detected"] = True

    # ── Task duration anomaly detection with history tracking ─────────────
    deadlines = _read_deadlines()
    _update_task_history(deadlines)
    history_anomalies = _detect_history_anomalies(deadlines)
    if history_anomalies:
        result["history_anomalies"] = history_anomalies
        try:
            Path(TASK_ANOMALIES_FILE).write_text(
                json.dumps({"ts": _now(), "anomalies": history_anomalies}, indent=2)
            )
        except Exception:
            pass
        for a in history_anomalies:
            rolling_info = f", rolling_avg={a['rolling_avg_s']}s" if a.get("rolling_avg_s") else ""
            _log(
                f"TASK ANOMALY: task {a['id']} ({a['type']}) "
                f"{a['description']} running {a['elapsed_s']}s — "
                f"reason={a['reason']}{rolling_info}"
            )

    # ── NEW: CI pipeline health monitoring ───────────────────────────────
    _check_ci_stall()
    _check_push_health()
    check_task_timings()

    # ── NEW: Timing anomaly detection ────────────────────────────────────
    timing_anomalies = _check_timing_anomalies()
    push_anomaly = _detect_stalled_push()
    if push_anomaly:
        timing_anomalies.append("git-push")
    if timing_anomalies:
        result["timing_anomalies"] = timing_anomalies
        anchored_messages: list[str] = []
        for op in timing_anomalies:
            expected = EXPECTED_DURATIONS.get(op, 300)
            timing = _read_timing_data()
            entry = timing.get(op, {})
            actual = entry.get("duration", 0)
            anchored_messages.append(
                f"\u26d4 TIMING ANOMALY: {op} running for {actual:.0f}s "
                f"(expected {expected}s). Check for network issues."
            )
        try:
            existing = ""
            directive_p = Path(PURE_IDLE_DIRECTIVE)
            if directive_p.exists():
                existing = directive_p.read_text()
            directive_p.write_text(existing + "\n".join(anchored_messages) + "\n")
        except Exception:
            pass

    # ── Periodic prune of _alerted_anomalies ──────────────────────────────
    _POLL_CYCLE_COUNT += 1
    if _POLL_CYCLE_COUNT % _POLL_CYCLE_PRUNE_INTERVAL == 0:
        _prune_alerted_anomalies()
        _check_plugin_hashes()

    # ── Apply reset ──────────────────────────────────────────────────────
    if reset_needed:
        _reset_streak()
        result["reset_applied"] = True

        # If the stop was NOT detected by our new logic, check and write directive
        if not result.get("stop_detected"):
            if check_agent_stalled():
                _write_continue_directive(
                    work_sources=["agent_stalled"],
                    stop_count=_read_stop_count(),
                    tasks_unchecked=tasks_unchecked,
                    ratchet_count=ratchet_count,
                    gate_red=gate_red,
                    ci_pending=False,
                    extra_message=f"agent stalled on stop enforcement, pending={len(pending)} todos",
                )

        if pending:
            _log(f"UNJAMMED: {reason}, pending={len(pending)} todos: {pending[:3]}")
        else:
            _log(f"UNJAMMED: {reason}, no pending todos detected but resetting anyway")

    # ── No reset: stop count decays if agent is active ───────────────────
    elif not has_any_work and mtime_age is not None and mtime_age < POLL_SECS:
        _clear_stop_count()
    elif ci_pending and not has_pending_work:
        ci_minutes = _ci_pending_for_too_long_minutes()
        if ci_minutes is not None and ci_minutes > 30:
            _log(f"CI STALLED: pending >30min (run {ci_run_id}) — may need investigation")
        elif ci_minutes is not None and ci_minutes > 10:
            _log(f"CI NOTE: pending {ci_minutes:.0f}min (run {ci_run_id}) — stop pushing new commits")
        else:
            _log(f"CI pending (run {ci_run_id}) — work locally while waiting")
    elif streak is not None:
        pass
    else:
        _log("streak file missing — enforcement may not be tracking")

    # ── Stalled task detection: idle streak + long-running task ──────────
    if mtime_age is not None and mtime_age > 20:
        task_state_path = Path(TASK_STATE_FILE)
        if task_state_path.exists():
            try:
                tasks = json.loads(task_state_path.read_text())
                if isinstance(tasks, dict):
                    tasks = [tasks]
                if isinstance(tasks, list):
                    now = time.time()
                    for task in tasks:
                        if not isinstance(task, dict):
                            continue
                        started = task.get("started", 0)
                        name = task.get("name", "unknown")
                        pid = task.get("pid")
                        if not started:
                            continue
                        elapsed = now - started
                        if elapsed > 60:
                            _log(f"STALLED TASK: {name} running {elapsed:.0f}s")
                            if pid:
                                kill_stalled_task(pid)
                            _reset_streak()
                            result["reset_applied"] = True
                            result["stop_detected"] = True
            except Exception:
                pass

    # ── Items 9-10: CI loop and true stall detection ─────────────────────
    ci_loop = _detect_ci_loop()
    ci_true_stall = _detect_ci_true_stall()
    if ci_loop:
        _log(f"CI LOOP DETECTED: >{CI_LOOP_THRESHOLD_PUSHES} pushes in <{CI_LOOP_THRESHOLD_MINUTES}min while CI pending. STOP PUSHING.")
        _write_disengage_signal(minutes=10, reason="ci_loop")
    if ci_true_stall:
        _log(f"CI TRUE STALL: pending >{CI_TRUE_STALL_MINUTES}min with no pushes for {CI_TRUE_STALL_NO_PUSH_MINUTES}min. CI may be broken.")

    # ── Item 13: Write unified orchestrator state ────────────────────────
    agent_active = mtime_age is not None and mtime_age < PURE_IDLE_SECS
    _write_orchestrator_state(
        tasks_unchecked=tasks_unchecked,
        ratchet_count=ratchet_count,
        gate_red=gate_red,
        ci_pending=ci_pending,
        repo_pending=False,
        agent_active=agent_active,
        ci_run_id=ci_run_id,
        stop_detected=result.get("stop_detected", False),
    )
    _clear_disengage_signal()
    _auto_reengage_enforcement(mtime_age)

    return result


# -- CLI ----------------------------------------------------------------------


def _cli_classification(argv: list[str]) -> int:
    """Handle --once, --count-stalled, --list-stalled, --all flags."""
    tasks_dir = Path(argv[0]) if argv and not argv[0].startswith("--") else Path("/tmp/gludd-tasks")
    results = scan_tasks_dir(tasks_dir)

    if "--once" in argv:
        result = check_and_reset()
        print(json.dumps(result, indent=2))
        return 0

    if "--count-stalled" in argv:
        count = sum(1 for _, s, _ in results if s == State.LIKELY_STALLED_INCOMPLETE)
        print(count)
        return 0

    if "--list-stalled" in argv:
        for name, state, _reason in results:
            if state == State.LIKELY_STALLED_INCOMPLETE:
                print(f"{name}  {state.value}")
        return 0

    if "--all" in argv:
        for name, state, reason in results:
            print(f"{name}  {state.value}  ({reason})")
        return 0

    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if argv and any(a.startswith("--") or not a.startswith("-") for a in argv):
        return _cli_classification(argv)

    _log(f"watchdog started — poll={POLL_SECS}s, threshold={STREAK_THRESHOLD}")
    _check_plugin_liveness_on_startup()
    while True:
        if HIBERNATION_MARKER.exists():
            _log("hibernation marker present — sleeping")
            time.sleep(POLL_SECS)
            continue
        try:
            check_and_reset()
            _check_force_dispatch()
            check_running_tasks()
            check_push_status()
            _check_gate_background()
            _check_plugin_liveness_periodic()
        except Exception as exc:
            _log(f"error: {exc}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    raise SystemExit(main())
