"""Shared fail-closed budget pre-check for all call sites."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def budget_pre_check(guard) -> str | None:
    """Return a denial reason string, or None if the call is allowed.

    Handles both guard interfaces:
      - check_all_limits(estimated_cost=0.0) -> dict
      - try_charge(cost=0.0) -> dict

    If guard is None, returns None (allowed).
    If guard has neither method, returns an error string (fail-closed).
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

    elif hasattr(guard, "try_charge"):
        try:
            verdict = guard.try_charge(cost=0.0)
        except Exception as exc:
            return f"budget check raised: {exc}"
        if not isinstance(verdict, dict):
            return "budget check returned non-dict"
        if not verdict.get("allowed", False):
            return verdict.get("reason", "budget exhausted")
        return None

    else:
        return "budget guard has unknown interface"
