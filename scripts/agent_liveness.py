#!/usr/bin/env python3
"""Ground-truth live-subagent counter — shared by `make floor-status` and the
agent-floor Stop hook (.claude/hooks/agent_floor_stop.sh) so both agree.

WHY THIS EXISTS (the bug it fixes):
    The old heuristic counted every task .output transcript whose mtime was
    within the last 90s. But a subagent gets a FINAL write to its transcript at
    the moment it COMPLETES — so a burst of completions all landed inside that
    90s window and were counted as "live". The orchestrator was told 11 agents
    were running when only 3 were. A counter that OVER-counts is worse than none:
    it hides a floor breach (reports "healthy" while the floor is actually
    breached) — the precise failure the agent-floor guardrail exists to prevent.

ROBUST SIGNAL:
    "Is this transcript being appended RIGHT NOW?" not "was it touched recently?".
    We sample every transcript's mtime, sleep a short PROBE window, then re-sample.
    A file is LIVE only if it GREW during the probe (the agent is still
    streaming tool calls / tokens), UNION a small recent-write tail so an agent
    that just emitted is not missed between writes. A COMPLETED agent's transcript
    is frozen, so it can never grow during the probe -> never false-counted.

BIAS (re-tuned 2026-06-17 for floor STABILITY, not just breach-safety):
    The original 12s tail was SHORTER than one LLM think-cycle (an agent blocked
    in a `make`/test or simply waiting on the model is frozen for 30-90s with no
    transcript write). So a fleet of agents that were all mid-think read as ~0
    live -> a FALSE floor breach -> the orchestrator dispatched a burst -> the
    count spiked (we saw 21) -> the burst finished together -> the count CRATERED
    below the floor. That oscillation (1<->21) is exactly the "floor not held at
    6" symptom. Fix: the tail now covers a full think-cycle, so (a) a live-but-
    quiet agent is no longer undercounted, and (b) a just-completed agent's final
    write decays out of the window GRADUALLY rather than instantly, damping the
    crater that triggered the next burst. Trade-off: a genuinely-drained fleet is
    detected ~TAIL_SECS later than before — an acceptable, deliberate exchange of
    a small breach-detection delay for the steady floor the operator asked for.
    The "grew during probe" signal remains the definitely-live core; the tail is
    only the smoothing term.

Format-independent and hook-independent: depends only on filesystem mtimes.
Fail-open by construction — any error yields a 0/None that callers treat as
"could not determine" (the Stop hook then allows the turn to end).
"""
from __future__ import annotations

import glob
import os
import sys
import time

PROBE_SECS = float(os.environ.get("FLOOR_PROBE_SECS", "2.5"))
# TAIL must cover a full LLM think-cycle (~30-90s) so a live agent waiting on the
# model is not undercounted as dead — the root cause of the 1<->21 floor
# oscillation. 75s is comfortably above typical latency while still aging out a
# truly-drained fleet within ~1 tick.
TAIL_SECS = float(os.environ.get("FLOOR_TAIL_SECS", "75.0"))


def _tasks_dir() -> str | None:
    """The newest claude session's tasks/ dir for this repo+uid (or None)."""
    base = "/private/tmp/claude-%d/-Users-shawnwilson-gludd" % os.getuid()
    sessions = sorted(glob.glob(base + "/*/"), key=os.path.getmtime, reverse=True)
    return next((s + "tasks" for s in sessions if os.path.isdir(s + "tasks")), None)


def live_count(probe: float = PROBE_SECS, tail: float = TAIL_SECS):
    """Return (live, total, tasks_dir).

    live  = transcripts that GREW during the probe window OR were written within
            the last `tail` seconds (actively streaming subagents).
    total = all task transcripts in the newest session dir.
    """
    tasks = _tasks_dir()
    fs = glob.glob(tasks + "/*.output") if tasks else []

    def snap() -> dict[str, float]:
        m: dict[str, float] = {}
        for f in fs:
            try:
                m[f] = os.path.getmtime(f)
            except OSError:
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
    # TEST-ONLY seam: if FLOOR_LIVE_OVERRIDE is set to an all-digit string, skip
    # the filesystem probe entirely and print that integer directly.  This makes
    # the hooks deterministically testable without real task-transcript files.
    # NEVER set this in production; it is exclusively for unit/integration tests.
    _override = os.environ.get("FLOOR_LIVE_OVERRIDE", "")
    if _override and _override.isdigit():
        if "--count" in argv:
            print(int(_override))
        else:
            print("[liveness] FLOOR_LIVE_OVERRIDE=%s (test seam active)" % _override)
        return 0

    try:
        live, total, tasks = live_count()
    except Exception:
        # Fail open: emit 0 so the Stop hook errs toward dispatching, never wedges.
        if "--count" in argv:
            print(0)
        else:
            print("[liveness] ERROR determining live agents (failing open -> 0)")
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
