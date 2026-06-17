"""Detect background subagents that have "come to rest" mid-task.

Each agent writes output to a file under a tasks/transcript directory.
This tool classifies each agent's most-recent state as one of:

    ACTIVE                   — file modified within the recency window
                               (default 90 s, overridable by GLUDD_WATCHDOG_WINDOW_SECS)
    LIKELY_STALLED_INCOMPLETE — not recently modified AND the tail does NOT look
                               like a completed result (heuristics below)
    DONE                     — not recently modified AND the tail looks like a
                               final result or summary block

Stall heuristics (applied to the last non-empty line of the file):
  * ends with ':'    (e.g. "Continuing with the remaining files:")
  * starts with one of the continuation phrases (case-insensitive):
    "continuing", "let me", "next"
  * lacks any result/summary marker in the tail block

Completion markers (any of these in the last ~20 non-empty lines signals DONE):
  "result:", "summary:", "complete", "finished", "done", "all done",
  "✓", "passed", "failed:" (a stated failure is a conclusion, not a stall)

Pure-function core (classify_tail) takes (tail_text, age_seconds, window_seconds)
and returns a (State, reason) pair — no I/O, fully unit-testable.

CLI
---
  python scripts/agent_watchdog.py [tasks_dir] [--window N] [--list-stalled] [--count-stalled]

  tasks_dir defaults to GLUDD_TASKS_DIR env var, then ./tasks.
  Missing or empty dir: prints nothing, exits 0 (fail-safe).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from enum import Enum
from pathlib import Path

# ── classification ────────────────────────────────────────────────────────────

DEFAULT_WINDOW_SECS = 90


class State(str, Enum):
    ACTIVE = "ACTIVE"
    LIKELY_STALLED_INCOMPLETE = "LIKELY_STALLED_INCOMPLETE"
    DONE = "DONE"


# Phrases at the START of the last non-empty line (lowercased) that hint at
# an incomplete mid-sentence continuation rather than a final result.
_CONTINUATION_PREFIXES: tuple[str, ...] = (
    "continuing",
    "let me",
    "next",
    "now let",
    "i'll",
    "i will",
)

# Keywords anywhere in the last ~20 non-empty lines that indicate completion.
_DONE_MARKERS: tuple[str, ...] = (
    "result:",
    "summary:",
    "complete",
    "finished",
    "all done",
    "✓",
    "passed",
    "failed:",  # a stated failure is a conclusion, not a stall
    "no changes",
    "task complete",
)


def _last_nonempty_lines(text: str, n: int = 20) -> list[str]:
    """Return up to *n* trailing non-empty lines from *text*."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-n:]


def classify_tail(
    tail_text: str,
    age_seconds: float,
    window_seconds: float = DEFAULT_WINDOW_SECS,
) -> tuple[State, str]:
    """Classify an agent output tail.

    Parameters
    ----------
    tail_text:
        The full (or last portion of the) output file content.
    age_seconds:
        How many seconds ago the file was last modified.
    window_seconds:
        Files modified within this many seconds are considered ACTIVE.

    Returns
    -------
    (State, reason_string)
    """
    if age_seconds < window_seconds:
        return State.ACTIVE, f"modified {age_seconds:.0f}s ago (< {window_seconds:.0f}s window)"

    nonempty = _last_nonempty_lines(tail_text, 20)
    if not nonempty:
        # Empty output file — treat as stalled (never produced a result)
        return (
            State.LIKELY_STALLED_INCOMPLETE,
            "output file is empty or all-whitespace",
        )

    last_line = nonempty[-1].strip()
    last_lower = last_line.lower()

    # Check done markers first — any completion keyword wins
    tail_block = "\n".join(nonempty).lower()
    for marker in _DONE_MARKERS:
        if marker in tail_block:
            return State.DONE, f"tail contains completion marker {marker!r}"

    # Check stall signals on the last non-empty line
    if last_line.endswith(":"):
        return (
            State.LIKELY_STALLED_INCOMPLETE,
            f"last line ends with ':' (mid-task continuation): {last_line[:80]!r}",
        )

    for prefix in _CONTINUATION_PREFIXES:
        if last_lower.startswith(prefix):
            return (
                State.LIKELY_STALLED_INCOMPLETE,
                f"last line starts with continuation phrase {prefix!r}: {last_line[:80]!r}",
            )

    # No done marker and no stall signal — still classify as stalled because
    # the file is stale and lacks a result.
    return (
        State.LIKELY_STALLED_INCOMPLETE,
        f"no result/summary marker found in tail; last line: {last_line[:80]!r}",
    )


# ── file scanning ─────────────────────────────────────────────────────────────


def _classify_file(
    path: Path,
    window_seconds: float,
    now: float,
) -> tuple[str, State, str]:
    """Return (task_id, state, reason) for a single output file."""
    task_id = path.stem
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return task_id, State.LIKELY_STALLED_INCOMPLETE, "could not stat file"

    age = now - mtime

    try:
        tail_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return task_id, State.LIKELY_STALLED_INCOMPLETE, "could not read file"

    state, reason = classify_tail(tail_text, age, window_seconds)
    return task_id, state, reason


def scan_tasks_dir(
    tasks_dir: Path,
    window_seconds: float = DEFAULT_WINDOW_SECS,
) -> list[tuple[str, State, str]]:
    """Return a list of (task_id, state, reason) for every .output file.

    Missing or non-directory *tasks_dir* → returns empty list (fail-safe).
    """
    if not tasks_dir.is_dir():
        return []

    now = time.time()
    results: list[tuple[str, State, str]] = []
    for path in sorted(tasks_dir.glob("*.output")):
        if not path.is_file():
            continue
        results.append(_classify_file(path, window_seconds, now))
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────


def _resolve_tasks_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("GLUDD_TASKS_DIR")
    if env:
        return Path(env)
    return Path("tasks")


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="Classify background agent output files as ACTIVE / LIKELY_STALLED_INCOMPLETE / DONE.",
    )
    parser.add_argument(
        "tasks_dir",
        nargs="?",
        default=None,
        help="Directory containing <task-id>.output files (default: GLUDD_TASKS_DIR or ./tasks)",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=float(os.environ.get("GLUDD_WATCHDOG_WINDOW_SECS", DEFAULT_WINDOW_SECS)),
        help=f"Recency window in seconds (default: {DEFAULT_WINDOW_SECS})",
    )
    parser.add_argument(
        "--list-stalled",
        action="store_true",
        help="Print task-ids and one-line reason for LIKELY_STALLED_INCOMPLETE agents",
    )
    parser.add_argument(
        "--count-stalled",
        action="store_true",
        help="Print the count of LIKELY_STALLED_INCOMPLETE agents",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print all agents with their state (default: silent unless --list-stalled/--count-stalled)",
    )
    args = parser.parse_args(argv)

    tasks_dir = _resolve_tasks_dir(args.tasks_dir)
    results = scan_tasks_dir(tasks_dir, window_seconds=args.window)

    stalled = [(tid, reason) for tid, state, reason in results if state == State.LIKELY_STALLED_INCOMPLETE]

    if args.list_stalled:
        for tid, reason in stalled:
            print(f"{tid}\t{reason}")

    if args.count_stalled:
        print(len(stalled))

    if args.all:
        for tid, state, reason in results:
            print(f"{state.value:<28} {tid}\t{reason}")

    if not (args.list_stalled or args.count_stalled or args.all):
        # Default: print a summary to stderr so callers can detect issues
        active = sum(1 for _, s, _ in results if s == State.ACTIVE)
        done = sum(1 for _, s, _ in results if s == State.DONE)
        print(
            f"agents: {len(results)} total  "
            f"active={active}  done={done}  stalled={len(stalled)}",
            file=sys.stderr,
        )
        if stalled:
            for tid, reason in stalled:
                print(f"  STALLED {tid}: {reason}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
