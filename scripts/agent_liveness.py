#!/usr/bin/env python3
"""Ground-truth live-subagent counter — the single source of truth shared by the
orchestrator's floor planner (scripts/floor_planner.py) and the agent-floor Stop
hook, so both agree on how many subagents are actually running RIGHT NOW.

WHY THIS EXISTS (the bug it fixes):
    The original heuristic counted every task .output transcript whose mtime was
    within the last ~90s. But a subagent gets a FINAL write to its transcript at
    the moment it COMPLETES — so a burst of completions all landed inside that
    window and were counted as "live". The orchestrator was told 11 agents were
    running when only 3 were. A counter that OVER-counts is worse than none: it
    hides a floor breach (reports "healthy" while the floor is actually breached)
    — the precise failure the agent-floor guardrail exists to prevent.

DETERMINISM FIX (the wobble bug this revision addresses):
    The prior implementation sampled mtime before and after a PROBE sleep, so two
    rapid back-to-back ``--count`` calls could return 6 / 13 / 18 / 21 depending
    on whether the sleep straddles an active write. Every hook that calls
    ``--count`` got a slightly different wall-clock slice, making the floor signal
    incoherent.

    Solution: eliminate the probe sleep entirely. Liveness is determined SOLELY
    by terminal-detection (see below). No sleep → no sampling variance → two
    consecutive calls at the same instant return the same count.

UNDERCOUNT FIX (the idle-agent bug this revision addresses):
    The prior dual-filter required BOTH a fresh mtime AND no terminal marker.
    An alive-but-idle agent (waiting on a long LLM call, no writes for >25s)
    failed the mtime gate and was silently dropped from the live count even though
    it had no terminal marker. An agent that is RUNNING but QUIET was being
    reported as not running — an under-count that hides a floor breach in the
    opposite direction.

TERMINAL-DETECTION ONLY (current approach):
    A transcript is counted LIVE iff it does NOT end with a terminal result marker:

      TERMINAL DETECTION: read the last non-empty line of each ``.output`` JSONL
      file. If it is valid JSON whose ``type`` field equals ``"result"`` OR whose
      ``subtype`` field equals ``"result"``, the agent has completed — exclude it
      from the live count. Fail-open: if the last line cannot be parsed or the
      file is empty, assume the agent is still running (never under-count a live
      agent).

    The mtime / window gate has been removed entirely. An alive-but-idle agent is
    correctly counted live regardless of how long it has been quiet.

Format-independent and hook-independent: depends only on last-line JSONL content.
Fail-safe by construction — any error yields 0 / exit 0 (callers treat 0 as
"could not determine, dispatch toward the floor" rather than wedging).

TESTABILITY:
    The transcript dir is configurable via GLUDD_TASKS_DIR so the counter can be
    unit-tested against a temp dir. FLOOR_LIVE_OVERRIDE is a test seam that skips
    probing entirely (print the override and exit). GLUDD_LIVENESS_WINDOW_SEC is
    accepted for backward-compatibility but is no longer used in liveness logic.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

# Single fixed window constant — every call reads this identically, eliminating
# inter-call variance caused by the old probe-sleep approach. Default 25 s: a
# live agent streams output every few seconds so 25 s catches it; a completed
# agent whose terminal marker is unparseable decays out quickly.
LIVENESS_WINDOW_SEC = float(os.environ.get("GLUDD_LIVENESS_WINDOW_SEC", "25.0"))


def _tasks_dir() -> str | None:
    """Resolve the transcript dir holding per-agent ``*.output`` files.

    Resolution order:
      1. ``GLUDD_TASKS_DIR`` env override (used by tests against a temp dir).
      2. The newest claude session's ``tasks/`` dir for this repo+uid.

    Returns the directory path, or ``None`` if none can be resolved.
    """
    override = os.environ.get("GLUDD_TASKS_DIR")
    if override:
        return override if os.path.isdir(override) else None

    base = "/private/tmp/claude-%d/-Users-shawnwilson-gludd" % os.getuid()
    sessions = sorted(glob.glob(base + "/*/"), key=os.path.getmtime, reverse=True)
    return next((s + "tasks" for s in sessions if os.path.isdir(s + "tasks")), None)


def _is_terminal(path: str) -> bool:
    """Return True if the transcript at ``path`` ends with a completion marker.

    Reads the last ~512 bytes of the file to find the last non-empty line, then
    tries to parse it as JSON. Returns True (completed) if:
      - ``type == "result"``  (direct result object), OR
      - ``subtype == "result"``  (system envelope around a result)

    Returns False on any exception (fail-open: assume still running if we cannot
    determine the terminal state). An empty file also returns False (agent just
    started, no content yet).
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size == 0:
                return False
            read_bytes = min(512, size)
            fh.seek(-read_bytes, 2)
            tail = fh.read(read_bytes)
        lines = tail.split(b"\n")
        # Walk backwards to find last non-empty line.
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped.decode("utf-8", errors="replace"))
            if not isinstance(obj, dict):
                return False
            if obj.get("type") == "result":
                return True
            if obj.get("subtype") == "result":
                return True
            return False
        return False
    except Exception:
        return False


def live_count(
    window: float = LIVENESS_WINDOW_SEC,
) -> tuple[int, int, str | None]:
    """Return ``(live, total, tasks_dir)``.

    live  = transcripts whose last line does NOT indicate a terminal result.
            Liveness is determined SOLELY by terminal-detection; the ``window``
            parameter is accepted for backward-compatibility but is not used.
    total = all ``*.output`` transcripts in the resolved tasks dir.
    tasks = the resolved tasks dir (or ``None`` if unresolved).

    A transcript is live iff:
        NOT _is_terminal(path)  (no terminal result marker on last line)

    The mtime gate has been removed. An alive-but-idle agent (no writes for a
    long time while waiting on an LLM call) is correctly counted live regardless
    of how long it has been quiet.

    Terminal detection is fail-open: an unparseable last line is treated as
    non-terminal (agent assumed running). An empty file is also treated as live
    (agent just started).
    """
    tasks = _tasks_dir()
    fs = glob.glob(tasks + "/*.output") if tasks else []

    live = 0
    total = 0
    for f in fs:
        try:
            os.path.getmtime(f)
        except OSError:
            # File vanished (agent dir cleaned up): skip it.
            continue
        total += 1
        if not _is_terminal(f):
            live += 1

    return live, total, tasks


def main(argv: list[str]) -> int:
    # TEST-ONLY seam: if FLOOR_LIVE_OVERRIDE is an all-digit string, skip the
    # filesystem probe entirely and print that integer directly. Makes callers
    # deterministically testable without real task-transcript files. NEVER set
    # this in production; it is exclusively for unit/integration tests.
    override = os.environ.get("FLOOR_LIVE_OVERRIDE", "")
    if override and override.isdigit():
        if "--count" in argv:
            print(int(override))
        else:
            print("[liveness] FLOOR_LIVE_OVERRIDE=%s (test seam active)" % override)
        return 0

    try:
        live, total, tasks = live_count()
    except Exception:
        # Fail safe: emit 0 so callers err toward dispatching, never wedge.
        if "--count" in argv:
            print(0)
        else:
            print("[liveness] ERROR determining live agents (failing safe -> 0)")
        return 0

    if "--count" in argv:
        print(live)
    else:
        print(
            "[liveness] live subagents (terminal-detection only): "
            "%d live  of %d total transcripts  (dir=%s)"
            % (live, total, tasks)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
