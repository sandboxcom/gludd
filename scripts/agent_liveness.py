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

ROBUST SIGNAL:
    "Is this transcript being appended RIGHT NOW?" not "was it touched recently?".
    We sample every transcript's mtime, sleep a short PROBE window, then re-sample.
    A file is LIVE only if it GREW during the probe (the agent is still streaming
    tool calls / tokens), UNION a small recent-write tail so an agent that just
    emitted is not missed between writes. A COMPLETED agent's transcript is frozen,
    so it can never grow during the probe -> never false-counted.

BIAS (floor STABILITY, not just breach-safety):
    A tail SHORTER than one LLM think-cycle undercounts a fleet that is all
    mid-think (an agent blocked in a `make`/test or waiting on the model is frozen
    for 30-90s with no transcript write) -> a FALSE floor breach -> a dispatch
    burst -> the count spikes -> the burst finishes together -> the count craters
    below the floor. That 1<->21 oscillation is exactly the "floor not held"
    symptom. So the tail (default 75s) covers a full think-cycle: a live-but-quiet
    agent is not undercounted, and a just-completed agent's final write decays out
    of the window gradually rather than instantly, damping the crater. The
    "grew during probe" signal remains the definitely-live core; the tail is only
    the smoothing term. Trade-off: a genuinely-drained fleet is detected ~TAIL_SECS
    later — a deliberate exchange of a small breach-detection delay for a steady
    floor.

Format-independent and hook-independent: depends only on filesystem mtimes.
Fail-safe by construction — any error yields 0 / exit 0 (callers treat 0 as
"could not determine, dispatch toward the floor" rather than wedging).

TESTABILITY:
    The transcript dir is configurable via GLUDD_TASKS_DIR so the counter can be
    unit-tested against a temp dir. FLOOR_LIVE_OVERRIDE is a test seam that skips
    probing entirely (print the override and exit). PROBE/TAIL are env-tunable.
"""
from __future__ import annotations

import glob
import os
import sys
import time

PROBE_SECS = float(os.environ.get("FLOOR_PROBE_SECS", "0.6"))
# TAIL must cover a full LLM think-cycle (~30-90s) so a live agent waiting on the
# model is not undercounted as dead — the root cause of the floor oscillation.
TAIL_SECS = float(os.environ.get("FLOOR_TAIL_SECS", "75.0"))


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
    probe: float = PROBE_SECS, tail: float = TAIL_SECS
) -> tuple[int, int, str | None]:
    """Return ``(live, total, tasks_dir)``.

    live  = transcripts that GREW during the probe window OR were written within
            the last ``tail`` seconds (actively-streaming subagents).
    total = all ``*.output`` transcripts in the resolved tasks dir.
    tasks = the resolved tasks dir (or ``None`` if unresolved).

    A frozen (completed) transcript can never grow during the probe, so it is
    only ever counted via the tail term and decays out after ``tail`` seconds.
    """
    tasks = _tasks_dir()
    fs = glob.glob(tasks + "/*.output") if tasks else []

    def snap() -> dict[str, float]:
        m: dict[str, float] = {}
        for f in fs:
            try:
                m[f] = os.path.getmtime(f)
            except OSError:
                # File vanished mid-probe (agent dir cleaned up): treat as absent.
                pass
        return m

    m1 = snap()
    time.sleep(probe)
    m2 = snap()
    now = time.time()
    live = 0
    for f in fs:
        if f not in m2:
            continue
        grew = m2[f] > m1.get(f, 0.0)
        recent = (now - m2[f]) < tail
        if grew or recent:
            live += 1
    return live, len(fs), tasks


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
            "[liveness] actively-streaming subagents "
            "(transcript grew during %.1fs probe, or written <%.0fs ago): "
            "%d live  of %d total transcripts  (dir=%s)"
            % (PROBE_SECS, TAIL_SECS, live, total, tasks)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
