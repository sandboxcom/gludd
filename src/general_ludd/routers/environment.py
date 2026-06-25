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
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from general_ludd.controllers.environment_advisor import build_optimization_hints
from general_ludd.routers.facts import _spend_facet

logger = logging.getLogger(__name__)

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
        try:
            optimization = build_optimization_hints(
                models=models, routing=routing, budget=budget
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
