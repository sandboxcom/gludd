"""HTTP router: Ornith training-data collector endpoints.

PSK-gated (admin-only). Surfaces:

  - ``POST   /admin/ornith/record``              -- record a fresh pair
    (called by ``OrnithClient.solve()`` after each invocation).
  - ``PATCH  /admin/ornith/{id}/outcome``        -- update outcome (called
    by the observer / review router / gate).
  - ``GET    /admin/ornith/pending``             -- list pairs with pending
    outcomes.
  - ``GET    /admin/ornith/pairs``               -- list pairs filtered by
    comma-separated outcome statuses + lookback window. Used by the
    self-improvement role to pull rejected / reverted artifacts.
  - ``GET    /admin/ornith/export``              -- JSONL export, returns
    file path + row count.
  - ``GET    /admin/ornith/stats``               -- aggregate stats.
  - ``GET    /admin/ornith/status``              -- MCP / endpoint status.
  - ``POST   /admin/ornith/self-improve``        -- trigger training cycle.
  - ``GET    /admin/ornith/history``             -- past self-improve cycles.
  - ``GET    /admin/ornith/config``              -- view ornith config.
  - ``PUT    /admin/ornith/config``              -- update ornith config.

All endpoints require the daemon PSK (admin middleware enforces it on
every ``/admin/*`` path).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.db.models import OrnithTrainingPairModel
from general_ludd.ornith.training_repo import (
    VALID_OUTCOME_STATUSES,
    VALID_SCAFFOLD_KINDS,
    OrnithInvocation,
    OrnithTrainingRepo,
)

logger = logging.getLogger(__name__)


def _get_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession] | None:
    return getattr(app.state, "_session_factory", None)


class RecordPairRequest(BaseModel):
    """Request body for recording a fresh scaffold/outcome training pair."""

    task_description: str = Field(min_length=1)
    target_files: list[str] = Field(default_factory=list)
    scaffold_kind: str
    scaffold_content: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    iterations_used: int = 0
    tokens_consumed: int = 0
    model_sha: str = ""
    project_id: str | None = None
    invoked_at: datetime | None = None


class SetOutcomeRequest(BaseModel):
    """Request body for setting the outcome status of a recorded pair."""

    status: str
    details: dict[str, object] = Field(default_factory=dict)


class OrnithConfigUpdateRequest(BaseModel):
    """Request body for updating the Ornith model_sha config value."""

    model_sha: str | None = None


def _pair_to_dict(row: OrnithTrainingPairModel) -> dict[str, object]:
    try:
        target_files = _json.loads(row.target_files or "[]")
    except Exception:
        target_files = []
    try:
        details = _json.loads(row.outcome_details or "{}")
    except Exception:
        details = {}
    return {
        "id": row.id,
        "invoked_at": row.invoked_at.isoformat() if row.invoked_at else None,
        "task_description": row.task_description,
        "target_files": target_files,
        "scaffold_kind": row.scaffold_kind,
        "scaffold_content": row.scaffold_content,
        "scaffold_hash": row.scaffold_hash,
        "iterations_used": row.iterations_used,
        "tokens_consumed": row.tokens_consumed,
        "model_sha": row.model_sha,
        "outcome_status": row.outcome_status,
        "outcome_details": details,
        "outcome_set_at": row.outcome_set_at.isoformat() if row.outcome_set_at else None,
        "project_id": row.project_id,
        "agent_id": row.agent_id,
    }


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register the admin Ornith training-data collector endpoints."""

    @app.post("/admin/ornith/record", status_code=201)
    async def api_record_pair(req: RecordPairRequest) -> dict[str, object]:
        if req.scaffold_kind not in VALID_SCAFFOLD_KINDS:
            raise HTTPException(
                status_code=422,
                detail=(f"invalid scaffold_kind; must be one of {sorted(VALID_SCAFFOLD_KINDS)}"),
            )
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        invoked_at = req.invoked_at or datetime.now(UTC)
        invocation = OrnithInvocation(
            task_description=req.task_description,
            target_files=req.target_files,
            scaffold_kind=req.scaffold_kind,
            scaffold_content=req.scaffold_content,
            agent_id=req.agent_id,
            iterations_used=req.iterations_used,
            tokens_consumed=req.tokens_consumed,
            model_sha=req.model_sha,
            project_id=req.project_id,
            invoked_at=invoked_at,
        )
        async with factory() as session:
            repo = OrnithTrainingRepo(session)
            try:
                row = await repo.record_pair(invocation)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            await session.commit()
            return _pair_to_dict(row)

    @app.patch("/admin/ornith/{pair_id}/outcome")
    async def api_set_outcome(pair_id: str, req: SetOutcomeRequest) -> dict[str, object]:
        if req.status not in VALID_OUTCOME_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=(f"invalid status; must be one of {sorted(VALID_OUTCOME_STATUSES)}"),
            )
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        async with factory() as session:
            repo = OrnithTrainingRepo(session)
            try:
                row = await repo.set_outcome(pair_id, req.status, req.details)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="pair not found") from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            await session.commit()
            return _pair_to_dict(row)

    @app.get("/admin/ornith/pending")
    async def api_list_pending(limit: int = 100, older_than_minutes: int = 0) -> dict[str, object]:
        factory = _get_session_factory(app)
        if factory is None:
            return {"pending": [], "count": 0}
        async with factory() as session:
            repo = OrnithTrainingRepo(session)
            rows = await repo.get_pending_outcomes(limit=limit, older_than_minutes=older_than_minutes)
            return {
                "pending": [_pair_to_dict(r) for r in rows],
                "count": len(rows),
            }

    @app.get("/admin/ornith/export")
    async def api_export(
        since: datetime | None = None,
        project_id: str | None = None,
        out_path: str | None = None,
    ) -> dict[str, object]:
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        async with factory() as session:
            repo = OrnithTrainingRepo(session)
            try:
                path = await repo.export_dataset(since=since, project_id=project_id, out_path=out_path)
            except ValueError as exc:
                # The confinement error contains both the rejected path and the
                # private allowlist. Keep those diagnostics server-side and
                # expose only the stable policy boundary to the caller.
                raise HTTPException(
                    status_code=422,
                    detail="out_path is outside the configured Ornith export root",
                ) from exc

        # Count rows OFF the event loop — a large export file would otherwise
        # block the loop for the full read.
        def _count_rows() -> int:
            n = 0
            with Path(path).open("r", encoding="utf-8") as fh:
                for _ in fh:
                    n += 1
            return n

        row_count = await asyncio.to_thread(_count_rows)
        return {
            "path": str(path),
            "row_count": row_count,
        }

    @app.get("/admin/ornith/stats")
    async def api_stats() -> dict[str, object]:
        factory = _get_session_factory(app)
        if factory is None:
            return {
                "counts_by_status": {},
                "total": 0,
                "success_rate": 0.0,
                "avg_tokens_per_call": 0.0,
            }
        async with factory() as session:
            repo = OrnithTrainingRepo(session)
            return await repo.stats()

    @app.get("/admin/ornith/pairs")
    async def api_list_pairs(
        status: str | None = None,
        limit: int = 100,
        lookback_days: int = 0,
        project_id: str | None = None,
    ) -> dict[str, object]:
        """List pairs filtered by comma-separated outcome statuses.

        ``status=rejected_by_gate,rejected_by_review,reverted`` is the shape
        the self-improvement role uses to find artifacts that NEED to be
        improved. Returns newest-first.
        """
        factory = _get_session_factory(app)
        if factory is None:
            return {"pairs": [], "count": 0}
        statuses: list[str] = []
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
        if not statuses:
            return {"pairs": [], "count": 0}
        async with factory() as session:
            repo = OrnithTrainingRepo(session)
            try:
                rows = await repo.list_pairs_by_statuses(
                    statuses=statuses,
                    project_id=project_id,
                    limit=limit,
                    lookback_days=lookback_days,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {"pairs": [_pair_to_dict(r) for r in rows], "count": len(rows)}

    @app.get("/admin/ornith/status")
    async def api_ornith_status() -> dict[str, object]:
        mcp_proc = getattr(app.state, "_ornith_mcp_proc", None)
        status = getattr(app.state, "_ornith_status", {})
        return {
            "installed": mcp_proc is not None,
            "version": status.get("version"),
            "model_sha": status.get("model_sha", os.environ.get("ORNITH_MODEL_SHA", "")),
            "last_call_at": status.get("last_call_at"),
            "total_calls": status.get("total_calls", 0),
            "success_rate": status.get("success_rate", 0.0),
            "sandbox_backend": status.get("sandbox_backend", "none"),
        }

    @app.post("/admin/ornith/self-improve")
    async def api_ornith_self_improve() -> dict[str, object]:
        import httpx

        daemon_url = (
            f"http://{app.state._host}:{app.state._port}" if hasattr(app.state, "_host") else "http://127.0.0.1:8000"
        )
        psk = os.environ.get("GLUDD_AUTH_PSK", "").strip()
        headers = {"Authorization": f"Bearer {psk}"} if psk else {}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{daemon_url}/admin/self-improve/run",
                    headers=headers,
                    timeout=300.0,
                )
                body = resp.json() if resp.status_code == 200 else {"error": resp.text, "status_code": resp.status_code}
        except Exception as exc:
            body = {"error": str(exc)}
        cycle = {
            "triggered_at": datetime.now(UTC).isoformat(),
            "result": body,
        }
        history = getattr(app.state, "_ornith_history", [])
        history.insert(0, cycle)
        app.state._ornith_history = history[:50]
        return {"status": "triggered", "cycle": cycle}

    @app.get("/admin/ornith/history")
    async def api_ornith_history(limit: int = 20) -> dict[str, object]:
        history = getattr(app.state, "_ornith_history", [])
        return {"cycles": history[: max(1, min(limit, 100))], "count": len(history[:limit])}

    @app.get("/admin/ornith/config")
    async def api_ornith_config_get() -> dict[str, object]:
        status = getattr(app.state, "_ornith_status", {})
        return {
            "ornith_enabled": bool(getattr(app.state, "_ornith_mcp_proc", None)),
            "model_sha": status.get("model_sha", os.environ.get("ORNITH_MODEL_SHA", "")),
            "binary_path": status.get("binary_path", "ornith"),
            "env_ornith_enabled": os.environ.get("ORNITH_ENABLED", "").lower() in {"1", "true", "yes"},
        }

    @app.put("/admin/ornith/config")
    async def api_ornith_config_update(req: OrnithConfigUpdateRequest) -> dict[str, object]:
        if req.model_sha is not None:
            status = getattr(app.state, "_ornith_status", {})
            status["model_sha"] = req.model_sha
            app.state._ornith_status = status
        return await api_ornith_config_get()
