"""Environment-introspection API: GET /api/environment.

A single, easy way for the model running a gludd job to see the environment it
runs inside — the model roster, routing policy, budget posture, compute options,
the tools/skills it can invoke, queue depth, host system facts — plus an
optimization advisor that distills actionable guidance from those signals.

This endpoint REUSES existing facets rather than re-deriving stats:
  - models   -> ModelGateway.list_profiles() (characteristic fields only)
  - routing  -> app.state._startup_config["model_routing"]
  - budget   -> RunBudgetGuard (app.state._budget_guard) + the spend-limiter
                facet reused verbatim from routers/facts.py (_spend_facet)
  - compute  -> ComputeProvider / GPUType enums + active ComputeConfig if present
  - tools    -> MCPClient.list_tools() + the static gludd_* ansible modules
  - skills   -> SkillRegistry.list_skills()
  - system   -> stdlib os / sys / shutil (never shells out)
  - optimization -> controllers.environment_advisor.build_optimization_hints

CRITICAL SECURITY: the model roster NEVER serializes api keys, tokens, the PSK,
auth headers, credential aliases, or any secret field. Only the explicit
characteristic fields below are emitted.

PSK auth is applied by the daemon middleware (the path is not public), exactly
like /api/facts.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sys
from typing import Annotated, Any

from fastapi import FastAPI, Query
from pydantic import BaseModel

from general_ludd.controllers.environment_advisor import (
    build_advice,
    build_optimization_hints,
)
from general_ludd.routers.facts import _spend_facet

logger = logging.getLogger(__name__)

# work_type (free-form, playbook-facing) -> TaskType enum member for adaptive
# routing. The advise surface accepts the orchestration vocabulary (feature /
# bugfix / refactor / review / docs / chat / classify); TaskType has no chat or
# classify member, so unmapped values fall back to FEATURE (mirroring
# routers/models.py's TaskType(...) -> TaskType.FEATURE fallback).
_WORK_TYPE_TO_TASK_TYPE: dict[str, str] = {
    "feature": "feature",
    "bugfix": "bug_fix",
    "bug_fix": "bug_fix",
    "refactor": "refactor",
    "review": "code_review",
    "code_review": "code_review",
    "test": "test_write",
    "test_write": "test_write",
    "docs": "documentation",
    "documentation": "documentation",
    "debug": "debugging",
    "debugging": "debugging",
    "optimize": "optimization",
    "optimization": "optimization",
    "security": "security_fix",
    "security_fix": "security_fix",
    "integration": "integration",
    # chat / classify have no TaskType member -> caller falls back to FEATURE.
}

# Default nominal output-token estimate for the cost projection when the caller
# does not (cannot) know the completion size up front.
_DEFAULT_EXPECTED_OUTPUT_TOKENS = 1024

# The ansible gludd_* modules a job can always invoke against the daemon, even
# when no MCP server is wired. Kept as the fail-soft floor for the tools catalog.
_ANSIBLE_TOOL_MODULES: list[dict[str, str]] = [
    {
        "name": "gludd_facts",
        "source": "ansible",
        "description": "Inject live daemon facts (work/todos/models/history/messages).",
    },
    {
        "name": "gludd_metrics",
        "source": "ansible",
        "description": "Inject live daemon metrics (agents/usage/cost/benchmarks).",
    },
    {
        "name": "gludd_traces",
        "source": "ansible",
        "description": "Inject recent execution traces + by-phase aggregate.",
    },
    {
        "name": "gludd_environment",
        "source": "ansible",
        "description": "Inject the consolidated environment brief (this endpoint).",
    },
]

# Characteristic ModelProfile fields that are SAFE to expose. Deliberately
# enumerated (allow-list) so a future ModelProfile field carrying a secret
# (credential_alias, api_base_alias, etc.) can never leak through a blanket dump.
_SAFE_MODEL_FIELDS = (
    "enabled",
    "quality_class",
    "latency_class",
    "context_window",
    "max_input_tokens",
    "max_output_tokens",
    "cost_per_input_token",
    "cost_per_output_token",
    "run_budget_usd",
    "api_metered",
    "fallback_profiles",
)


class EnvironmentBrief(BaseModel):
    """Consolidated environment + optimization brief for a running job."""

    models: list[dict[str, Any]] = []
    routing: dict[str, Any] = {}
    budget: dict[str, Any] = {}
    compute: dict[str, Any] = {}
    tools: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    queues: list[dict[str, Any]] = []
    system: dict[str, Any] = {}
    optimization: dict[str, Any] = {}


class AdviceBrief(BaseModel):
    """Per-task advice surface for ``GET /api/environment/advise``."""

    task_type: str = ""
    recommendation: dict[str, Any] = {}
    route: dict[str, Any] = {}
    est_cost_usd: float = 0.0
    use_workflow: bool = False
    workflow_reason: str = ""
    resource_hints: dict[str, Any] = {}


async def _resolve_advice(
    app: FastAPI,
    *,
    work_type: str,
    prompt_tokens: int | None,
    priority: str,
) -> dict[str, Any]:
    """Gather the per-task advice for *work_type* from live app.state.

    Does ALL the app.state / network I/O (adaptive routing, profile lookup,
    cost projection, budget gate, health), then delegates the pure decision
    logic to ``controllers.environment_advisor.build_advice``. Wrapped so any
    failure yields the advisor's fallback recommendation rather than a 500.
    """
    from general_ludd.controllers.environment_advisor import _advice_fallback

    try:
        # 1) Adaptive routing decision. Prefer the LIVE router on app.state,
        #    fall back to a fresh AdaptiveRouter() (mirrors models.py).
        from general_ludd.schemas.benchmark import TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        router = getattr(app.state, "_adaptive_router", None)
        if router is None or not hasattr(router, "route"):
            router = AdaptiveRouter()

        task_value = _WORK_TYPE_TO_TASK_TYPE.get(
            (work_type or "").strip().lower(), "feature"
        )
        try:
            task_type = TaskType(task_value)
        except ValueError:  # pragma: no cover - mapping table is closed
            task_type = TaskType.FEATURE

        recommendation: dict[str, Any] = {
            "selected_prompt_profile_id": None,
            "selected_model_profile_id": "default",
            "composite_score": 0.0,
            "estimated_cost_usd": 0.0,
            "sample_count": 0,
            "fallback": True,
            "reason": "insufficient_historical_data",
        }
        try:
            decision = await router.route(task_type)
            recommendation = {
                "selected_prompt_profile_id": decision.selected_prompt_profile_id,
                "selected_model_profile_id": decision.selected_model_profile_id,
                "composite_score": decision.composite_score,
                "estimated_cost_usd": decision.estimated_cost_usd,
                "sample_count": (
                    max(decision.sample_count, 1) if not decision.fallback else 0
                ),
                "fallback": decision.fallback,
                "reason": decision.reason,
            }
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("advise: adaptive routing failed: %s", exc)

        # 2) Resolve the chosen profile's characteristic fields from the gateway.
        gateway = getattr(app.state, "_model_gateway", None)
        gateway_present = gateway is not None and hasattr(gateway, "list_profiles")
        chosen_id = recommendation.get("selected_model_profile_id")
        profile: dict[str, Any] = {}
        has_local_or_free_profile = False
        if gateway_present and gateway is not None:
            try:
                prof_obj = None
                if hasattr(gateway, "get_profile") and chosen_id:
                    prof_obj = gateway.get_profile(chosen_id)
                if prof_obj is not None:
                    profile = {
                        "model_profile_id": getattr(
                            prof_obj, "model_profile_id", chosen_id
                        ),
                        "model_name": getattr(prof_obj, "model_name", None),
                        "context_window": getattr(prof_obj, "context_window", None),
                        "max_output_tokens": getattr(
                            prof_obj, "max_output_tokens", None
                        ),
                        "quality_class": getattr(prof_obj, "quality_class", None),
                    }
                # A "local/free" profile = not api_metered (no per-token spend).
                for p in gateway.list_profiles():
                    if getattr(p, "enabled", False) and not getattr(
                        p, "api_metered", True
                    ):
                        has_local_or_free_profile = True
                        break
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("advise: profile lookup failed: %s", exc)

        # 3) Health of the chosen model (degraded latency when unhealthy).
        healthy = True
        health_tracker = getattr(app.state, "_health_tracker", None)
        if health_tracker is not None and chosen_id:
            try:
                healthy = bool(
                    health_tracker.is_healthy(chosen_id, admit_probe=False)
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("advise: health check failed: %s", exc)

        # 4) Cost projection. Prefer the spend-limiter (live catalog), else the
        #    static infra pricing table.
        in_tokens = prompt_tokens if isinstance(prompt_tokens, int) else 0
        out_tokens = _DEFAULT_EXPECTED_OUTPUT_TOKENS
        model_name = profile.get("model_name") or chosen_id or "__default__"
        est_cost_usd: float | None = None
        limiter = getattr(app.state, "_spend_limiter", None)
        if limiter is not None and hasattr(limiter, "token_cost_usd"):
            try:
                est_cost_usd = float(
                    limiter.token_cost_usd(model_name, in_tokens, out_tokens)
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("advise: limiter cost projection failed: %s", exc)
        if est_cost_usd is None:
            try:
                from general_ludd.infra.pricing import (
                    token_cost_usd as _static_cost,
                )

                est_cost_usd = float(
                    _static_cost(model_name, in_tokens, out_tokens)
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("advise: static cost projection failed: %s", exc)
                est_cost_usd = 0.0

        # 5) Budget gate.
        budget_ok = True
        budget_warning = False
        budget_remaining_usd: float | None = None
        guard = getattr(app.state, "_budget_guard", None)
        if guard is not None and hasattr(guard, "check_all_limits"):
            try:
                verdict = guard.check_all_limits(estimated_cost=est_cost_usd)
                if isinstance(verdict, dict):
                    budget_ok = bool(verdict.get("allowed", True))
                    rem = verdict.get("remaining_budget")
                    if isinstance(rem, (int, float)):
                        budget_remaining_usd = float(rem)
                        # Warn when remaining headroom is thin relative to cost.
                        if est_cost_usd and budget_remaining_usd < est_cost_usd * 3:
                            budget_warning = True
                    if not budget_ok:
                        budget_warning = True
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("advise: budget gate failed: %s", exc)

        # 6) Hardware: is a local model permitted on this host?
        local_model_allowed = False
        hardware = getattr(app.state, "_hardware", None)
        if hardware is not None:
            local_model_allowed = bool(
                getattr(hardware, "local_model_allowed", False)
            )

        return build_advice(
            work_type=work_type,
            priority=priority,
            recommendation=recommendation,
            profile=profile,
            est_cost_usd=est_cost_usd,
            prompt_tokens=prompt_tokens,
            healthy=healthy,
            gateway_present=gateway_present,
            local_model_allowed=local_model_allowed,
            has_local_or_free_profile=has_local_or_free_profile,
            budget_ok=budget_ok,
            budget_warning=budget_warning,
            budget_remaining_usd=budget_remaining_usd,
        )
    except Exception as exc:  # pragma: no cover - never 500 the route
        logger.debug("advise resolution failed entirely: %s", exc)
        return _advice_fallback(work_type)


def _models_facet(app: FastAPI) -> list[dict[str, Any]]:
    """Roster of the gateway's profiles, secret fields NEVER serialized.

    Fails soft to ``[]`` when no gateway is on app.state.
    """
    gateway = getattr(app.state, "_model_gateway", None)
    if gateway is None or not hasattr(gateway, "list_profiles"):
        return []
    roster: list[dict[str, Any]] = []
    try:
        profiles = gateway.list_profiles()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("model roster unavailable: %s", exc)
        return []
    for p in profiles:
        # NOTE: build the dict from the SAFE allow-list only. The ModelProfile's
        # credential_alias / api_base_alias / provider_package / etc. are
        # intentionally excluded so no credential reference is ever emitted.
        entry: dict[str, Any] = {
            "profile_id": getattr(p, "model_profile_id", None),
            "provider": getattr(p, "provider", None),
            "model": getattr(p, "model_name", None),
        }
        for field in _SAFE_MODEL_FIELDS:
            entry[field] = getattr(p, field, None)
        # fallback_profiles is a list — copy defensively.
        fb = entry.get("fallback_profiles")
        if isinstance(fb, (list, tuple)):
            entry["fallback_profiles"] = list(fb)
        roster.append(entry)
    return roster


def _routing_facet(app: FastAPI) -> dict[str, Any]:
    """Routing policy from the startup model_routing config, or ``{}``."""
    startup_config = getattr(app.state, "_startup_config", {}) or {}
    routing = startup_config.get("model_routing")
    if routing is None:
        return {}
    try:
        return {
            "default_profile": getattr(routing, "default_profile", None),
            "weak_model_profile": getattr(routing, "weak_model_profile", None),
            "roles": dict(getattr(routing, "role_routing", {}) or {}),
            "latency": dict(getattr(routing, "latency_routing", {}) or {}),
            "quality": dict(getattr(routing, "quality_routing", {}) or {}),
            "fallback_chain": list(getattr(routing, "fallback_chain", []) or []),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("routing facet unavailable: %s", exc)
        return {}


def _budget_facet(app: FastAPI) -> dict[str, Any]:
    """Unified budget: run-level (RunBudgetGuard) + window/limiter spend.

    Run-level fields fail soft to ``None``; the window spend is reused verbatim
    from routers/facts.py (_spend_facet) rather than reimplemented.
    """
    facet: dict[str, Any] = {
        "run_remaining_usd": None,
        "run_limit_usd": None,
        "run_spent_usd": None,
        "elapsed_seconds": None,
    }
    guard = getattr(app.state, "_budget_guard", None)
    if guard is not None:
        try:
            spent = guard.get_total_spend()
            facet["run_spent_usd"] = spent
            facet["elapsed_seconds"] = guard.get_elapsed_seconds()
            check = guard.check_run_budget()
            remaining = check.get("remaining_budget")
            facet["run_remaining_usd"] = remaining
            # Derive the limit from spent + remaining when the guard is finite.
            if (
                isinstance(spent, (int, float))
                and isinstance(remaining, (int, float))
            ):
                facet["run_limit_usd"] = spent + remaining
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("run-budget facet unavailable: %s", exc)
    try:
        facet["window"] = _spend_facet(app)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("window-spend facet unavailable: %s", exc)
        facet["window"] = None
    return facet


def _compute_facet(app: FastAPI) -> dict[str, Any]:
    """Available compute providers + GPU types + the active config if present."""
    facet: dict[str, Any] = {"providers": [], "gpu_types": [], "configured": None}
    try:
        from general_ludd.infra.compute import ComputeProvider, GPUType

        facet["providers"] = [p.value for p in ComputeProvider]
        facet["gpu_types"] = [g.value for g in GPUType]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("compute enums unavailable: %s", exc)
    config = getattr(app.state, "_compute_config", None)
    if config is not None:
        try:
            facet["configured"] = (
                config.model_dump() if hasattr(config, "model_dump") else dict(config)
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("compute config not serializable: %s", exc)
            facet["configured"] = None
    return facet


async def _tools_facet(app: FastAPI) -> list[dict[str, Any]]:
    """Catalog of invocable tools: live MCP tools (if a client is wired) plus the
    static gludd_* ansible modules. Fails soft to just the ansible modules.
    """
    catalog: list[dict[str, Any]] = list(_ANSIBLE_TOOL_MODULES)
    mcp_client = getattr(app.state, "_mcp_client", None)
    if mcp_client is not None and hasattr(mcp_client, "list_tools"):
        try:
            tools = await mcp_client.list_tools()
            for t in tools or []:
                name = (
                    t.get("name")
                    if isinstance(t, dict)
                    else getattr(t, "name", None)
                )
                desc = (
                    t.get("description")
                    if isinstance(t, dict)
                    else getattr(t, "description", "")
                )
                if name:
                    catalog.append(
                        {"name": name, "source": "mcp", "description": desc or ""}
                    )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("mcp tool catalog unavailable: %s", exc)
    return catalog


def _skills_facet(app: FastAPI) -> list[dict[str, Any]]:
    """Skill names + descriptions from the SkillRegistry, or ``[]``."""
    registry = getattr(app.state, "_skill_registry", None)
    if registry is None or not hasattr(registry, "list_skills"):
        return []
    try:
        return [
            {"name": s.name, "description": getattr(s, "description", "")}
            for s in registry.list_skills()
        ]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("skills facet unavailable: %s", exc)
        return []


async def _queues_facet(app: FastAPI) -> list[dict[str, Any]]:
    """Queue name + depth. Reuses the todos/work backlog summary when reachable;
    fails soft to ``[]``.
    """
    factory = getattr(app.state, "_session_factory", None)
    if factory is None:
        return []
    try:
        from general_ludd.db.repository import TaskReturnRepository, TodoRepository

        async with factory() as session:
            todo_summary = await TodoRepository(session).status_summary()
            work_summary = await TaskReturnRepository(session).work_summary()
        queues: list[dict[str, Any]] = []
        backlog = todo_summary.get("backlog_size")
        if backlog is not None:
            queues.append({"name": "todos", "depth": backlog})
        in_flight = work_summary.get("in_flight")
        if in_flight is not None:
            queues.append({"name": "work_in_flight", "depth": in_flight})
        return queues
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("queues facet unavailable: %s", exc)
        return []


def _system_facet() -> dict[str, Any]:
    """Host system facts via stdlib only — never shells out. Every field is
    guarded and falls back to ``None`` on failure.
    """
    facet: dict[str, Any] = {
        "cpu_count": None,
        "python_version": None,
        "load_avg": None,
        "mem_available_mb": None,
        "disk_free_mb": None,
    }
    with contextlib.suppress(Exception):  # pragma: no cover - defensive
        facet["cpu_count"] = os.cpu_count()
    with contextlib.suppress(Exception):  # pragma: no cover - defensive
        facet["python_version"] = sys.version.split()[0]
    try:
        facet["load_avg"] = list(os.getloadavg())  # not available on all platforms
    except Exception:
        facet["load_avg"] = None
    try:
        usage = shutil.disk_usage(os.getcwd())
        facet["disk_free_mb"] = round(usage.free / (1024 * 1024), 1)
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        # mem_available is cheaply readable on Linux via /proc/meminfo; guard so a
        # non-Linux host (or a sandbox) simply yields None rather than erroring.
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    facet["mem_available_mb"] = round(kb / 1024, 1)
                    break
    except Exception:
        facet["mem_available_mb"] = None
    return facet


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get(
        "/api/environment",
        response_model=EnvironmentBrief,
        summary="Consolidated environment + optimization brief for a running job",
        description=(
            "Read-only snapshot of the environment a gludd job runs inside — model "
            "roster (NO secrets), routing policy, budget posture, compute options, "
            "invocable tools/skills, queue depth, host system facts — plus an "
            "optimization advisor that recommends profiles per work-type and flags "
            "budget pressure. Every sub-section fails soft to a safe empty/null "
            "default; the handler never returns a 500."
        ),
    )
    async def api_environment() -> EnvironmentBrief:
        # Each facet is independently guarded; a failure in one yields its safe
        # default rather than failing the whole request.
        try:
            models = _models_facet(app)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("models section failed: %s", exc)
            models = []
        try:
            routing = _routing_facet(app)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("routing section failed: %s", exc)
            routing = {}
        try:
            budget = _budget_facet(app)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("budget section failed: %s", exc)
            budget = {}
        try:
            compute = _compute_facet(app)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("compute section failed: %s", exc)
            compute = {}
        try:
            tools = await _tools_facet(app)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("tools section failed: %s", exc)
            tools = list(_ANSIBLE_TOOL_MODULES)
        try:
            skills = _skills_facet(app)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("skills section failed: %s", exc)
            skills = []
        try:
            queues = await _queues_facet(app)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("queues section failed: %s", exc)
            queues = []
        try:
            system = _system_facet()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("system section failed: %s", exc)
            system = {}
        # The advisor's flakiness hints need the live MetricsCollector; it is
        # published on app.state by _get_or_create_extended_subsystems as
        # _metrics_collector. Resolve it defensively here (the only non-pure
        # input handed to the otherwise-pure advisor) — a missing collector
        # simply omits the model_flaky hints.
        metrics_collector = getattr(app.state, "_metrics_collector", None)
        try:
            optimization = build_optimization_hints(
                models=models,
                routing=routing,
                budget=budget,
                metrics_collector=metrics_collector,
            )
        except Exception as exc:  # pragma: no cover - defensive (advisor is pure)
            logger.debug("optimization section failed: %s", exc)
            optimization = {"hints": [], "recommended_profile_for": {}}

        return EnvironmentBrief(
            models=models,
            routing=routing,
            budget=budget,
            compute=compute,
            tools=tools,
            skills=skills,
            queues=queues,
            system=system,
            optimization=optimization,
        )

    @app.get(
        "/api/environment/advise",
        response_model=AdviceBrief,
        summary="Per-task model + workflow recommendation for a running job",
        description=(
            "Read-only, per-task advice: given a work_type (and optional "
            "prompt_tokens / priority) it returns the recommended model "
            "profile (via the adaptive router), a projected cost, whether to "
            "drive the multi-step langgraph workflow vs a single-shot router "
            "call, and resource hints (prefer_local / budget_ok / "
            "budget_warning / context_fits). On insufficient history or any "
            "internal error it returns a safe fallback recommendation and "
            "never a 500. Sits behind the same PSK middleware as "
            "/api/environment."
        ),
    )
    async def api_environment_advise(
        # work_type is reflected into the response (task_type / workflow_reason)
        # so cap its length to bound the response and avoid echoing an
        # unbounded operator-supplied string (NEW-ENV-1).
        work_type: Annotated[str, Query(max_length=64)],
        # prompt_tokens feeds the cost projection; a negative value would yield a
        # negative est_cost_usd and silently suppress the budget warning
        # (NEW-ENV-2). Bound it to [0, 2_000_000] — larger than any real context
        # window, small enough to keep the multiply trivial.
        prompt_tokens: Annotated[int | None, Query(ge=0, le=2_000_000)] = None,
        priority: Annotated[str, Query(max_length=16)] = "quality",
    ) -> AdviceBrief:
        # priority is a soft hint; clamp unknown values to the safe default.
        prio = (priority or "quality").strip().lower()
        if prio not in ("cost", "quality", "latency"):
            prio = "quality"
        try:
            advice = await _resolve_advice(
                app,
                work_type=work_type,
                prompt_tokens=prompt_tokens,
                priority=prio,
            )
        except Exception as exc:  # pragma: no cover - _resolve_advice is guarded
            logger.debug("advise route failed: %s", exc)
            from general_ludd.controllers.environment_advisor import (
                _advice_fallback,
            )

            advice = _advice_fallback(work_type)
        return AdviceBrief(**advice)
