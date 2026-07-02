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

# Burn-rate: when the projected time-to-exhaustion drops below this many
# seconds we escalate the burn_rate hint from "info" to "warning" so a role
# knows it is about to run out of budget very soon.
_BURN_RATE_WARN_SECONDS = 120.0

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


# ---------------------------------------------------------------------------
# Per-task advice surface (build_advice)
# ---------------------------------------------------------------------------
#
# A richer, per-task recommendation than build_optimization_hints: given a
# resolved routing decision plus the chosen profile's characteristics and a few
# environment flags, it tells a playbook whether to drive the multi-step
# langgraph workflow vs a single-shot router call, which model profile to use,
# the projected cost, and a small bag of resource hints. Like the rest of this
# module it is PURE: the caller (the route) does all app.state / network I/O and
# hands the already-resolved facts in; build_advice never raises.

# Model-name prefixes that denote a "reasoning" model (slower, deliberative).
# REPLICATED from daemon.py's _REASONING_MODEL_PREFIXES (which is a function-
# local in _lifespan and therefore NOT importable); daemon.py is a top-of-tree
# assembler that imports nearly every subsystem, so importing from it here would
# create an import cycle. Keep these two tuples in sync.
_REASONING_MODEL_PREFIXES: tuple[str, ...] = ("glm-4.5", "glm-5")

# Work types that benefit from the gated multi-step workflow (classify -> select
# -> generate -> review -> quality-gated-retry) vs a single-shot router call.
_WORKFLOW_WORK_TYPES: frozenset[str] = frozenset(
    {"feature", "bugfix", "refactor", "review"}
)
_SINGLE_SHOT_WORK_TYPES: frozenset[str] = frozenset({"docs", "chat", "classify"})

# Number of workflow retries to budget headroom for when deciding use_workflow.
# The workflow can re-run the generate/review step on a quality-gate miss; we
# require headroom for est_cost * (max_retries + 1) before recommending it.
_DEFAULT_MAX_RETRIES = 2


def _latency_class(
    *,
    model_name: str | None,
    max_output_tokens: int | None,
    healthy: bool,
) -> str:
    """Pure latency bucket for a chosen profile.

    - "degraded" when the model is unhealthy (overrides everything else);
    - "slow" for reasoning models (prefix match on _REASONING_MODEL_PREFIXES);
    - "fast" for a small max_output budget on a healthy model;
    - "standard" otherwise.
    """
    if not healthy:
        return "degraded"
    name = (model_name or "").strip().lower()
    if name and any(name.startswith(p) for p in _REASONING_MODEL_PREFIXES):
        return "slow"
    mo = max_output_tokens if isinstance(max_output_tokens, int) else None
    if mo is not None and mo <= 4000:
        return "fast"
    return "standard"


def build_advice(
    *,
    work_type: str,
    priority: str = "quality",
    recommendation: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    est_cost_usd: float | None = None,
    prompt_tokens: int | None = None,
    healthy: bool = True,
    gateway_present: bool = False,
    local_model_allowed: bool = False,
    has_local_or_free_profile: bool = False,
    budget_ok: bool = True,
    budget_warning: bool = False,
    budget_remaining_usd: float | None = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Assemble the per-task advice contract. PURE — never raises.

    The caller resolves the adaptive routing decision (``recommendation``), the
    chosen profile's characteristic fields (``profile``: model_name,
    context_window, max_output_tokens, model_profile_id, prompt_profile_id) and
    the environment flags, then hands them here. On any internal inconsistency a
    fallback recommendation is returned rather than an exception.

    Returns the dict described in the route docstring:
    ``{task_type, recommendation{...}, route{...}, est_cost_usd, use_workflow,
    workflow_reason, resource_hints{...}}``.
    """
    try:
        rec = dict(recommendation or {})
        prof = dict(profile or {})
        wt = (work_type or "").strip().lower()

        model_profile_id = rec.get("selected_model_profile_id") or prof.get(
            "model_profile_id"
        )
        prompt_profile_id = rec.get("selected_prompt_profile_id")

        latency_class = _latency_class(
            model_name=prof.get("model_name"),
            max_output_tokens=prof.get("max_output_tokens"),
            healthy=healthy,
        )
        quality_class = prof.get("quality_class") or (
            "high" if not rec.get("fallback") else "unknown"
        )

        est_cost = _safe_float(est_cost_usd)
        if est_cost is None:
            est_cost = _safe_float(rec.get("estimated_cost_usd")) or 0.0

        # context_fits: prompt fits inside the chosen profile's context window.
        context_window = prof.get("context_window")
        if isinstance(prompt_tokens, int) and isinstance(context_window, int):
            context_fits = prompt_tokens <= context_window
        else:
            # Unknown either way -> assume it fits rather than block work.
            context_fits = True

        # prefer_local: hardware allows a local model AND the caller is
        # cost-optimizing AND a local/free profile actually exists.
        prefer_local = bool(
            local_model_allowed
            and (priority or "").strip().lower() == "cost"
            and has_local_or_free_profile
        )

        # use_workflow: gated multi-step pipeline only for the heavier work
        # types, only when a gateway exists, and only when the budget can
        # absorb a few retries.
        retries = max_retries if isinstance(max_retries, int) and max_retries >= 0 else 0
        headroom_needed = est_cost * (retries + 1)
        rem = _safe_float(budget_remaining_usd)
        budget_allows_workflow = budget_ok and (
            rem is None or rem >= headroom_needed
        )

        if wt in _SINGLE_SHOT_WORK_TYPES:
            use_workflow = False
            workflow_reason = f"work_type '{wt}' is single-shot"
        elif wt not in _WORKFLOW_WORK_TYPES:
            use_workflow = False
            workflow_reason = f"work_type '{wt}' not in workflow set"
        elif not gateway_present:
            use_workflow = False
            workflow_reason = "no model gateway available"
        elif not budget_allows_workflow:
            use_workflow = False
            workflow_reason = (
                f"insufficient budget headroom for "
                f"{retries + 1}x est_cost (${headroom_needed:.4f})"
            )
        else:
            use_workflow = True
            workflow_reason = (
                f"work_type '{wt}' benefits from gated multi-step workflow"
            )

        recommendation_out: dict[str, Any] = {
            "model_profile": model_profile_id,
            "reason": rec.get("reason", "insufficient_historical_data"),
            "composite_score": _safe_float(rec.get("composite_score")) or 0.0,
            "fallback": bool(rec.get("fallback", True)),
            "sample_count": int(rec.get("sample_count", 0) or 0),
            "latency_class": latency_class,
            "quality_class": quality_class,
        }

        return {
            "task_type": work_type,
            "recommendation": recommendation_out,
            "route": {
                "selected_model_profile_id": model_profile_id,
                "selected_prompt_profile_id": prompt_profile_id,
            },
            "est_cost_usd": float(est_cost),
            "use_workflow": use_workflow,
            "workflow_reason": workflow_reason,
            "resource_hints": {
                "prefer_local": prefer_local,
                "budget_ok": bool(budget_ok),
                "budget_warning": bool(budget_warning),
                "context_fits": bool(context_fits),
            },
        }
    except Exception:  # pragma: no cover - defensive; never 500 the route
        return _advice_fallback(work_type)


def _advice_fallback(work_type: str) -> dict[str, Any]:
    """Safe fallback advice when anything in build_advice goes wrong."""
    return {
        "task_type": work_type,
        "recommendation": {
            "model_profile": "default",
            "reason": "insufficient_historical_data",
            "composite_score": 0.0,
            "fallback": True,
            "sample_count": 0,
            "latency_class": "standard",
            "quality_class": "unknown",
        },
        "route": {
            "selected_model_profile_id": "default",
            "selected_prompt_profile_id": None,
        },
        "est_cost_usd": 0.0,
        "use_workflow": False,
        "workflow_reason": "fallback: insufficient_historical_data",
        "resource_hints": {
            "prefer_local": False,
            "budget_ok": True,
            "budget_warning": False,
            "context_fits": True,
        },
    }


def build_optimization_hints(
    *,
    models: list[dict[str, Any]] | None = None,
    routing: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    metrics_collector: Any | None = None,
    token_tracker: Any | None = None,
) -> dict[str, Any]:
    """Return ``{"hints": [...], "recommended_profile_for": {...}}``.

    Never raises. ``hints`` is a list of ``{signal, recommendation, severity}``
    (some richer signals carry extra keys, e.g. ``burn_rate`` and
    ``model_flaky``); ``recommended_profile_for`` maps known work-types to a
    concrete profile_id drawn from the routing config.

    ``metrics_collector`` is a non-pure input: an optional, duck-typed
    MetricsCollector (``is_flaky`` / ``get_recent_failures``) the caller may
    hand in so the advisor can flag flaky models. It is read defensively and
    never mutated; ``None`` (or any failure reading it) simply omits the
    flakiness hints.

    ``token_tracker`` is a second optional, duck-typed, read-only dependency: a
    :class:`~general_ludd.observability.token_cost.TokenCostTracker` whose
    ``classify(work_type)`` refines the static cheap/quality taxonomy with what
    gludd has actually OBSERVED — a normally-"quality" work-type seen token-light
    flips to the weak profile, a normally-"cheap" one seen token-heavy flips to
    the default/role profile. ``None`` (or a tracker with too few samples, which
    classifies ``"unknown"``/``"moderate"``) falls back to the static mapping, so
    an unset/empty tracker is byte-identical to the previous behavior.
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

    # 1b) Burn-rate / time-to-exhaustion: derive the run's spend velocity from
    #     the budget facet (run_spent_usd over elapsed_seconds) and project how
    #     long the remaining budget lasts at that rate. Each guard is explicit
    #     so a missing/zero/garbage facet field simply omits the hint.
    run_spent = _safe_float(budget.get("run_spent_usd"))
    elapsed = _safe_float(budget.get("elapsed_seconds"))
    run_remaining = _safe_float(budget.get("run_remaining_usd"))
    if (
        run_spent is not None
        and run_spent > 0
        and elapsed is not None
        and elapsed > 0
    ):
        velocity = run_spent / elapsed
        time_remaining: float | None = None
        if velocity > 0 and run_remaining is not None and run_remaining >= 0:
            time_remaining = run_remaining / velocity
        severity = "info"
        if time_remaining is not None and time_remaining < _BURN_RATE_WARN_SECONDS:
            severity = "warning"
        hints.append(
            {
                "signal": "burn_rate",
                "recommendation": (
                    "budget burning at "
                    f"{velocity:.6f} USD/s"
                    + (
                        f"; ~{time_remaining:.0f}s of budget remaining"
                        if time_remaining is not None
                        else "; time-to-exhaustion unknown"
                    )
                ),
                "velocity_usd_per_sec": velocity,
                "time_remaining_sec": time_remaining,
                "severity": severity,
            }
        )

    # 2) Work-type -> profile recommendations from routing, refined by the
    #    LEARNED token-cost classification when the tracker has enough samples.
    #    Cheap/mechanical work -> weak profile; high-quality work -> default (or
    #    a role-specific profile). A tracker that has OBSERVED a work-type as
    #    token-light/heavy overrides the static taxonomy for that work-type;
    #    "moderate"/"unknown" (incl. an empty/absent tracker) keeps the static call.
    quality_target = default_profile

    def _resolve(work_type: str, static_kind: str) -> str | None:
        learned = "unknown"
        if token_tracker is not None:
            try:
                learned = token_tracker.classify(work_type)
            except Exception:
                # A misbehaving tracker must never break the advisory — fall
                # back to the static taxonomy.
                learned = "unknown"
        if learned == "light":
            kind = "cheap"
        elif learned == "heavy":
            kind = "quality"
        else:
            kind = static_kind
        if kind == "cheap":
            # weak_profile may be None; the caller's `if target` then drops it,
            # reproducing the previous `if weak_profile:` guard exactly.
            return weak_profile
        role_target = role_routing.get(work_type)
        return role_target or quality_target

    for work_type in _CHEAP_WORK_TYPES:
        target = _resolve(work_type, "cheap")
        if target:
            recommended[work_type] = target
    for work_type in _QUALITY_WORK_TYPES:
        target = _resolve(work_type, "quality")
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

    # 4) Model flakiness: when a metrics collector is reachable, flag any
    #    enabled profile the collector judges flaky (low success rate over
    #    enough calls) and surface its most-recent failures so a role can prefer
    #    a fallback. Fully defensive — no collector, or any error reading it,
    #    simply yields no flakiness hints (never an exception).
    if metrics_collector is not None and hasattr(metrics_collector, "is_flaky"):
        for m in models:
            if not isinstance(m, dict):
                continue
            if not m.get("enabled"):
                continue
            profile_id = m.get("profile_id")
            if not profile_id:
                continue
            try:
                if not metrics_collector.is_flaky(profile_id):
                    continue
                recent: list[str] = []
                getter = getattr(metrics_collector, "get_recent_failures", None)
                if callable(getter):
                    fetched = getter(profile_id)
                    if isinstance(fetched, (list, tuple)):
                        recent = list(fetched)[:3]
                hints.append(
                    {
                        "signal": "model_flaky",
                        "model": profile_id,
                        "recommendation": "prefer fallback profile",
                        "recent_failures": recent,
                        "severity": "warning",
                    }
                )
            except Exception:  # pragma: no cover - defensive; never raise
                continue

    return {"hints": hints, "recommended_profile_for": recommended}
