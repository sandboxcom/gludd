#!/usr/bin/env python3
"""Floor planner — decide how many subagents to dispatch NOW to keep a steady
concurrency floor without overshooting a ceiling.

WHY A PLANNER (not "dispatch until live == floor"):
    A naive "top up to the floor" rule oscillates. Agents complete in bursts; if
    we only ever refill exactly to the floor, the next wave of completions drops
    us below it before the next planning tick, and we never actually HOLD the
    floor — we bounce under it. So the planner dispatches to a small BUFFER ABOVE
    the floor: normal near-term completions then land us back AT the floor rather
    than under it.

PREDICTED DRAIN (lookahead):
    An agent that has been in flight longer than a drain threshold is likely to
    finish before the next planning tick. Those agents are still "live" right now,
    but counting on them to stay live would leave us short. So for each agent
    older than the threshold we treat one unit of current liveness as already
    gone, and pre-dispatch a replacement to cover it. This keeps the EFFECTIVE
    (post-drain) live count at the buffer.

CEILING:
    We never let the post-dispatch live count exceed the ceiling. dispatch_now is
    clamped so ``live + dispatch_now <= ceiling``; at or over the ceiling we
    dispatch 0 regardless of buffer/drain.

The core (:func:`plan`) is a pure function — no I/O, fully unit-testable. The CLI
wrapper reads numbers from argv/env and prints the decision as JSON.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

# An agent older than this (seconds) is predicted to complete before the next
# planning tick, so we pre-dispatch a replacement to cover its imminent drain.
DRAIN_AGE_SECS = float(os.environ.get("FLOOR_DRAIN_AGE_SECS", "240.0"))
# How far above the floor to aim, so normal completions land us AT the floor,
# not under it. Clamped to the ceiling.
BUFFER = int(os.environ.get("FLOOR_BUFFER", "2"))


def plan(
    live: int,
    floor: int,
    target: int,
    ceiling: int,
    inflight_ages: list[float] | None = None,
    drain_age: float = DRAIN_AGE_SECS,
    buffer: int = BUFFER,
) -> dict[str, Any]:
    """Compute a deployment decision. Pure function — no I/O.

    Args:
        live: current ground-truth live-subagent count.
        floor: minimum concurrency to hold.
        target: desired steady-state concurrency.
        ceiling: hard maximum concurrency (never exceeded).
        inflight_ages: ages (seconds) of currently-live agents; those older than
            ``drain_age`` are predicted to drain soon and are pre-covered.
        drain_age: age threshold for the predicted-drain lookahead.
        buffer: how far above the floor to aim (clamped into [floor, ceiling]).

    Returns:
        ``{live, floor, target, ceiling, dispatch_now, reason}``.
    """
    # --- Normalize inputs so a caller (or a fail-safe 0 from the liveness probe)
    # can't produce a nonsensical plan. ----------------------------------------
    live = max(0, int(live))
    floor = max(0, int(floor))
    ceiling = max(0, int(ceiling))
    # Keep floor/target ordered under the ceiling: floor <= target <= ceiling.
    target = max(floor, min(int(target), ceiling))
    floor = min(floor, ceiling)
    buffer = max(0, int(buffer))

    ages = inflight_ages or []
    draining = sum(1 for a in ages if a is not None and float(a) >= drain_age)

    # Aim point: the floor plus a buffer, but never past the target band's top
    # (the buffer is a smoothing margin, not a reason to exceed target), and
    # never past the ceiling.
    aim = min(max(floor + buffer, floor), ceiling)
    aim = max(aim, target)  # target is the desired steady state; honor it.
    aim = min(aim, ceiling)

    # Effective live count once the predicted-drain agents complete.
    effective_live = max(0, live - draining)

    # Headroom under the ceiling is an absolute cap on what we may dispatch now.
    headroom = max(0, ceiling - live)

    if live >= ceiling:
        return _decision(
            live, floor, target, ceiling, 0,
            "at/over ceiling (%d>=%d): hold, dispatch 0" % (live, ceiling),
        )

    need = aim - effective_live
    if need <= 0:
        if draining:
            reason = (
                "in band (effective %d after %d predicted-drain >= aim %d): "
                "dispatch 0" % (effective_live, draining, aim)
            )
        else:
            reason = "in band (live %d >= aim %d): dispatch 0" % (live, aim)
        return _decision(live, floor, target, ceiling, 0, reason)

    dispatch = min(need, headroom)
    if effective_live < floor:
        reason = (
            "below floor (effective %d < floor %d): dispatch %d toward aim %d"
            % (effective_live, floor, dispatch, aim)
        )
        if draining:
            reason += " (incl %d for predicted drain)" % draining
    else:
        reason = (
            "topping up to buffered aim %d (effective %d): dispatch %d"
            % (aim, effective_live, dispatch)
        )
        if draining:
            reason += " (incl %d for predicted drain)" % draining
    if dispatch < need:
        reason += "; clamped by ceiling headroom %d" % headroom
    return _decision(live, floor, target, ceiling, dispatch, reason)


def _decision(
    live: int, floor: int, target: int, ceiling: int, dispatch_now: int, reason: str
) -> dict[str, Any]:
    return {
        "live": live,
        "floor": floor,
        "target": target,
        "ceiling": ceiling,
        "dispatch_now": int(dispatch_now),
        "reason": reason,
    }


def _int_arg(argv: list[str], flag: str, default: int) -> int:
    if flag in argv:
        try:
            return int(argv[argv.index(flag) + 1])
        except (IndexError, ValueError):
            return default
    return default


def _ages_arg(argv: list[str]) -> list[float]:
    if "--ages" not in argv:
        return []
    try:
        raw = argv[argv.index("--ages") + 1]
    except IndexError:
        return []
    out: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(float(part))
        except ValueError:
            pass
    return out


def main(argv: list[str]) -> int:
    """Thin CLI wrapper. Flags: --live --floor --target --ceiling --ages a,b,c."""
    live = _int_arg(argv, "--live", 0)
    floor = _int_arg(argv, "--floor", int(os.environ.get("FLOOR_MIN", "6")))
    target = _int_arg(argv, "--target", int(os.environ.get("FLOOR_TARGET", "10")))
    ceiling = _int_arg(argv, "--ceiling", int(os.environ.get("FLOOR_CEILING", "12")))
    ages = _ages_arg(argv)
    try:
        decision = plan(live, floor, target, ceiling, ages)
    except Exception as exc:  # noqa: BLE001 — CLI must fail safe, never traceback.
        decision = _decision(
            max(0, live), floor, target, ceiling, 0,
            "error computing plan (%s): fail-safe dispatch 0" % type(exc).__name__,
        )
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
