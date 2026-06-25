"""Pure optimization-advisor for the environment-introspection endpoint.

``build_optimization_hints`` distills the already-assembled environment signals
(model roster, routing, budget) into actionable, *pure* guidance for the model
running a gludd job: where it is spending budget, which model profile to prefer
for a given kind of work, and when to fall back to a cheaper metered model.

Design constraints:
  - PURE: no I/O, no app.state access, no network — operates only on the dicts
    handed to it (which the router has already assembled defensively).
  - DEFENSIVE: every branch is guarded; no exception ever escapes. A malformed
    signal yields fewer hints, never a crash, so a sub-section failure in the
    caller can never turn into a 500.
"""

from __future__ import annotations

from typing import Any

# When run-budget spend crosses this fraction of the run limit we emit a budget
# hint nudging the job to reduce scope / prefer the weak (cheap) profile.
_BUDGET_WARN_FRACTION = 0.8

# Work-type taxonomy -> the *kind* of profile the routing should serve it from.
# "cheap" maps to weak_model_profile; "quality" maps to the default/role profile.
# Kept deliberately small and stable so playbooks can rely on the keys.
_CHEAP_WORK_TYPES = (
    "mechanical",
    "cheap",
    "formatting",
    "lint",
    "rename",
    "boilerplate",
    "summarize",
    "classify",
    "extract",
)
_QUALITY_WORK_TYPES = (
    "high_quality",
    "architecture",
    "design",
    "security",
    "review",
    "reasoning",
    "planning",
    "debug",
)


def _safe_float(value: Any) -> float | None:
    """Coerce *value* to a finite float, or None when it is not usable."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # Reject NaN / inf — they make every comparison below meaningless.
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def build_optimization_hints(
    *,
    models: list[dict[str, Any]] | None = None,
    routing: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``{"hints": [...], "recommended_profile_for": {...}}``.

    Never raises. ``hints`` is a list of ``{signal, recommendation, severity}``;
    ``recommended_profile_for`` maps known work-types to a concrete profile_id
    drawn from the routing config.
    """
    models = models or []
    routing = routing or {}
    budget = budget or {}

    hints: list[dict[str, Any]] = []
    recommended: dict[str, str] = {}

    default_profile = routing.get("default_profile")
    weak_profile = routing.get("weak_model_profile")
    role_routing = routing.get("roles") or {}
    if not isinstance(role_routing, dict):
        role_routing = {}

    # 1) Budget pressure: if a large fraction of the run budget is already spent,
    #    nudge toward smaller scope / the weak (cheap) profile.
    run_spent = _safe_float(budget.get("run_spent_usd"))
    run_limit = _safe_float(budget.get("run_limit_usd"))
    if run_spent is not None and run_limit is not None and run_limit > 0:
        fraction = run_spent / run_limit
        if fraction >= _BUDGET_WARN_FRACTION:
            severity = "critical" if fraction >= 1.0 else "warning"
            rec = "reduce scope / prefer weak_model_profile"
            if weak_profile:
                rec = (
                    f"reduce scope / prefer weak_model_profile "
                    f"({weak_profile})"
                )
            hints.append(
                {
                    "signal": "budget",
                    "recommendation": rec,
                    "severity": severity,
                }
            )

    # 2) Work-type -> profile recommendations from routing.
    #    Cheap/mechanical work -> weak profile; high-quality work -> default
    #    (or a role-specific profile when routing names one).
    if weak_profile:
        for work_type in _CHEAP_WORK_TYPES:
            recommended[work_type] = weak_profile
    quality_target = default_profile
    for work_type in _QUALITY_WORK_TYPES:
        # Prefer an explicit role mapping for this work-type when present.
        role_target = role_routing.get(work_type)
        target = role_target or quality_target
        if target:
            recommended[work_type] = target

    # 3) Metered-model fallback: if any enabled, api_metered profile has a
    #    cheaper fallback available, recommend preferring the fallback for
    #    low-stakes work.
    profile_ids = {
        m.get("profile_id")
        for m in models
        if isinstance(m, dict) and m.get("profile_id")
    }
    for m in models:
        if not isinstance(m, dict):
            continue
        if not m.get("enabled") or not m.get("api_metered"):
            continue
        fallbacks = m.get("fallback_profiles") or []
        if not isinstance(fallbacks, (list, tuple)):
            continue
        # A fallback is "available" if it is a known profile in the roster.
        available = [fb for fb in fallbacks if fb in profile_ids]
        if available:
            hints.append(
                {
                    "signal": "metered_model",
                    "recommendation": (
                        f"profile '{m.get('profile_id')}' is api_metered; prefer "
                        f"fallback '{available[0]}' for low-stakes work"
                    ),
                    "severity": "info",
                }
            )

    return {"hints": hints, "recommended_profile_for": recommended}
