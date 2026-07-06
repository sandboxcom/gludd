"""FastAPI router for the self-update pipeline (Phase 2 Step 2, issue #81).

Wraps the pure self-update ladder (classifier -> apply / priority) in two
admin endpoints that an operator or orchestrator can drive:

  * ``POST /admin/self-update/plan``    -- classify a request then run the
    apply ladder, returning the outcome + audit record (no backlog side
    effect).
  * ``POST /admin/self-update/enqueue`` -- classify a request then turn the
    plan into a backlog todo spec and persist it (so the existing backlog /
    scheduler own execution).

Both endpoints are **PSK-gated** exactly like ``routers/self_improve.py``:
authentication is applied by the daemon middleware on every non-public
``/admin/*`` path (the path is intentionally absent from the daemon's
``_PUBLIC_PATHS``). The router performs no auth of its own so it cannot
drift from the daemon's posture.

Everything is **fail-soft**: a classifier / apply / persistence failure is
caught and returned as an ``{"status": "error", ...}`` payload with HTTP 200
so an operator always gets a structured response (the self-update surface
must never break the daemon's admin API).
"""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.repository import TodoRepository
from general_ludd.self_update.apply import apply_plan
from general_ludd.self_update.classifier import classify
from general_ludd.self_update.model import SelfUpdateRequest
from general_ludd.self_update.priority import to_todo_spec


def _get_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession] | None:
    return getattr(app.state, "_session_factory", None)


def _request_from_payload(payload: dict[str, object]) -> SelfUpdateRequest:
    """Build a :class:`SelfUpdateRequest` from the JSON payload.

    Accepts ``raw_text`` (preferred) or ``text`` as the request body so both
    the canonical and the abbreviated form work. ``approval_token`` is only
    forwarded when truthy so an empty string normalises to ``None`` (no
    implicit approval).
    """
    raw_text = str(payload.get("raw_text") or payload.get("text") or "")
    requested_by = str(payload.get("requested_by") or "user")
    approval_token = cast("str | None", payload.get("approval_token") or None)
    return SelfUpdateRequest(
        raw_text=raw_text,
        requested_by=requested_by,
        approval_token=approval_token,
    )


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post("/admin/self-update/plan")
    async def admin_self_update_plan(payload: dict[str, object]) -> dict[str, object]:
        try:
            request = _request_from_payload(payload)
            plan = classify(request)
            # Phase 2 Step 3: read the daemon-built audit_sink (constructed in
            # _lifespan over AuditEventRepository) so every apply decision is
            # persisted. Missing (e.g. boot before lifespan) -> None, which
            # apply_plan treats as "no audit side-effect" (fail-soft).
            audit_sink = getattr(app.state, "_self_update_audit_sink", None)
            result = apply_plan(plan, request, audit_sink=audit_sink)
            return {
                "status": "ok",
                "outcome": result.outcome,
                "applied": result.applied,
                "audit": result.audit.as_dict(),
                "landed_files": list(result.landed_files),
                "subsystem": plan.subsystem.value,
                "change_kind": plan.change_kind.value,
                "apply_tier": plan.apply_tier.value,
                "requires_approval": plan.requires_approval,
                "confidence": plan.confidence,
                "rationale": plan.rationale,
            }
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

    @app.post("/admin/self-update/enqueue")
    async def admin_self_update_enqueue(payload: dict[str, object]) -> dict[str, object]:
        try:
            request = _request_from_payload(payload)
            project_id = payload.get("project_id")
            plan = classify(request)
            spec = to_todo_spec(
                plan,
                request,
                project_id=str(project_id) if project_id is not None else None,
            )

            factory = _get_session_factory(app)
            if factory is not None:
                async with factory() as session:
                    repo = TodoRepository(session)
                    created = await repo.create(todo_data=spec)
                    todo_id = created.todo_id
                    await session.commit()
                return {
                    "status": "ok",
                    "queued": True,
                    "persisted": True,
                    "todo_id": todo_id,
                    "spec": spec,
                }

            # No DB session factory wired (e.g. boot / fail-soft mode): keep the
            # spec in the in-memory daemon_state backlog so it is not lost and
            # remains observable via /admin/daemon/stats + the admin UI.
            backlog = cast("list[object]", _daemon_state.setdefault("todos", []))
            backlog.append(spec)
            return {
                "status": "ok",
                "queued": True,
                "persisted": False,
                "spec": spec,
            }
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
