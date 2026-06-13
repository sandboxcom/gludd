"""Facts aggregation API: GET /api/facts.

Read-only structured snapshot for playbook logic. Branches in playbooks can
key off `gludd.work.*`, `gludd.todos.*`, `gludd.models.*`, `gludd.history.*`,
and `gludd.messages.*` (the latter injected via the gludd_facts module).

This endpoint REUSES existing repositories/collectors — it does not duplicate
stat logic:
  - work     -> TaskReturnRepository.work_summary (in-flight/claimed by status)
  - todos    -> TodoRepository.status_summary (counts, oldest age, backlog)
  - models   -> MetricsCollector.get_global_model_usage + model_routing config
  - history  -> TaskReturnRepository.history_summary (success/failure rates)
  - messages -> AgentMessageRepository.unread_counts (per-recipient unread)

PSK auth is applied by the daemon middleware (path is not public).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from general_ludd.db.repository import (
    AgentMessageRepository,
    TaskReturnRepository,
    TodoRepository,
)

logger = logging.getLogger(__name__)


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
            "project_id": project_id,
        }
