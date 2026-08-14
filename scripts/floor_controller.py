#!/usr/bin/env python3
"""Floor controller — single integration point that composes the three floor tools.

PROBLEM BEING SOLVED:
    floor_planner, agent_liveness, and agent_watchdog each exist as tested, pure-ish
    utilities, but nothing combined them into ONE decision that an orchestrator could
    act on without custom glue code per caller.  The result was that the
    dispatch/repoke/kill logic was "behavioral" — a human eyeballed three separate
    outputs and decided.  This module is that composition layer.

WHAT ``decide`` DOES:
    1. Calls ``floor_planner.plan`` for the *dispatch count*: how many new agents
       should be launched right now (pre-drain-aware, ceiling-clamped).
    2. Iterates every inflight agent, calls ``agent_watchdog.classify_tail`` on its
       tail text + age, and routes it to one of three buckets:
         - repoke_ids : LIKELY_STALLED_INCOMPLETE — agent is alive but stuck; poke it
         - kill_ids   : DONE but not reaped (idling); safe to reap / untrack
         - (neither)  : ACTIVE — leave it alone
    3. Returns a plain dict so the orchestrator can JSON-serialise it, log it, or
       pattern-match on it without importing this module.

PURE / DETERMINISTIC:
    ``decide`` has NO side effects: it does not spawn processes, write files, or read
    the filesystem.  All inputs come in via parameters (counts, ages, tail texts) so
    the function is fully unit-testable with synthetic data.

CLI (``make floor-plan``):
    Reads a JSON blob from stdin or a file (via the STATE= Makefile variable) of the
    form::

        {
          "live": 4,
          "inflight": [
            {"id": "agent-abc", "age_seconds": 300, "tail": "Let me continue..."},
            {"id": "agent-def", "age_seconds": 30,  "tail": "result: done"}
          ],
          "floor":   6,
          "target":  10,
          "ceiling": 12
        }

    and prints the decision as JSON.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

# ---------------------------------------------------------------------------
# Dynamic import of sibling scripts so this module works both as a script
# (python scripts/floor_controller.py) AND when imported from the tests dir
# without the scripts/ directory on sys.path.
# ---------------------------------------------------------------------------

_SCRIPTS = Path(__file__).resolve().parent


def _load(name: str) -> ModuleType:
    module_name = f"_gludd_script_{name}"
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        module_name, _SCRIPTS / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot find scripts/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    # Python 3.14's dataclass annotation resolver (and Ansible's wrapper) looks
    # the class module up in sys.modules while exec_module is still running.
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return mod


_floor_planner = _load("floor_planner")
_agent_watchdog = _load("agent_watchdog")

# Re-export the enums/types callers may want.
State = _agent_watchdog.State
plan = _floor_planner.plan
classify_tail = _agent_watchdog.classify_tail


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def decide(
    live_agents: int,
    inflight: list[dict[str, Any]],
    floor: int = 6,
    target: int = 10,
    ceiling: int = 12,
    watchdog_window: float = _agent_watchdog.DEFAULT_WINDOW_SECS,
    gate_running: bool = False,
    writer_cap_during_gate: int = 0,
    expected_completions_soon: int = 0,
    buffer: int = 2,
) -> dict[str, Any]:
    """Produce a composite orchestration plan.

    Parameters
    ----------
    live_agents:
        Current ground-truth live count (e.g. from ``agent_liveness.live_count``).
    inflight:
        A list of agent descriptors, each a dict with at least:
          - ``"id"``          : str  — opaque agent identifier
          - ``"age_seconds"`` : float — seconds since last transcript write
          - ``"tail"``        : str  — last portion of the agent's output transcript
    floor:
        Minimum concurrency to hold.
    target:
        Desired steady-state concurrency.
    ceiling:
        Hard maximum concurrency (never exceeded post-dispatch).
    watchdog_window:
        Passed to ``classify_tail``; files modified within this many seconds are
        ACTIVE (not stalled).
    gate_running:
        When True, a CI/test gate is currently executing.  Heavy-writer worktree
        dispatch is capped to ``writer_cap_during_gate`` (default 0 extra writers),
        but READ-ONLY dispatch toward FLOOR is UNAFFECTED — the orchestrator MUST
        still reach the floor using read-only agents.  This encodes the rule:
        "a running gate does NOT lower the read-only floor."
        When False (default), behaviour is identical to the pre-gate logic.
    writer_cap_during_gate:
        Maximum NEW heavy-writer dispatches allowed while a gate is running.
        Default 0 (no new worktree-writers during a gate).  Ignored when
        ``gate_running=False``.
    expected_completions_soon:
        Number of currently-live agents expected to complete before the next
        planning tick.  When > 0, the controller pre-empts the anticipated dip:
        if ``live_agents - expected_completions_soon < floor``, it dispatches
        replacements NOW rather than waiting for the floor to be breached.
        Default 0 (backward-compatible: behaves identically to the pre-predictive
        logic).
    buffer:
        How far above the floor to aim when computing the dispatch target.
        ``aim = max(floor + buffer, target)``.  Default 2.

    Returns
    -------
    A dict::

        {
          "dispatch_n":                int,        # how many new agents to launch
          "repoke_ids":                [str, ...], # ids of stalled-but-incomplete agents to repoke
          "kill_ids":                  [str, ...], # ids of DONE/idle agents safe to reap
          "reason":                    str,        # human-readable summary from floor_planner
          "planner":                   {...},      # full floor_planner.plan output (for logging)
          "read_only_only":            bool,       # True when gate_running forces RO-only dispatch
          "gate_running":              bool,       # echoes the gate_running input for logging
          "expected_completions_soon": int,        # echoes input for logging
          "buffer":                    int,        # echoes input for logging
        }
    """
    # --- 1. Classify every inflight agent -----------------------------------
    repoke_ids: list[str] = []
    kill_ids: list[str] = []

    for agent in inflight:
        agent_id: str = str(agent.get("id", "unknown"))
        age: float = float(agent.get("age_seconds", 0.0))
        tail: str = str(agent.get("tail", ""))

        state, _reason = classify_tail(tail, age, watchdog_window)

        if state == State.LIKELY_STALLED_INCOMPLETE:
            repoke_ids.append(agent_id)
        elif state == State.DONE:
            kill_ids.append(agent_id)
        # ACTIVE -> no action

    # --- 2. Ask the planner for a dispatch count ----------------------------
    # Pass the ages of ACTIVE inflight agents to the drain-lookahead logic.
    # Agents already going into repoke/kill are counted as still "live" by the
    # planner (they haven't been reaped yet), so their ages feed the drain
    # predictor correctly.
    inflight_ages: list[float] = [
        float(a.get("age_seconds", 0.0)) for a in inflight
    ]

    # Incorporate the caller-supplied buffer into the planner's aim target so
    # ``aim = max(floor + buffer, target)``.  The planner already has its own
    # internal BUFFER constant but that is process-global; the caller's buffer
    # parameter allows per-call tuning without env-var side effects.
    effective_target = max(floor + buffer, target)

    # --- 2a. Predictive pre-emption -----------------------------------------
    # If ``expected_completions_soon > 0`` completions are imminent, pre-empt
    # the dip: use the ANTICIPATED live count as the effective live count when
    # it would drop below the aim (floor + buffer).  This dispatches
    # replacements NOW rather than waiting for the floor breach to appear in
    # the next tick.  Backward-compatible default (0) leaves the planner
    # call unchanged.
    effective_live = live_agents
    if expected_completions_soon > 0:
        anticipated_live = max(0, live_agents - expected_completions_soon)
        if anticipated_live < effective_target:
            effective_live = anticipated_live

    planner_result = plan(
        live=effective_live,
        floor=floor,
        target=effective_target,
        ceiling=ceiling,
        inflight_ages=inflight_ages,
    )

    # Clamp dispatch to actual ceiling headroom (not effective headroom) so
    # we never exceed ``ceiling - live_agents`` regardless of effective_live.
    dispatch_n: int = min(
        planner_result["dispatch_now"],
        max(0, ceiling - live_agents),
    )

    # --- 3. Apply gate-safe floor rule -------------------------------------
    # When a gate is running:
    #   - Dispatch toward FLOOR is read-only-only (no new heavy worktree-writers).
    #   - New heavy-writer dispatch beyond writer_cap_during_gate is suppressed.
    # BUT: we never suppress read-only dispatch toward the floor — the count
    # stays >= 1 as long as live < floor.  Ceiling still wins unconditionally.
    read_only_only: bool = False
    reason: str = planner_result["reason"]

    if gate_running and dispatch_n > 0:
        read_only_only = True
        reason = (
            f"{reason} [gate-safe: gate running — dispatch is READ-ONLY agents only; "
            f"heavy-writer cap={writer_cap_during_gate}]"
        )

    return {
        "dispatch_n": dispatch_n,
        "repoke_ids": repoke_ids,
        "kill_ids": kill_ids,
        "reason": reason,
        "planner": planner_result,
        "read_only_only": read_only_only,
        "gate_running": gate_running,
        "expected_completions_soon": expected_completions_soon,
        "buffer": buffer,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_state(argv: list[str]) -> dict[str, Any]:
    """Load state JSON from a file path arg or stdin."""
    if argv and not argv[0].startswith("-"):
        with open(argv[0]) as fh:
            return json.load(fh)  # type: ignore[no-any-return]
    raw = sys.stdin.read()
    return json.loads(raw)  # type: ignore[no-any-return]


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    try:
        state = _load_state(argv)
    except (json.JSONDecodeError, OSError, IndexError) as exc:
        print(
            json.dumps({"error": str(exc), "dispatch_n": 0, "repoke_ids": [], "kill_ids": [], "reason": "input error"}),
            file=sys.stdout,
        )
        return 1

    result = decide(
        live_agents=int(state.get("live", 0)),
        inflight=list(state.get("inflight", [])),
        floor=int(state.get("floor", 6)),
        target=int(state.get("target", 10)),
        ceiling=int(state.get("ceiling", 12)),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
