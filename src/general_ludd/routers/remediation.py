"""HTTP router: remediation system endpoints.

PSK-gated (admin-only). Surfaces:

  - ``GET  /admin/remediation/scan?project_id=...``
        Run the detector once and return the findings (no remediation).
  - ``POST /admin/remediation/remediate?project_id=...``
        Run the detector once AND apply remediation for each finding.
  - ``GET  /admin/remediation/chronic-blockers?project_id=...``
        Structured chronic-blocker report.
  - ``GET  /admin/remediation/history?project_id=...&since=...``
        Audit trail of past remediation actions.
  - ``GET  /admin/remediation/config``
        Current RemediationConfig thresholds.

All endpoints require the daemon PSK (admin middleware enforces it on every
``/admin/*`` path).
"""

from __future__ import annotations

import json as _json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import RemediationActionModel
from general_ludd.db.repository import (
    HumanTodoRepository,
    RemediationActionRepository,
    TodoRepository,
)
from general_ludd.remediation.blocker_detector import (
    BlockedTask,
    BlockerDetector,
    RemediationConfig,
)
from general_ludd.remediation.dispatcher import (
    RemediationAction,
    RemediationDispatcher,
)
from general_ludd.remediation.reporter import chronic_blocker_report

logger = logging.getLogger(__name__)


PSK_BYPASS_ROUTES = frozenset(
    {"/admin/remediation/scan", "/admin/remediation/remediate"}
)


def _get_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession] | None:
    return getattr(app.state, "_session_factory", None)


def _blocked_task_to_dict(b: BlockedTask) -> dict[str, object]:
    return {
        "todo_id": b.todo_id,
        "project_id": b.project_id,
        "blocked_at": b.blocked_at.isoformat() if b.blocked_at else None,
        "blocked_duration_seconds": b.blocked_duration_seconds,
        "blocker_kind": b.blocker_kind,
        "blocker_summary": b.blocker_summary,
        "suggested_remediation": b.suggested_remediation,
        "linked_human_todo_id": b.linked_human_todo_id,
        "task_type": b.task_type,
    }


def _action_to_dict(a: RemediationAction) -> dict[str, object]:
    return {
        "kind": str(a.kind.value) if hasattr(a.kind, "value") else str(a.kind),
        "blocked_todo_id": a.blocked_todo_id,
        "project_id": a.project_id,
        "ok": a.ok,
        "summary": a.summary,
        "detail": a.detail,
        "reason": a.reason,
    }


def _remediation_row_to_dict(row: RemediationActionModel) -> dict[str, object]:
    try:
        detail = _json.loads(row.detail or "{}")
    except Exception:
        detail = {}
    return {
        "id": row.id,
        "blocked_todo_id": row.blocked_todo_id,
        "project_id": row.project_id,
        "blocker_kind": row.blocker_kind,
        "action_kind": row.action_kind,
        "summary": row.summary,
        "detail": detail,
        "ok": row.ok,
        "reason": row.reason,
        "created_at": str(row.created_at) if row.created_at else None,
    }


def _config_to_dict(cfg: RemediationConfig) -> dict[str, object]:
    return {
        "human_input_block_hours": cfg.human_input_block_hours,
        "permission_escalation_block_hours": cfg.permission_escalation_block_hours,
        "max_requeues_before_chronic": cfg.max_requeues_before_chronic,
        "chronic_lookback_days": cfg.chronic_lookback_days,
        "min_chronic_incidents": cfg.min_chronic_incidents,
        "retry_delay_hours": cfg.retry_delay_hours,
    }


def _get_remediation_config(daemon_state: dict[str, object]) -> RemediationConfig:
    """Single config-source read for every ``/admin/remediation/*`` handler (#52).

    ``daemon_state["remediation_config"]`` is populated once at daemon
    startup (``daemon.py``'s ``_remediation_config_from_uc``) from
    ``UserConfig.remediation`` — the SAME instance the EventLoop tick phase
    (``_phase_remediate_blocked_tasks``) reads via ``self._daemon_state``.
    Centralized here (rather than each handler repeating the cast+fallback)
    so the HTTP path and the tick path can never disagree on where the
    config comes from. Falls back to ``RemediationConfig()`` defaults when
    unset (e.g. a bare test app) or of the wrong type.
    """
    cfg = daemon_state.get("remediation_config")
    return cfg if isinstance(cfg, RemediationConfig) else RemediationConfig()


def _build_detector(
    app: FastAPI, project_id: str | None, config: RemediationConfig | None = None
) -> BlockerDetector:
    factory = _get_session_factory(app)
    if factory is None:
        raise HTTPException(
            status_code=503,
            detail="session factory not wired; daemon is not ready",
        )
    # NOTE: we instantiate the detector without live repos here; the scan
    # path opens its own session and constructs repos inside. This keeps the
    # detector stateless across requests.
    return BlockerDetector(
        todo_repo=None,
        human_todo_repo=None,
        event_log_repo=None,
        config=config,
        session=None,
    )


_T = TypeVar("_T")


async def _run_with_session(
    app: FastAPI, coro_factory: Callable[..., Awaitable[_T]]
) -> _T:
    """Open a fresh session from app.state._session_factory, run, commit."""
    factory = _get_session_factory(app)
    if factory is None:
        raise HTTPException(
            status_code=503,
            detail="session factory not wired; daemon is not ready",
        )
    async with factory() as session:
        from general_ludd.db.repository import (
            HumanTodoRepository,
            TodoRepository,
        )

        result = await coro_factory(
            session=session,
            todo_repo=TodoRepository(session),
            human_todo_repo=HumanTodoRepository(session),
        )
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return result


class RemediationConfigResponse(BaseModel):
    human_input_block_hours: int
    permission_escalation_block_hours: int
    max_requeues_before_chronic: int
    chronic_lookback_days: int
    min_chronic_incidents: int
    retry_delay_hours: int


def register(app: FastAPI, daemon_state: dict[str, object]) -> None:
    @app.get("/admin/remediation/scan", response_model=None)
    async def get_scan(project_id: str | None = Query(default=None)) -> dict[str, object]:
        """Run the detector once; return findings without applying remediation."""
        cfg = _get_remediation_config(daemon_state)

        async def _run(
            *,
            session: AsyncSession,
            todo_repo: TodoRepository,
            human_todo_repo: HumanTodoRepository,
        ) -> dict[str, object]:
            det = BlockerDetector(
                todo_repo=todo_repo,
                human_todo_repo=human_todo_repo,
                config=cfg,
                session=session,
            )
            findings = await det.scan(project_id=project_id)
            return {
                "project_id": project_id,
                "blocked_tasks": [_blocked_task_to_dict(b) for b in findings],
                "total": len(findings),
            }

        return await _run_with_session(app, _run)

    @app.post("/admin/remediation/remediate", response_model=None)
    async def post_remediate(
        project_id: str | None = Query(default=None),
        x_idempotency_key: str | None = Header(
            default=None,
            alias="X-Idempotency-Key",
        ),
    ) -> dict[str, object]:
        """Run the detector once AND apply remediation for each finding.

        Idempotency (C25):
        - ``X-Idempotency-Key`` header: if a row with this key already
          exists, return the previously-recorded result (``idempotent_replay:
          true``) without running the detector or dispatcher again.
        - Per-action dedupe: before dispatching each finding, check the
          ``remediation_actions`` table for a recent row matching
          ``(blocked_todo_id, action_kind)`` within the configured
          ``retry_delay_hours`` cooldown.  A match is skipped with
          ``skipped_reason: "duplicate"`` so a still-blocked task does not
          get a fresh dispatch/retry/human-todo on every call.
        """
        cfg = _get_remediation_config(daemon_state)

        async def _run(
            *,
            session: AsyncSession,
            todo_repo: TodoRepository,
            human_todo_repo: HumanTodoRepository,
        ) -> dict[str, object]:
            from general_ludd.db.repository import RemediationActionRepository

            remediation_repo = RemediationActionRepository(session)

            if x_idempotency_key is not None:
                existing = await remediation_repo.find_by_idempotency_key(
                    x_idempotency_key
                )
                if existing:
                    return {
                        "project_id": project_id,
                        "scanned": 0,
                        "actions": [_remediation_row_to_dict(r) for r in existing],
                        "idempotent_replay": True,
                    }

            det = BlockerDetector(
                todo_repo=todo_repo,
                human_todo_repo=human_todo_repo,
                config=cfg,
                session=session,
            )
            findings = await det.scan(project_id=project_id)
            dispatcher = RemediationDispatcher(
                detector=det,
                todo_repo=todo_repo,
                human_todo_repo=human_todo_repo,
                remediation_repo=remediation_repo,
            )
            window = timedelta(hours=cfg.retry_delay_hours)
            cutoff = datetime.now(UTC) - window
            actions: list[dict[str, object]] = []
            for blocked in findings:
                action_kind: str = (
                    str(blocked.suggested_remediation) if blocked.suggested_remediation else "no_action"
                )
                is_duplicate = await remediation_repo.exists_recent_by_action(
                    blocked.todo_id, action_kind, cutoff
                )
                if is_duplicate:
                    actions.append(
                        {
                            "kind": action_kind,
                            "blocked_todo_id": blocked.todo_id,
                            "project_id": blocked.project_id,
                            "ok": True,
                            "summary": "Skipped (duplicate — already remediated within window).",
                            "skipped_reason": "duplicate",
                        }
                    )
                    continue
                a = await dispatcher.remediate(
                    blocked, idempotency_key=x_idempotency_key
                )
                actions.append(_action_to_dict(a))

            return {
                "project_id": project_id,
                "scanned": len(findings),
                "actions": actions,
            }

        return await _run_with_session(app, _run)

    @app.get(
        "/admin/remediation/chronic-blockers",
        response_model=None,
    )
    async def get_chronic_blockers(
        project_id: str | None = Query(default=None),
        lookback_days: int | None = Query(default=None),
    ) -> dict[str, object]:
        """Return the chronic-blocker report."""
        cfg = _get_remediation_config(daemon_state)

        async def _run(
            *,
            session: AsyncSession,
            todo_repo: TodoRepository,
            human_todo_repo: HumanTodoRepository,
        ) -> dict[str, object]:
            det = BlockerDetector(
                todo_repo=todo_repo,
                human_todo_repo=human_todo_repo,
                config=cfg,
                session=session,
            )
            return await chronic_blocker_report(
                det,
                project_id=project_id,
                lookback_days=lookback_days,
            )

        return await _run_with_session(app, _run)

    @app.get("/admin/remediation/history", response_model=None)
    async def get_history(
        project_id: str | None = Query(default=None),
        since: datetime | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        """Return the audit trail of past remediation actions."""
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(
                status_code=503, detail="session factory not wired"
            )
        async with factory() as session:
            repo = RemediationActionRepository(session)
            if since is not None:
                rows = await repo.list_since(since, project_id=project_id)
            else:
                rows = await repo.list_for_project(
                    project_id=project_id, limit=limit
                )
            return {
                "project_id": project_id,
                "since": since.isoformat() if since else None,
                "actions": [_remediation_row_to_dict(r) for r in rows],
                "total": len(rows),
            }

    @app.get(
        "/admin/remediation/config",
        response_model=RemediationConfigResponse,
    )
    async def get_config() -> RemediationConfigResponse:
        """Return the current RemediationConfig thresholds."""
        cfg = _get_remediation_config(daemon_state)
        return RemediationConfigResponse(
            human_input_block_hours=cfg.human_input_block_hours,
            permission_escalation_block_hours=cfg.permission_escalation_block_hours,
            max_requeues_before_chronic=cfg.max_requeues_before_chronic,
            chronic_lookback_days=cfg.chronic_lookback_days,
            min_chronic_incidents=cfg.min_chronic_incidents,
            retry_delay_hours=cfg.retry_delay_hours,
        )
