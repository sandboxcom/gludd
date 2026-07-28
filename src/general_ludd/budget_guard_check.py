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
from typing import Protocol, cast

logger = logging.getLogger(__name__)


class _RemainingGuard(Protocol):
    """Structural type for guards exposing a ``remaining()`` USD budget query."""

    def remaining(self, now: float | None = ...) -> float: ...


class _GatewayProfile(Protocol):
    """Structural type for a model gateway exposing ``get_profile``."""

    def get_profile(self, name: str) -> object | None: ...


def compute_projected_cost_usd(gateway: _GatewayProfile | None, budget_guard: object | None = None) -> float:
    """Compute a per-call USD cost projection from the default model profile.

    Derives the projection from the model gateway's default profile (model
    name, max input/output tokens) and the guard's ``token_cost_usd`` when
    available, falling back to the static pricing table otherwise.  Returns
    0.0 when no gateway is available so callers that lack one stay compatible
    with the prior reactive-only semantics.

    The projection is capped at 1 000 input tokens to keep it conservative.
    """
    if gateway is None:
        return 0.0

    try:
        profile = gateway.get_profile("default")
    except Exception:
        logger.debug("compute_projected_cost_usd: gateway.get_profile failed")
        return 0.0

    if profile is None:
        return 0.0

    try:
        model = str(getattr(profile, "model_name", "__default__") or "__default__")
        proj_in = min(int(getattr(profile, "max_input_tokens", 1000) or 1000), 1000)
        proj_out = int(getattr(profile, "max_output_tokens", 0) or 0)
    except (TypeError, ValueError):
        logger.debug("compute_projected_cost_usd: profile attribute coercion failed")
        return 0.0

    guard_cost = getattr(budget_guard, "token_cost_usd", None)
    if callable(guard_cost):
        try:
            return float(guard_cost(model, proj_in, proj_out))
        except Exception:
            logger.debug("compute_projected_cost_usd: guard.token_cost_usd raised; falling back to static table")

    from general_ludd.infra.pricing import token_cost_usd as _static

    return float(_static(model, proj_in, proj_out))


def budget_pre_check(guard: object, projected_cost: float = 0.0) -> str | None:
    """Run a non-mutating budget pre-check against *guard*.

    Args:
        guard:          A budget guard instance, or ``None`` (no budget configured).
        projected_cost: Forwarded to the guard so it can block the call whose own
                        projection crosses the cap, not only react to prior calls'
                        cumulative spend. Default ``0.0`` preserves the documented
                        reactive-only semantics for callers that do not yet thread
                        a projection (security finding #14: the engine path now
                        threads a positive projection; reviewer / job_invocation /
                        tool_loop retain the 0.0 default until their own fixes land).

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
            verdict = guard.check_all_limits(estimated_cost=projected_cost)
        except Exception as exc:
            logger.warning("budget_pre_check: check_all_limits raised: %s", exc)
            return f"budget check raised: {exc}"

        if not isinstance(verdict, dict):
            logger.warning("budget_pre_check: check_all_limits returned non-dict: %r", verdict)
            return "budget check returned non-dict"

        if not verdict.get("allowed", False):
            reason = verdict.get("reason", "budget exhausted")
            return str(reason)

        return None

    # --- SpendLimiter (exposes would_exceed -- the real non-mutating check) ---
    if hasattr(guard, "would_exceed"):
        try:
            # would_exceed(projected_usd: float, now: float | None = None) -> bool.
            # A projected cost of 0.0 only triggers deferral when the window is
            # ALREADY over the limit (the guard's own documented semantics);
            # a positive projected_cost lets the guard block the over-cap call.
            over = guard.would_exceed(projected_cost)
        except Exception as exc:
            logger.warning("budget_pre_check: would_exceed raised: %s", exc)
            return f"budget check raised: {exc}"

        if over:
            # Extract a human-readable reason if the guard exposes remaining().
            try:
                remaining = cast(_RemainingGuard, guard).remaining()
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
