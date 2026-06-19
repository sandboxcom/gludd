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

    Solution: eliminate the probe sleep entirely. Use a SINGLE fixed wall-clock
    window (``GLUDD_LIVENESS_WINDOW_SEC``, default 120 s) evaluated identically
    at every call site. A transcript is LIVE if its mtime falls within that
    window. No sleep → no sampling variance → two consecutive calls at the same
    instant return the same count. The window is wide enough to cover a full LLM
    think-cycle (30-90 s), so a live-but-quiet agent is not under-counted.

BIAS (floor STABILITY):
    A completed agent's transcript decays out of the window after
    GLUDD_LIVENESS_WINDOW_SEC seconds — the same smoothing as the old tail term,
    but without the probe noise. This trades a small breach-detection delay for a
    steady, deterministic floor signal.

Format-independent and hook-independent: depends only on filesystem mtimes.
Fail-safe by construction — any error yields 0 / exit 0 (callers treat 0 as
"could not determine, dispatch toward the floor" rather than wedging).

TESTABILITY:
    The transcript dir is configurable via GLUDD_TASKS_DIR so the counter can be
    unit-tested against a temp dir. FLOOR_LIVE_OVERRIDE is a test seam that skips
    probing entirely (print the override and exit). GLUDD_LIVENESS_WINDOW_SEC is
    env-tunable so tests can pass a short window without patching module globals.
"""
from __future__ import annotations

import glob
import os
import sys
import time

# Single fixed window constant — every call reads this identically, eliminating
# inter-call variance caused by the old probe-sleep approach.
LIVENESS_WINDOW_SEC = float(os.environ.get("GLUDD_LIVENESS_WINDOW_SEC", "120.0"))


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


def live_count(
    window: float = LIVENESS_WINDOW_SEC,
) -> tuple[int, int, str | None]:
    """Return ``(live, total, tasks_dir)``.

    live  = transcripts whose mtime falls within the last ``window`` seconds.
            Uses a single ``time.time()`` snapshot so every transcript is
            evaluated against the SAME clock value — no probe sleep, no drift.
    total = all ``*.output`` transcripts in the resolved tasks dir.
    tasks = the resolved tasks dir (or ``None`` if unresolved).

    A completed (frozen) transcript's mtime does not advance, so it decays out
    of the window after ``window`` seconds and stops being counted as live.
    """
    tasks = _tasks_dir()
    fs = glob.glob(tasks + "/*.output") if tasks else []

    # Single clock snapshot — all files evaluated against the same ``now``.
    now = time.time()
    cutoff = now - window

    live = 0
    total = 0
    for f in fs:
        try:
            mtime = os.path.getmtime(f)
        except OSError:
            # File vanished (agent dir cleaned up): skip it.
            continue
        total += 1
        if mtime >= cutoff:
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
            "[liveness] live subagents (mtime within %.0fs window): "
            "%d live  of %d total transcripts  (dir=%s)"
            % (LIVENESS_WINDOW_SEC, live, total, tasks)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
