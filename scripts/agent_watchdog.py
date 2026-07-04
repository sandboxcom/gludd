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
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

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
            if line.strip().startswith(marker):
                return State.LIKELY_STALLED_INCOMPLETE, f"let me / continuing: '{marker}' prefix"

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
TODOWRITE_STATE = os.environ.get("GLUDD_TODOWRITE_STATE", "/tmp/gludd-todowrite-state.json")
RESET_LOG = "/tmp/gludd-auto-reset.log"
HIBERNATION_MARKER = Path("/tmp/gludd-watchdog-hibernating")
POLL_SECS = 10

STREAK_THRESHOLD = 3  # with 10s polling, threshold is reached in ~30s of sustained grinding
STOP_IDLE_SECS = 15  # streak file mtime older than this + pending work = text-only stop
STOP_COUNT_FILE = "/tmp/gludd-watchdog-stop-count.json"
STOP_ESCALATE_THRESHOLD = 3

STOP_STATE = os.environ.get("GLUDD_STOP_STATE", "/tmp/gludd-stop-state.json")
FALSE_DONE_BLOCKS = os.environ.get("GLUDD_FALSE_DONE_BLOCKS", "/tmp/gludd-false-done-blocks.json")
CONTINUE_DIRECTIVE = os.environ.get("GLUDD_CONTINUE_DIRECTIVE", "/tmp/gludd-continue-directive.txt")

_WORKSPACE = Path(os.environ.get("GLUDD_WORKSPACE", os.getcwd()))
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


def _streak_mtime_age_seconds() -> float | None:
    streak_path = Path(STREAK_FILE)
    if not streak_path.exists():
        return None
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


def check_and_reset() -> dict:
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

    # ── NEW: Stop detection via pending-work + streak mtime ──────────────
    has_pending_work = _pending_work_exists()
    mtime_age = _streak_mtime_age_seconds()

    if has_pending_work and streak == 0 and mtime_age is not None and mtime_age > STOP_IDLE_SECS:
        reset_needed = True
        reason = f"STOP DETECTED: agent idle with pending work ({mtime_age:.0f}s since last tool)"
        result["stop_detected"] = True
        _log(reason)

        stop_count = _increment_stop_count()
        if stop_count >= STOP_ESCALATE_THRESHOLD:
            directive = (
                f"[{_now()}] REPEATED STOP DETECTED ({stop_count}x) — WORK OR FACE RESTART\n"
            )
        else:
            directive = (
                f"[{_now()}] STOP DETECTED: pending work exists. "
                "Text-only responses suppressed. DISPATCH SUBAGENTS NOW.\n"
            )
        Path(CONTINUE_DIRECTIVE).write_text(directive)
        _log(f"directive written to {CONTINUE_DIRECTIVE} (stop_count={stop_count})")

        # Clear stop-state file if it exists, so plugin doesn't double-block
        sp = Path(STOP_STATE)
        if sp.exists():
            try:
                sp.unlink()
                _log(f"cleared stop-state: {sp}")
            except Exception:
                pass

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

    # ── Apply reset ──────────────────────────────────────────────────────
    if reset_needed:
        _reset_streak()
        result["reset_applied"] = True

        # If the stop was NOT detected by our new logic, check and write directive
        if not result.get("stop_detected"):
            if check_agent_stalled():
                directive = f"[{_now()}] CONTINUE: agent stalled on stop enforcement, pending={len(pending)} todos.\n"
                Path(CONTINUE_DIRECTIVE).write_text(directive)

        if pending:
            _log(f"UNJAMMED: {reason}, pending={len(pending)} todos: {pending[:3]}")
        else:
            _log(f"UNJAMMED: {reason}, no pending todos detected but resetting anyway")

    # ── No reset: stop count decays if agent is active ───────────────────
    elif not has_pending_work and mtime_age is not None and mtime_age < POLL_SECS:
        _clear_stop_count()
    elif streak is not None:
        pass
    else:
        _log("streak file missing — enforcement may not be tracking")

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
    while True:
        if HIBERNATION_MARKER.exists():
            _log("hibernation marker present — sleeping")
            time.sleep(POLL_SECS)
            continue
        try:
            check_and_reset()
        except Exception as exc:
            _log(f"error: {exc}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    raise SystemExit(main())
