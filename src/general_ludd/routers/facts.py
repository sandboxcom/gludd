"""Facts aggregation API: GET /api/facts (+ focused /api/metrics, /api/traces).

Read-only structured snapshot for playbook logic. Branches in playbooks can
key off `gludd.work.*`, `gludd.todos.*`, `gludd.models.*`, `gludd.history.*`,
`gludd.messages.*`, `gludd.metrics.*`, and `gludd.traces.*` (the latter
injected via the gludd_facts / gludd_metrics / gludd_traces modules).

This endpoint REUSES existing repositories/collectors — it does not duplicate
stat logic:
  - work     -> TaskReturnRepository.work_summary (in-flight/claimed by status)
  - todos    -> TodoRepository.status_summary (counts, oldest age, backlog)
  - models   -> MetricsCollector.get_global_model_usage + model_routing config
  - history  -> TaskReturnRepository.history_summary (success/failure rates)
  - messages -> AgentMessageRepository.unread_counts (per-recipient unread)
  - metrics  -> MetricsCollector.get_full_report / get_global_model_usage /
                get_cost_by_project + BenchmarkRepository.get_aggregate_scores
  - traces   -> RecentTracesBuffer.snapshot (genuinely-captured in-process
                traces) + OTelBridge exporter status

PSK auth is applied by the daemon middleware (path is not public).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from general_ludd.db.repository import (
    AgentMessageRepository,
    BenchmarkRepository,
    TaskReturnRepository,
    TodoRepository,
)

logger = logging.getLogger(__name__)

# Bounded caps so facts stay usable in playbook when:/vars: conditions.
_DEFAULT_TRACE_LIMIT = 20
_DEFAULT_SPAN_CAP = 25
_DEFAULT_RANKING_LIMIT = 10


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


def _models_facet(app: FastAPI) -> dict[str, Any]:
    """Configured routing + per-model usage/health from the live MetricsCollector."""
    facet: dict[str, Any] = {"routing": {}, "usage": {}}
    startup_config = getattr(app.state, "_startup_config", {}) or {}
    routing = startup_config.get("model_routing")
    if routing is not None:
        facet["routing"] = {
            "default_profile": getattr(routing, "default_profile", None),
            "weak_model_profile": getattr(routing, "weak_model_profile", None),
            "role_routing": dict(getattr(routing, "role_routing", {}) or {}),
            "fallback_chain": list(getattr(routing, "fallback_chain", []) or []),
        }
    collector = getattr(app.state, "_metrics_collector", None)
    if collector is not None and hasattr(collector, "get_global_model_usage"):
        usage = collector.get_global_model_usage()
        facet["usage"] = {
            mid: {
                "total_calls": u.total_calls,
                "successful_calls": u.successful_calls,
                "failed_calls": u.failed_calls,
                "success_rate": u.success_rate,
                "total_cost_usd": u.total_cost_usd,
            }
            for mid, u in usage.items()
        }
    return facet


async def _metrics_facet(
    app: FastAPI,
    project_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Deeper metrics: agent-level report, global model usage, per-project cost,
    and benchmark rankings (when a BenchmarkRepository is reachable).

    Reuses MetricsCollector / BenchmarkRepository — no stat logic is duplicated.
    """
    facet: dict[str, Any] = {
        "agents": [],
        "total_agents": 0,
        "running_agents": 0,
        "global_model_usage": {},
        "cost_by_project": {},
        "benchmark_rankings": [],
    }
    collector = getattr(app.state, "_metrics_collector", None)
    if collector is not None and hasattr(collector, "get_full_report"):
        report = collector.get_full_report()
        agents = report.get("agents", [])
        if agent_id is not None:
            agents = [a for a in agents if a.get("agent_id") == agent_id]
        elif project_id is not None:
            agents = [a for a in agents if a.get("project") == project_id]
        facet["agents"] = agents
        facet["total_agents"] = report.get("total_agents", 0)
        facet["running_agents"] = report.get("running_agents", 0)
        facet["global_model_usage"] = report.get("global_model_usage", {})
    if collector is not None and hasattr(collector, "get_cost_by_project"):
        cost_by_project = collector.get_cost_by_project()
        if project_id is not None:
            cost_by_project = {
                k: v for k, v in cost_by_project.items() if k == project_id
            }
        facet["cost_by_project"] = cost_by_project

    factory = _get_session_factory(app)
    if factory is not None:
        try:
            repo = BenchmarkRepository(session_factory=factory)
            rankings = await repo.get_aggregate_scores()
            rankings.sort(
                key=lambda r: r.get("composite_score") or 0.0, reverse=True
            )
            facet["benchmark_rankings"] = rankings[:_DEFAULT_RANKING_LIMIT]
        except Exception as exc:
            logger.debug("benchmark rankings unavailable: %s", exc)
    return facet


def _traces_facet(
    app: FastAPI,
    limit: int = _DEFAULT_TRACE_LIMIT,
    todo_id: str | None = None,
) -> dict[str, Any]:
    """Recent execution traces + by-phase aggregate + otel exporter status.

    Sourced ONLY from the in-process RecentTracesBuffer (genuinely-captured
    telemetry). The otel exporter status is reported honestly: "available" when
    an OTLP collector bridge is active, otherwise "disabled".
    """
    buffer = getattr(app.state, "_recent_traces", None)
    facet: dict[str, Any]
    if buffer is not None and hasattr(buffer, "snapshot"):
        facet = buffer.snapshot(
            limit=limit, max_spans=_DEFAULT_SPAN_CAP, todo_id=todo_id
        )
    else:
        facet = {"count": 0, "total_recorded": 0, "recent": [], "by_phase": {}}

    otel_bridge = getattr(app.state, "_otel_bridge", None)
    if otel_bridge is not None and getattr(otel_bridge, "is_available", None):
        facet["otel_exporter_status"] = (
            "available" if otel_bridge.is_available() else "disabled"
        )
    else:
        facet["otel_exporter_status"] = "disabled"
    return facet


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get("/api/facts")
    async def api_facts(project_id: str | None = None) -> dict[str, Any]:
        work: dict[str, Any] = {}
        todos: dict[str, Any] = {}
        history: dict[str, Any] = {}
        messages: dict[str, Any] = {}

        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                todo_repo = TodoRepository(session)
                tr_repo = TaskReturnRepository(session)
                msg_repo = AgentMessageRepository(session)
                todos = await todo_repo.status_summary(project_id=project_id)
                work = await tr_repo.work_summary(project_id=project_id)
                history = await tr_repo.history_summary(project_id=project_id)
                unread = await msg_repo.unread_counts(project_id=project_id)
                messages = {"unread_by_recipient": unread, "total_unread": sum(unread.values())}

        return {
            "work": work,
            "todos": todos,
            "models": _models_facet(app),
            "history": history,
            "messages": messages,
            "metrics": await _metrics_facet(app, project_id=project_id),
            "traces": _traces_facet(app),
            "project_id": project_id,
        }

    @app.get("/api/metrics")
    async def api_metrics(
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Focused read-only metrics snapshot (gludd_metrics module).

        Optional filters: ``project_id`` (per-project agents/cost) and
        ``agent_id`` (single agent).
        """
        return await _metrics_facet(app, project_id=project_id, agent_id=agent_id)

    @app.get("/api/traces")
    async def api_traces(
        todo_id: str | None = None,
        limit: int = _DEFAULT_TRACE_LIMIT,
    ) -> dict[str, Any]:
        """Focused read-only traces snapshot (gludd_traces module).

        Optional filters: ``todo_id`` and ``limit`` (max recent traces).
        """
        bounded = max(1, min(limit, _DEFAULT_TRACE_LIMIT * 5))
        return _traces_facet(app, limit=bounded, todo_id=todo_id)
