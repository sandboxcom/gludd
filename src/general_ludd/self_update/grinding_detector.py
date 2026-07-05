"""Auto-detect broken-agent patterns and generate self-improvement fix todos.

Reads enforcement-plugin state files from ``/tmp/`` and creates
self-improvement todo dicts for each detected failure mode. Designed to be
called from ``_phase_self_improve`` so the harness turns its own broken
behaviour into fix tasks without operator intervention.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── canonical paths for enforcement-plugin state files ──────────────────────
_STREAK_FILE = "/tmp/gludd-floor-streak.json"
_STOP_STATE_FILE = "/tmp/gludd-stop-state.json"
_TASK_DEADLINE_FILE = "/tmp/gludd-task-deadlines.json"

# ── detection thresholds ────────────────────────────────────────────────────
_STREAK_HIGH = 10
_WINDOW_SECONDS = 300  # 5 min
_BLOCKS_HIGH = 2


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("grinding_detector: failed to read %s: %s", path, exc)
        return {}


def _recent_count(record: dict[str, Any], window: float, key: str) -> int:
    """Count entries with a ``timestamp`` (or ``ts`` / ``time``) within *window*
    seconds of now, with *key* set to a truthy value."""
    now = time.time()
    entries = record.get("entries") or record.get("history") or []
    if not isinstance(entries, list):
        return 0
    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("timestamp") or entry.get("ts") or entry.get("time") or 0
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            continue
        if now - ts > window:
            continue
        if entry.get(key):
            count += 1
    return count


def _recent_single_value(record: dict[str, Any], window: float, key: str) -> float:
    """If the file stores a single scalar + timestamp, return the scalar when
    the timestamp is recent, else 0."""
    ts = record.get("timestamp") or record.get("ts") or record.get("time") or 0
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return 0.0
    if time.time() - ts > window:
        return 0.0
    val = record.get(key, 0)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _recent_max_streak(record: dict[str, Any], window: float) -> int:
    """The enforce-floor.ts plugin may write a raw ``streak`` counter with a
    timestamp, or a history of streak readings."""
    # Simple scalar case: {"streak": 12, "timestamp": 1712345678.0}
    ts = record.get("timestamp") or record.get("ts") or record.get("time") or 0
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = 0
    if time.time() - ts <= window:
        streak = record.get("streak", 0)
        try:
            s = int(streak)
            if s > 0:
                return s
        except (TypeError, ValueError):
            pass

    # History / entries case
    entries = record.get("entries") or record.get("history") or []
    if not isinstance(entries, list):
        return 0
    max_s = 0
    now = time.time()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ets = entry.get("timestamp") or entry.get("ts") or entry.get("time") or 0
        try:
            ets = float(ets)
        except (TypeError, ValueError):
            continue
        if now - ets > window:
            continue
        streak = entry.get("streak", 0)
        try:
            s = int(streak)
            if s > max_s:
                max_s = s
        except (TypeError, ValueError):
            continue
    return max_s


def _recent_dispatch_count(record: dict[str, Any], window: float) -> int:
    """Count recent dispatches from the floor state file."""
    entries = record.get("entries") or record.get("history") or []
    if not isinstance(entries, list):
        return 0
    now = time.time()
    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ets = entry.get("timestamp") or entry.get("ts") or entry.get("time") or 0
        try:
            ets = float(ets)
        except (TypeError, ValueError):
            continue
        if now - ets > window:
            continue
        dispatched = entry.get("dispatched") or entry.get("dispatch_count") or 0
        try:
            if int(dispatched) > 0:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


def detect_and_create_todos() -> list[dict[str, Any]]:
    """Scan enforcement state files and return self-improvement todo dicts
    for every detected broken-agent pattern.

    Each returned dict is compatible with
    ``SelfImprovementHarness.generate_fix_todos`` and
    ``EventLoop._persist_self_improve_todos``.
    """
    todos: list[dict[str, Any]] = []

    # ── 1. high streak → agent grinding inline ──────────────────────────
    streak_data = _read_json(_STREAK_FILE)
    max_streak = _recent_max_streak(streak_data, _WINDOW_SECONDS)
    if max_streak > _STREAK_HIGH:
        todos.append({
            "title": "Fix enforce-floor.ts streaking — agent is grinding inline",
            "description": (
                f"Floor streak reached {max_streak} in the last "
                f"{_WINDOW_SECONDS}s (threshold: {_STREAK_HIGH}). "
                "The agent is running too many tools on the main thread instead "
                "of dispatching subagents. Investigate and fix the streaking "
                "pattern in enforce-floor.ts or the agent's dispatch behaviour."
            ),
            "work_type": "code",
            "priority": "high",
            "source": "grinding_detector",
            "gap_type": "agent_grinding",
            "evidence": {
                "max_streak": max_streak,
                "window_seconds": _WINDOW_SECONDS,
                "threshold": _STREAK_HIGH,
            },
        })

    # ── 2. low dispatch count → not multitasking ─────────────────────────
    dispatch_count = _recent_dispatch_count(streak_data, _WINDOW_SECONDS) if max_streak <= 0 else 0
    if dispatch_count < 2 and not todos:  # only if not already flagged as grinding
        # Check if there's evidence of recent activity but no dispatches
        entries = streak_data.get("entries") or streak_data.get("history") or []
        now = time.time()
        recent_entries = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ets = entry.get("timestamp") or entry.get("ts") or entry.get("time") or 0
            try:
                ets = float(ets)
            except (TypeError, ValueError):
                continue
            if now - ets <= _WINDOW_SECONDS:
                recent_entries += 1
        if recent_entries > 5 and dispatch_count < 2:
            todos.append({
                "title": "Fix agent not multitasking — dispatch floor below minimum",
                "description": (
                    f"Only {dispatch_count} dispatches in the last {_WINDOW_SECONDS}s "
                    f"but the agent had {recent_entries} main-thread tool calls. "
                    "The 10-agent floor is not being maintained — the agent is "
                    "running everything inline instead of dispatching subagents."
                ),
                "work_type": "code",
                "priority": "high",
                "source": "grinding_detector",
                "gap_type": "low_dispatch",
                "evidence": {
                    "dispatch_count": dispatch_count,
                    "recent_entries": recent_entries,
                    "window_seconds": _WINDOW_SECONDS,
                },
            })

    # ── 3. frequent stop blocks → enforce-stop.ts false positives ────────
    stop_data = _read_json(_STOP_STATE_FILE)
    blocks = _recent_count(stop_data, _WINDOW_SECONDS, "blocked")
    if blocks > _BLOCKS_HIGH:
        todos.append({
            "title": "Fix enforce-stop.ts false positives — stop hooks firing too often",
            "description": (
                f"enforce-stop.ts blocked {blocks} tool calls in the last "
                f"{_WINDOW_SECONDS}s (threshold: {_BLOCKS_HIGH}). "
                "The stop-enforcement plugin is firing on legitimate work. "
                "Narrow the check to reduce false positives without removing enforcement."
            ),
            "work_type": "code",
            "priority": "high",
            "source": "grinding_detector",
            "gap_type": "stop_false_positives",
            "evidence": {
                "block_count": blocks,
                "window_seconds": _WINDOW_SECONDS,
                "threshold": _BLOCKS_HIGH,
            },
        })

    # ── 4. task deadlines exceeded → subagent timeout pattern ────────────
    deadline_data = _read_json(_TASK_DEADLINE_FILE)
    deadlines = _recent_count(deadline_data, _WINDOW_SECONDS * 2, "deadline_exceeded")
    if deadlines > _BLOCKS_HIGH:
        todos.append({
            "title": "Fix task deadline violations — subagents timing out",
            "description": (
                f"{deadlines} task deadline exceeded events in the last "
                f"{_WINDOW_SECONDS * 2}s. Subagents are running too long. "
                "Split oversized tasks, reduce per-subagent workload, or "
                "run long operations in the background instead."
            ),
            "work_type": "code",
            "priority": "medium",
            "source": "grinding_detector",
            "gap_type": "task_deadlines",
            "evidence": {
                "deadline_count": deadlines,
                "window_seconds": _WINDOW_SECONDS * 2,
                "threshold": _BLOCKS_HIGH,
            },
        })

    return todos
