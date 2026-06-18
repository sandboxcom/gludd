"""Non-mutating budget pre-check dispatcher.

Centralises "can I proceed?" pre-call budget gating for the reviewer and
job-invocation paths.  Uses REAL non-mutating methods discovered by reading
the actual guard implementations:

* RunBudgetGuard  -> ``check_all_limits(estimated_cost=0.0)``
  Returns ``dict[str, bool | str | float]`` with an ``"allowed"`` key.

* SpendLimiter    -> ``would_exceed(projected_usd: float) -> bool``
  Returns True when the call *would* push spend past the cap.
  This is the correct NON-MUTATING check; ``try_charge`` RECORDS a charge
  and must NOT be used as a pre-check (it mutates state even at cost=0.0
  when no cap is configured, and its ``kind`` kwarg is required).

* None guard      -> proceed (no budget configured).

* Unknown shape   -> deny (fail-closed).

Any exception during the check is caught and turned into a denial string so
callers never see a raw exception from the guard layer.
"""

from __future__ import annotations

import logging
from typing import Any, cast

logger = logging.getLogger(__name__)


def budget_pre_check(guard: object) -> str | None:
    """Run a non-mutating budget pre-check against *guard*.

    Args:
        guard: A budget guard instance, or ``None`` (no budget configured).

    Returns:
        ``None`` when the call is allowed.
        A non-empty denial reason string when the call should be blocked.
    """
    # No guard -> no budget configured, proceed.
    if guard is None:
        return None

    # --- RunBudgetGuard (and any guard that exposes check_all_limits) ---
    if hasattr(guard, "check_all_limits"):
        try:
            verdict = guard.check_all_limits(estimated_cost=0.0)
        except Exception as exc:
            logger.warning("budget_pre_check: check_all_limits raised: %s", exc)
            return f"budget check raised: {exc}"

        if not isinstance(verdict, dict):
            logger.warning(
                "budget_pre_check: check_all_limits returned non-dict: %r", verdict
            )
            return "budget check returned non-dict"

        if not verdict.get("allowed", False):
            reason = verdict.get("reason", "budget exhausted")
            return str(reason)

        return None

    # --- SpendLimiter (exposes would_exceed -- the real non-mutating check) ---
    if hasattr(guard, "would_exceed"):
        try:
            # would_exceed(projected_usd: float, now: float | None = None) -> bool
            # A projected cost of 0.0 only triggers deferral when the window is
            # ALREADY over the limit (the guard's own documented semantics).
            over = guard.would_exceed(0.0)
        except Exception as exc:
            logger.warning("budget_pre_check: would_exceed raised: %s", exc)
            return f"budget check raised: {exc}"

        if over:
            # Extract a human-readable reason if the guard exposes remaining().
            try:
                remaining = cast(Any, guard).remaining()
                return f"spend limit exceeded: remaining=${remaining:.6f}"
            except Exception:
                return "spend limit exceeded"

        return None

    # --- Unknown interface: fail-closed ---
    logger.warning(
        "budget_pre_check: guard %r has no recognised interface "
        "(check_all_limits / would_exceed) -- denying (fail-closed)",
        type(guard).__name__,
    )
    return "budget guard has unknown interface"
