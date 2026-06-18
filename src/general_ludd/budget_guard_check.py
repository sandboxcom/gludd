"""Shared budget-guard pre-check used by every model-call site.

All three callers — ExecutionEngine, ReturnReviewer, and
invoke_model_for_generation — must enforce the SAME fail-closed semantics.
This module is the single source of that logic so the three surfaces cannot
drift (mirrors the original engine.py ``_budget_pre_check``).

Fail-CLOSED contract
--------------------
* ``guard=None``                                    → ``None``  (intentional no-op)
* ``guard`` has ``check_all_limits``                → call it; non-dict / missing
  ``allowed`` / raises → deny string
* ``guard`` has ``try_charge`` (but not ``check_all_limits``) → call it; non-dict /
  ``allowed`` falsy / raises → deny string
* ``guard`` has neither method (unknown shape, guard is not None) → deny string

The function returns ``None`` to mean "allowed" or a non-empty string
(the denial reason) to mean "denied".  Callers decide what to do with the
denial (raise, return failed TaskReturn, return None, etc.).
"""

from __future__ import annotations

from typing import Any


def budget_pre_check(guard: Any) -> str | None:
    """Run budget pre-check; return denial reason string or ``None`` (allowed).

    See module docstring for the full fail-closed contract.
    """
    if guard is None:
        return None

    if hasattr(guard, "check_all_limits"):
        try:
            verdict = guard.check_all_limits(estimated_cost=0.0)
        except Exception as exc:
            return f"budget check raised: {exc}"
        if not isinstance(verdict, dict):
            return "budget check returned non-dict"
        if not verdict.get("allowed", False):
            return verdict.get("reason", "budget exhausted")
        return None

    if hasattr(guard, "try_charge"):
        try:
            verdict = guard.try_charge(cost=0.0)
        except Exception as exc:
            return f"budget check raised: {exc}"
        if not isinstance(verdict, dict):
            return "budget check returned non-dict"
        if not verdict.get("allowed", False):
            return verdict.get("reason", "budget exhausted")
        return None

    return "budget guard has unknown interface"
