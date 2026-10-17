"""Gate progress/ETA estimation — public facade.

The heavy lifting lives in sibling modules:
- `parsing` — log parsing, discovery, heartbeat-based timing
- `estimation` — duration averages, templates, ETA/percent, failure summary
- `_types` — shared dataclasses

This module keeps the small cooldown helpers and re-exports the public API
so callers (`scripts/gate_progress.py`, tests) keep importing from here.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from general_ludd.gate._types import GateEvent, GateRun, Heartbeat, ProgressReport
from general_ludd.gate.estimation import estimate_progress
from general_ludd.gate.parsing import discover_gate_logs, elapsed_at_line, parse_gate_log
from general_ludd.gate.render import failure_summary, render_progress

__all__ = [
    "GateEvent",
    "GateRun",
    "Heartbeat",
    "ProgressReport",
    "cooldown_remaining",
    "discover_gate_logs",
    "elapsed_at_line",
    "estimate_progress",
    "failure_summary",
    "parse_gate_log",
    "record_cooldown",
    "render_progress",
]


def cooldown_remaining(state_file: Path, cooldown_secs: float, now: float) -> float:
    """Return the remaining cooldown seconds before the next poll is allowed.

    State file is JSON with a last_check_epoch key. Corrupt or missing state
    means no cooldown (first poll always allowed).
    """
    if not state_file.exists():
        return 0.0
    try:
        raw = state_file.read_text(encoding="utf-8")
        last = float(json.loads(raw)["last_check_epoch"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 0.0
    return max(0.0, (last + cooldown_secs) - now)


def record_cooldown(state_file: Path, now: float) -> None:
    """Record a poll timestamp so rapid re-polls are refused."""
    with contextlib.suppress(OSError):
        state_file.write_text(json.dumps({"last_check_epoch": now}), encoding="utf-8")
