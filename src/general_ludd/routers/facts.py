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

import asyncio
import logging
import os
import shutil
import subprocess
from typing import Any, cast

from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from general_ludd.db.repository import (
    AgentMessageRepository,
    BenchmarkRepository,
    TaskReturnRepository,
    TodoRepository,
)
from general_ludd.routers._util import get_session_factory as _get_session_factory
from general_ludd.routers.accounting import _build_accountant as _build_accounting_accountant
from general_ludd.routers.coordination import _coordination_facet

logger = logging.getLogger(__name__)

# Bounded caps so facts stay usable in playbook when:/vars: conditions.
_DEFAULT_TRACE_LIMIT = 20
_DEFAULT_SPAN_CAP = 25
_DEFAULT_RANKING_LIMIT = 10


def _resolve_trace_project_id(request: Any, query_project_id: str | None) -> str | None:
    """Derive the effective project_id for trace / metrics queries.

    XT-3/XT-4 cross-tenant fix: when the auth middleware stamps
    ``request.state.project_id`` from a project-scoped bearer token
    (``project_id:psk`` format), the auth-derived scope ALWAYS wins —
    the caller-supplied ``?project_id=`` query param is untrusted and
    ignored. When ``request.state.project_id`` is absent (legacy global
    PSK, back-compat), the query param is used as-is.
    """
    scope: str | None = cast(str | None, getattr(request.state, "project_id", None))
    if scope is not None:
        return scope
    return query_project_id


def _models_facet(app: FastAPI) -> dict[str, object]:
    """Configured routing + per-model usage/health from the live MetricsCollector."""
    facet: dict[str, object] = {"routing": {}, "usage": {}}
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
) -> dict[str, object]:
    """Deeper metrics: agent-level report, global model usage, per-project cost,
    and benchmark rankings (when a BenchmarkRepository is reachable).

    Reuses MetricsCollector / BenchmarkRepository — no stat logic is duplicated.
    """
    facet: dict[str, object] = {
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
            # Tenant isolation (XT-1): scope benchmark aggregation to the caller's
            # project so rankings don't aggregate BenchmarkResultModel rows across
            # tenants. project_id is None for unscoped/global callers.
            rankings = await repo.get_aggregate_scores(project_id=project_id)
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
    project_id: str | None = None,
) -> dict[str, object]:
    """Recent execution traces + by-phase aggregate + otel exporter status.

    Sourced ONLY from the in-process RecentTracesBuffer (genuinely-captured
    telemetry). The otel exporter status is reported honestly: "available" when
    an OTLP collector bridge is active, otherwise "disabled".

    ``project_id`` scopes the traces to the caller's tenant (XT trace-leak fix):
    when supplied, only that project's traces are returned and legacy
    None-project traces are excluded, so trace names / phases / costs / tokens
    of other tenants never leak. ``None`` = unscoped/global caller.
    """
    buffer = getattr(app.state, "_recent_traces", None)
    facet: dict[str, object]
    if buffer is not None and hasattr(buffer, "snapshot"):
        facet = buffer.snapshot(
            limit=limit,
            max_spans=_DEFAULT_SPAN_CAP,
            todo_id=todo_id,
            project_id=project_id,
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


def _codebase_facet(
    app: FastAPI,
    recent_failures: dict[str, object] | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """Best-effort codebase self-knowledge for the self-improvement pipeline.

    Composed from genuinely-capturable signals via CodebaseIntrospector: git
    churn, stdlib-ast complexity, parsed coverage.xml / .gate-status debt,
    harness dead-code/missing-test findings, the live in-process trace buffer's
    by-phase perf cost, and (when available) the DB task-return history passed
    in as ``recent_failures``. Each facet is ``None`` when its source is absent
    — nothing is fabricated. The repo root is read from
    ``app.state._repo_root`` and falls back to the process cwd.
    """
    from general_ludd.code_intelligence.introspect import CodebaseIntrospector

    repo_root = getattr(app.state, "_repo_root", None) or os.getcwd()
    buffer = getattr(app.state, "_recent_traces", None)
    try:
        introspector = CodebaseIntrospector(
            repo_root=str(repo_root),
            traces_buffer=buffer,
            recent_failures=recent_failures,
            # Scope the perf_cost trace aggregate to the caller's tenant so the
            # codebase facet cannot re-leak cross-tenant trace cost/tokens.
            project_id=project_id,
        )
        return introspector.snapshot()
    except Exception as exc:  # pragma: no cover - defensive, fail soft
        logger.debug("codebase facet unavailable: %s", exc)
        return {
            "churn": None,
            "complexity": None,
            "coverage": None,
            "debt": None,
            "dead_code": None,
            "missing_tests": None,
            "perf_cost": None,
            "recent_failures": recent_failures,
        }


async def _features_facet(app: FastAPI, project_id: str | None = None) -> dict[str, object]:
    """Feature-database summary: counts by status + list of feature names per status.

    Sourced from FeatureRepository — never self-asserted.
    """
    from general_ludd.db.models import FeatureStatus
    from general_ludd.db.repository import FeatureRepository

    factory = _get_session_factory(app)
    facet: dict[str, object] = {
        "total": 0,
        "by_status": {},
        "verified": [],
        "implemented": [],
        "requested": [],
        "regressed": [],
    }
    if factory is None:
        return facet
    try:
        async with factory() as session:
            # XT-2: scope the feature facet to the requested project so the facts
            # summary cannot count/list another tenant's features. project_id was
            # accepted but silently dropped before this scoping. None = unscoped.
            repo = (
                FeatureRepository.scoped(session, project_id)
                if project_id is not None
                else FeatureRepository(session)
            )
            rows = await repo.list_all()
        by_status: dict[str, list[str]] = {}
        for row in rows:
            status = row.status
            by_status.setdefault(status, []).append(row.name)
        by_status_counts: dict[str, int] = {}
        for status in FeatureStatus:
            names = by_status.get(status.value, [])
            by_status_counts[status.value] = len(names)
            facet[status.value] = names
        facet["by_status"] = by_status_counts
        facet["total"] = len(rows)
    except Exception as exc:
        logger.debug("features facet unavailable: %s", exc)
    return facet
def _spend_facet(app: FastAPI) -> dict[str, object]:
    """Current rolling-window spend summary for the spend-limiter subsystem.

    Returns a dict suitable for embedding in /api/facts under the ``"spend"``
    key.  When no limiter is active the values are safe defaults.
    """
    limiter = getattr(app.state, "_spend_limiter", None)
    if limiter is None:
        return {
            "limiter_active": False,
            "window_spend_usd": 0.0,
            "limit_usd": None,
            "remaining_usd": None,
            "window_seconds": None,
        }
    return {
        "limiter_active": True,
        "window_spend_usd": limiter.window_spend(),
        "limit_usd": limiter._limit_usd,
        "remaining_usd": limiter.remaining(),
        "window_seconds": limiter._window_seconds,
    }
async def _accounting_facet(
    app: FastAPI,
    project_id: str | None = None,
) -> dict[str, object]:
    """Per-project accounting snapshot(s) for playbook consumption.

    When ``project_id`` is supplied, returns a single-project result under
    ``{"project": <snapshot>}``.  Otherwise returns all active projects
    under ``{"projects": [<snapshot>, ...]}``.

    Reuses the Accountant from routers/accounting — no stat logic is
    duplicated.
    """
    try:
        import asyncio as _asyncio

        accountant = await _build_accounting_accountant(app)
        if project_id is not None:
            result = await _asyncio.to_thread(accountant.account_for, project_id)
            from dataclasses import asdict

            return {"project": asdict(result)}
        results = await _asyncio.to_thread(accountant.account_all)
        from dataclasses import asdict

        return {"projects": [asdict(r) for r in results]}
    except Exception as exc:
        # Do not leak internal exception detail to the client: log the real
        # error (with traceback) for operators, return a generic message in the
        # response body. The "error" key is kept so callers can still detect the
        # degraded state without parsing HTTP status.
        logger.warning("accounting facet unavailable: %s", exc, exc_info=True)
        return {"projects": [], "error": "accounting facet unavailable"}
def _osquery_facet(app: FastAPI) -> dict[str, object]:
    """Fast availability + version probe for osquery system-state querying.

    This is intentionally a *cheap* probe (``osqueryi --version`` with a short
    timeout) so it never blocks /api/facts on a real query. It fails soft to
    ``{"available": false}`` when the binary is absent or the probe errors.

    The osqueryi binary is resolved from the daemon's binary filestore
    (``binaries/osquery``, downloaded on first use) and then from the system
    ``PATH``. A heavier query-as-corpus search path is a later phase that
    consumes this availability signal.
    """
    facet: dict[str, object] = {"available": False, "version": None, "source": None, "path": None}

    binary: str | None = None
    source: str | None = None
    try:
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        store = getattr(app.state, "_filestore", None)
        boot = BinaryBootstrapper(store=store) if store is not None else BinaryBootstrapper()
        path = boot.get_binary_path("osquery")
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            binary = path
            source = "filestore"
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("osquery filestore probe unavailable: %s", exc)

    if binary is None:
        on_path = shutil.which("osqueryi")
        if on_path:
            binary = on_path
            source = "path"

    if binary is None:
        return facet

    facet["path"] = binary
    facet["source"] = source
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0:
            facet["available"] = True
            facet["version"] = (proc.stdout or "").strip() or None
    except Exception as exc:  # fail soft, never block facts
        logger.debug("osquery version probe failed: %s", exc)
    return facet


def _schedule_facet(app: FastAPI) -> dict[str, object]:
    """Last computed schedule plan and in-flight batch summary.

    Populated by POST /api/schedule; returns an empty placeholder when no plan
    has been computed yet in this daemon lifetime.
    """
    last_plan = getattr(app.state, "_schedule_last_plan", None)
    if last_plan is None:
        return {
            "last_plan": None,
            "batch_count": 0,
            "item_count": 0,
        }
    batches: list[list[str]] = last_plan.get("batches", [])
    items: list[dict[str, object]] = last_plan.get("items", [])
    return {
        "last_plan": last_plan,
        "batch_count": len(batches),
        "item_count": len(items),
    }


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    @app.get(
        "/api/facts",
        summary="Get consolidated daemon facts for playbooks",
        description=(
            "Unified read-only snapshot of daemon state: work queue, todos, "
            "model profiles, routing, budget, metrics, traces, codebase "
            "intelligence, features, accounting, scheduling, coordination. "
            "PSK-authenticated."
        ),
    )
    async def api_facts(request: Request, project_id: str | None = None) -> dict[str, object]:
        scope = _resolve_trace_project_id(request, project_id)
        work: dict[str, object] = {}
        todos: dict[str, object] = {}
        history: dict[str, object] = {}
        messages: dict[str, object] = {}

        factory = _get_session_factory(app)
        if factory is not None:
            try:
                async with factory() as session:
                    todo_repo = TodoRepository(session)
                    tr_repo = TaskReturnRepository(session)
                    msg_repo = AgentMessageRepository(session)
                    todos = await todo_repo.status_summary(project_id=scope)
                    work = await tr_repo.work_summary(project_id=scope)
                    history = await tr_repo.history_summary(project_id=scope)
                    unread = await msg_repo.unread_counts(project_id=scope)
                    messages = {
                        "unread_by_recipient": unread,
                        "total_unread": sum(unread.values()),
                    }
            except (SQLAlchemyError, ConnectionError, OSError, TimeoutError) as exc:
                # A daemon can be healthy before its optional external database
                # is reachable. Keep non-database facts available instead of
                # turning a read-only status snapshot into HTTP 500.
                logger.warning("facts database facets unavailable: %s", exc)

        dispatch_facet_fn = getattr(app.state, "_dispatch_facet", None)
        dispatch: dict[str, object] = (
            dispatch_facet_fn() if callable(dispatch_facet_fn) else {}
        )
        return {
            "work": work,
            "todos": todos,
            "models": _models_facet(app),
            "history": history,
            "messages": messages,
            "metrics": await _metrics_facet(app, project_id=scope),
            "traces": _traces_facet(app, project_id=scope),
            "codebase": await asyncio.to_thread(
                _codebase_facet,
                app,
                recent_failures=history or None,
                project_id=scope,
            ),
            "features": await _features_facet(app, project_id=scope),
            "dispatch": dispatch,
            "spend": _spend_facet(app),
            "accounting": await _accounting_facet(app, project_id=scope),
            "schedule": _schedule_facet(app),
            "coordination": _coordination_facet(app),
            "osquery": await asyncio.to_thread(_osquery_facet, app),
            "project_id": scope,
        }

    @app.get("/api/metrics")
    async def api_metrics(
        request: Request,
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, object]:
        """Focused read-only metrics snapshot (gludd_metrics module).

        Optional filters: ``project_id`` (per-project agents/cost) and
        ``agent_id`` (single agent). The effective ``project_id`` scope is
        resolved via ``_resolve_trace_project_id``: when the auth middleware
        stamps ``request.state.project_id`` from a scoped bearer token it
        ALWAYS wins over the query param (XT cross-tenant fix). Unscoped
        callers (legacy global PSK) retain the query-param behaviour.
        """
        scope = _resolve_trace_project_id(request, project_id)
        return await _metrics_facet(app, project_id=scope, agent_id=agent_id)

    @app.get("/api/traces")
    async def api_traces(
        request: Request,
        todo_id: str | None = None,
        limit: int = _DEFAULT_TRACE_LIMIT,
        project_id: str | None = None,
    ) -> dict[str, object]:
        """Focused read-only traces snapshot (gludd_traces module).

        Optional filters: ``todo_id``, ``limit`` (max recent traces). The
        effective ``project_id`` scope is resolved via
        ``_resolve_trace_project_id``: when the auth middleware stamps
        ``request.state.project_id`` from a scoped bearer token it ALWAYS
        wins over the query param (XT-3/XT-4 cross-tenant fix). Unscoped
        callers (legacy global PSK) retain the query-param behaviour.
        """
        bounded = max(1, min(limit, _DEFAULT_TRACE_LIMIT * 5))
        scope = _resolve_trace_project_id(request, project_id)
        if scope is None:
            raise HTTPException(
                status_code=400,
                detail="project_id is required for /api/traces — "
                "supply a ?project_id= query parameter or use a project-scoped bearer token",
            )
        return _traces_facet(
            app, limit=bounded, todo_id=todo_id, project_id=scope
        )
