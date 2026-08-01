"""Auto-detect broken-agent patterns and generate self-improvement fix todos.

Reads enforcement-plugin state files from ``/tmp/`` and creates
self-improvement todo dicts for each detected failure mode. Designed to be
called from ``_phase_self_improve`` so the harness turns its own broken
behaviour into fix tasks without operator intervention.

Also provides ``GrindingDetector``, a class that analyzes session-level
tool-call and text-response patterns — grinding episodes (4+ consecutive
non-dispatch tool calls), premature stops (text-only response + ≥30s idle),
and generates remediation reports.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from general_ludd.security.state import (
    SecureStateError,
    project_state,
    secure_write_text,
    trusted_owned_file,
)

logger = logging.getLogger(__name__)

# ── dispatch tool names ──────────────────────────────────────────────────
_DISPATCH_TOOLS = frozenset({"task", "agent", "workflow", "skill"})

# ── canonical paths for enforcement-plugin state files ──────────────────────
_STREAK_FILE: str | None = None
_STOP_STATE_FILE: str | None = None
_TASK_DEADLINE_FILE: str | None = None
_LEGACY_ENFORCEMENT_STATE_ROOT = Path(os.sep) / "tmp"

# ── detection thresholds ────────────────────────────────────────────────────
_STREAK_HIGH = 10
_WINDOW_SECONDS = 300  # 5 min
_BLOCKS_HIGH = 2


def _read_json(path: str) -> dict[str, Any]:
    if not trusted_owned_file(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("grinding_detector: failed to read %s: %s", path, exc)
        return {}


def _enforcement_state_file(override: str | None, filename: str) -> str:
    if override is not None:
        return override
    namespaced = project_state().path("enforcement", filename)
    try:
        namespaced.lstat()
    except OSError:
        legacy = _LEGACY_ENFORCEMENT_STATE_ROOT / f"gludd-{filename}"
        if trusted_owned_file(legacy):
            return str(legacy)
    return str(namespaced)


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
    streak_data = _read_json(
        _enforcement_state_file(_STREAK_FILE, "floor-streak.json")
    )
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
    stop_data = _read_json(
        _enforcement_state_file(_STOP_STATE_FILE, "stop-state.json")
    )
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
    deadline_data = _read_json(
        _enforcement_state_file(_TASK_DEADLINE_FILE, "task-deadlines.json")
    )
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


# ── Session-level pattern analysis ───────────────────────────────────────────


@dataclass
class GrindingEpisode:
    """A detected grinding episode: 4+ consecutive non-dispatch tool calls."""

    start_index: int
    end_index: int
    tool_count: int
    tool_names: list[str]
    start_timestamp: float
    end_timestamp: float


@dataclass
class StopEpisode:
    """A detected premature stop: text-only response + ≥30s idle."""

    response_index: int
    idle_seconds: float
    timestamp: float


@dataclass
class GrindingReport:
    """Aggregated grinding/premature-stop detection results."""

    grinding_episodes: list[GrindingEpisode] = field(default_factory=list)
    stop_episodes: list[StopEpisode] = field(default_factory=list)
    total_tool_calls_analyzed: int = 0
    total_responses_analyzed: int = 0
    generated_at: float = 0.0


class GrindingDetector:
    """Analyzes agent session data for grinding and premature-stop patterns.

    Operates on structured session tool-call and text-response records
    collected from opencode's tool-call log or ``/tmp/gludd-*-state.json`` files.
    Runs inside the daemon (full filesystem access), not as an opencode plugin.
    """

    _REPORT_PATH: str | None = None

    def __init__(
        self,
        streak_threshold: int = 4,
        idle_threshold: float = 30.0,
    ) -> None:
        self._streak_threshold = streak_threshold
        self._idle_threshold = idle_threshold
        self._report: GrindingReport = GrindingReport()

    # ── public API ───────────────────────────────────────────────────────

    def detect_grinding(
        self, session_tool_calls: list[dict[str, Any]]
    ) -> list[GrindingEpisode]:
        """Detect grinding episodes: 4+ consecutive non-dispatch tool calls.

        Each entry in *session_tool_calls* must have at minimum:
        ``{"tool_name": str, "timestamp": float}``.  An optional ``is_dispatch``
        bool is checked first; if absent, *tool_name* is matched against the
        known dispatch set (``task``, ``agent``, ``workflow``, ``skill``).
        """
        if not session_tool_calls:
            return []

        episodes: list[GrindingEpisode] = []
        streak_start: int | None = None
        streak_names: list[str] = []

        for i, call in enumerate(session_tool_calls):
            if not isinstance(call, dict):
                continue
            is_dispatch = self._is_dispatch(call)
            if is_dispatch:
                if streak_start is not None and len(streak_names) >= self._streak_threshold:
                    episodes.append(self._build_episode(
                        streak_start, i - 1, streak_names, session_tool_calls,
                    ))
                streak_start = None
                streak_names = []
            else:
                if streak_start is None:
                    streak_start = i
                streak_names.append(call.get("tool_name", "unknown"))

        # Trailing streak
        if streak_start is not None and len(streak_names) >= self._streak_threshold:
            episodes.append(self._build_episode(
                streak_start, len(session_tool_calls) - 1, streak_names, session_tool_calls,
            ))

        self._report.grinding_episodes = episodes
        self._report.total_tool_calls_analyzed = len(session_tool_calls)
        return episodes

    def detect_premature_stop(
        self, session_text_responses: list[dict[str, Any]]
    ) -> list[StopEpisode]:
        """Detect premature stops: text-only responses followed by ≥30s idle.

        Each entry must have: ``{"has_tool_calls": bool, "timestamp": float}``.
        Idle is computed as ``next_response.timestamp - current.timestamp``.
        The last response is only flagged if it has no tool calls and its
        timestamp is at least *idle_threshold* before *now*.
        """
        if not session_text_responses:
            return []

        episodes: list[StopEpisode] = []
        now = time.time()

        for i in range(len(session_text_responses)):
            resp = session_text_responses[i]
            if not isinstance(resp, dict):
                continue
            if resp.get("has_tool_calls", True):
                continue
            ts = resp.get("timestamp", 0)
            if i + 1 < len(session_text_responses):
                next_ts = session_text_responses[i + 1].get("timestamp", ts)
                idle_s = next_ts - ts
            else:
                idle_s = now - ts
            if idle_s >= self._idle_threshold:
                episodes.append(StopEpisode(
                    response_index=i,
                    idle_seconds=round(idle_s, 1),
                    timestamp=ts,
                ))

        self._report.stop_episodes = episodes
        self._report.total_responses_analyzed = len(session_text_responses)
        return episodes

    def generate_remediation_report(self) -> dict[str, Any]:
        """Write aggregated report to ``/tmp/gludd-grinding-report.json``.

        Returns the report as a dict and also writes it to disk.
        """
        self._report.generated_at = time.time()
        report_dict: dict[str, Any] = {
            "generated_at": self._report.generated_at,
            "grinding_episodes": [
                {
                    "start_index": e.start_index,
                    "end_index": e.end_index,
                    "tool_count": e.tool_count,
                    "tool_names": e.tool_names,
                    "start_timestamp": e.start_timestamp,
                    "end_timestamp": e.end_timestamp,
                }
                for e in self._report.grinding_episodes
            ],
            "stop_episodes": [
                {
                    "response_index": e.response_index,
                    "idle_seconds": e.idle_seconds,
                    "timestamp": e.timestamp,
                }
                for e in self._report.stop_episodes
            ],
            "total_tool_calls_analyzed": self._report.total_tool_calls_analyzed,
            "total_responses_analyzed": self._report.total_responses_analyzed,
        }
        try:
            report_path = self._REPORT_PATH or str(
                project_state().path("self-update", "grinding-report.json")
            )
            secure_write_text(report_path, json.dumps(report_dict, indent=2))
            logger.info(
                "Grinding report written to %s: %d grinding, %d stop episodes",
                report_path,
                len(self._report.grinding_episodes),
                len(self._report.stop_episodes),
            )
        except (OSError, SecureStateError) as exc:
            logger.warning("Failed to write grinding report: %s", exc)
        return report_dict

    # ── helpers ──────────────────────────────────────────────────────────

    def _is_dispatch(self, call: dict[str, Any]) -> bool:
        if call.get("is_dispatch") is True:
            return True
        return call.get("tool_name", "") in _DISPATCH_TOOLS

    def _build_episode(
        self,
        start: int,
        end: int,
        names: list[str],
        calls: list[dict[str, Any]],
    ) -> GrindingEpisode:
        start_ts = float(calls[start].get("timestamp", 0))
        end_ts = float(calls[end].get("timestamp", 0))
        return GrindingEpisode(
            start_index=start,
            end_index=end,
            tool_count=len(names),
            tool_names=list(names),
            start_timestamp=start_ts,
            end_timestamp=end_ts,
        )
