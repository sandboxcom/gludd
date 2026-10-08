"""Pure TLC counterexample parsing for the formal collection."""

from __future__ import annotations

import re
from typing import Any

_INVARIANT_RE = re.compile(r"Error: Invariant (\w+) is violated")
_STATE_RE = re.compile(r"^State (\d+):\s*(.*)")
_VARIABLE_RE = re.compile(r"^/\\ (\w+) = (.*)")


def parse_tlc_trace(raw: str) -> dict[str, Any]:
    """Parse a bounded TLC trace into stable, collection-owned JSON data."""
    invariant_match = _INVARIANT_RE.search(raw)
    invariant = invariant_match.group(1) if invariant_match else "UnknownInvariant"
    marker = "The behavior up to this point is:"
    trace_section = raw[raw.find(marker) :] if marker in raw else ""
    steps: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in trace_section.splitlines():
        state_match = _STATE_RE.match(line)
        if state_match:
            if current is not None:
                steps.append(current)
            current = {
                "state_n": int(state_match.group(1)),
                "label": state_match.group(2).strip(),
                "vars": {},
            }
            continue
        if current is None:
            continue
        variable_match = _VARIABLE_RE.match(line)
        if variable_match:
            current["vars"][variable_match.group(1)] = variable_match.group(2).strip()
    if current is not None:
        steps.append(current)
    step_count = len(steps)
    narrative = (
        f"TLC found a violation of invariant {invariant}. "
        f"The counterexample has {step_count} state(s). "
        "State 1 is the initial state; the final state is the impossible state. "
        f"The invariant {invariant} was violated because the system reached a state "
        "that was modeled as IMPOSSIBLE. Review the design constraints and the Next "
        "predicate to prevent this transition."
    )
    return {
        "role": "tla_trace_interpret",
        "status": "completed",
        "invariant": invariant,
        "step_count": step_count,
        "steps": steps,
        "narrative": narrative,
    }


__all__ = ["parse_tlc_trace"]
