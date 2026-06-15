"""Per-project accounting API.

GET /api/accounting            — accounting snapshot for ALL known projects
GET /api/accounting/{project_id} — accounting snapshot for ONE project

PSK auth is applied by the daemon middleware (path is not public).

Data sources are wired up from live daemon state:
  - usage records: MetricsCollector.get_cost_by_project + get_full_report
  - todos:         TodoRepository.list_all (project-filtered)
  - role runs:     RoleRunRepository.list_all (project-filtered)
  - loc_changed:   injected as 0 until git-diff --numstat integration lands
  - project list:  ProjectManager.list_active

# TODO(integration): wire loc_changed to a real git-diff --numstat source
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.accounting.ledger import Accountant, ProjectAccounting

logger = logging.getLogger(__name__)


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


def _accounting_to_dict(result: ProjectAccounting) -> dict[str, Any]:
    return asdict(result)


async def _build_accountant(app: FastAPI, quota_usd: float = 0.0) -> Accountant:
    """Wire live daemon state into an Accountant ready for aggregation.

    Usage provider: pulls from MetricsCollector cost_by_project + agent
    reports where available, falling back to empty lists.
    Todo/role providers: hit the DB session factory synchronously via
    cached lists fetched once before building the Accountant.
    """

    # 1. Fetch usage records (from MetricsCollector)
    usage_by_project: dict[str, list[Any]] = {}
    collector = getattr(app.state, "_metrics_collector", None)
    if collector is not None:
        try:
            if hasattr(collector, "get_full_report"):
                report = collector.get_full_report()
                for agent_info in report.get("agents", []):
                    pid = agent_info.get("project") or agent_info.get("project_id") or ""
                    if not pid:
                        continue

                    class _UsageRecord:
                        def __init__(self, pid: str, info: dict[str, Any]) -> None:
                            self.project_id = pid
                            mu = info.get("model_usage", {})
                            # Sum across all models for this agent
                            self.tokens_used = sum(
                                int(u.get("total_calls", 0)) * 1000
                                for u in (mu.values() if isinstance(mu, dict) else [])
                            )
                            self.usd_spent = float(info.get("total_cost_usd", 0.0))
                            self.elapsed_seconds = float(info.get("run_time_seconds", 0.0))

                    usage_by_project.setdefault(pid, []).append(_UsageRecord(pid, agent_info))
        except Exception as exc:
            logger.debug("MetricsCollector usage unavailable: %s", exc)

    # 2. Fetch todos and role runs from DB (once, eagerly, into plain lists)
    todo_by_project: dict[str, list[Any]] = {}
    role_by_project: dict[str, list[Any]] = {}

    factory = _get_session_factory(app)
    if factory is not None:
        try:
            from general_ludd.db.repository import RoleRunRepository, TodoRepository

            async with factory() as session:
                todo_repo = TodoRepository(session)
                role_repo = RoleRunRepository(session)
                all_todos = await todo_repo.list_all()
                all_roles = await role_repo.list_all()
            for t in all_todos:
                pid = t.project_id or ""
                # Map ORM status to accounting buckets
                raw_status = str(t.status)
                if raw_status in ("backlog", "queued", "blocked", "failed", "needs_more_work"):
                    bucket = "pending"
                elif raw_status in ("active", "reviewing_return", "manual_hold", "awaiting_result"):
                    bucket = "in_progress"
                elif raw_status == "complete":
                    bucket = "done"
                else:
                    bucket = raw_status

                class _TodoRecord:
                    def __init__(self, pid: str, status: str, points: int) -> None:
                        self.project_id = pid
                        self.status = status
                        self.points = points

                todo_by_project.setdefault(pid, []).append(
                    _TodoRecord(pid, bucket, int(getattr(t, "priority", 0)))
                )
            for r in all_roles:
                pid = r.project_id or ""

                class _RoleRecord:
                    def __init__(self, pid: str, role: str) -> None:
                        self.project_id = pid
                        self.role = role

                role_by_project.setdefault(pid, []).append(_RoleRecord(pid, r.role))
        except Exception as exc:
            logger.debug("DB providers unavailable: %s", exc)

    # 3. Project list from ProjectManager
    known_projects: list[str] = []
    pm = getattr(app.state, "_project_manager", None)
    if pm is not None:
        try:
            known_projects = [
                getattr(p, "project_id", str(p)) for p in pm.list_active()
            ]
        except Exception as exc:
            logger.debug("ProjectManager unavailable: %s", exc)

    # 4. Build callables
    def _usage_provider(pid: str) -> list[Any]:
        return usage_by_project.get(pid, [])

    def _todo_provider(pid: str) -> list[Any]:
        return todo_by_project.get(pid, [])

    def _role_provider(pid: str) -> list[Any]:
        return role_by_project.get(pid, [])

    def _loc_provider(pid: str) -> int:
        # TODO(integration): wire loc_changed to a real git-diff --numstat source
        return 0

    def _project_provider() -> list[str]:
        return known_projects

    return Accountant(
        usage_provider=_usage_provider,
        todo_provider=_todo_provider,
        role_provider=_role_provider,
        loc_provider=_loc_provider,
        project_provider=_project_provider,
        quota_usd=quota_usd,
    )


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get("/api/accounting")
    async def api_accounting_all() -> list[dict[str, Any]]:
        """Return accounting snapshots for all active projects."""
        accountant = await _build_accountant(app)
        results = accountant.account_all()
        return [_accounting_to_dict(r) for r in results]

    @app.get("/api/accounting/{project_id}")
    async def api_accounting_project(project_id: str) -> dict[str, Any]:
        """Return an accounting snapshot for a single project."""
        accountant = await _build_accountant(app)
        try:
            result = accountant.account_for(project_id)
        except Exception as exc:
            logger.warning("Accounting failed for %s: %s", project_id, exc)
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}") from exc
        return _accounting_to_dict(result)
