"""HTTP router for account backup, deletion, and cloud retention notices.

Endpoints:
    POST   /api/account/backup     — export user data as JSON in the response
    DELETE /api/account             — permanently delete all user data
    GET    /api/account/policy      — get retention policy text for a service

Auth follows the daemon pattern: GET endpoints are public (an operator should
be able to see a cloud provider's published retention policy without a PSK);
write endpoints (POST backup, DELETE) are PSK-gated. The deletion endpoint
additionally requires ``confirm=true`` in the request body — a second
defence-in-depth against fat-fingered deletes.

The backup endpoint returns the JSON payload directly in the HTTP response
rather than a file download; the CLI / caller is free to write it to disk.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from general_ludd.account.backup import (
    _delete_user_data,
    _export_user_data,
)
from general_ludd.account.deletion_notice import (
    SUPPORTED_SERVICES,
    build_deletion_notice,
    get_policy_text,
)
from general_ludd.account.ephemeral import (
    SUPPORTED_PROVIDERS,
    EphemeralAccountManager,
)
from general_ludd.routers._util import get_session_factory as _get_session_factory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class BackupRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)


class DeleteRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    confirm: bool = Field(default=False)


class CreateAccountRequest(BaseModel):
    """Body for ``POST /api/account/create`` — provision a cloud account.

    When ``ephemeral`` is true, the account is created via the daemon's
    :class:`EphemeralAccountManager` (when wired) and tracked for automatic
    cleanup once the retention window elapses or the task using it completes.
    """

    provider: str = Field(min_length=1, max_length=32)
    budget: float = Field(default=10.0, ge=0)
    ephemeral: bool = Field(default=False)


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    @app.post("/api/account/backup")
    async def api_account_backup(req: BackupRequest) -> dict[str, object]:
        """Export all user-scoped data as JSON (returned in the body)."""
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        try:
            payload = await _export_user_data(factory, req.user_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("account backup failed for user_id=%s", req.user_id)
            raise HTTPException(status_code=500, detail=f"backup failed: {exc}") from exc
        return payload

    @app.delete("/api/account")
    async def api_account_delete(req: DeleteRequest) -> dict[str, object]:
        """Permanently delete all user-scoped data.

        Requires ``confirm=true`` in the body. Returns a summary of what was
        deleted. The operation is irreversible.
        """
        if not req.confirm:
            raise HTTPException(
                status_code=400,
                detail="confirm=true is required for account deletion",
            )
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database available")
        try:
            summary = await _delete_user_data(factory, req.user_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("account delete failed for user_id=%s", req.user_id)
            raise HTTPException(status_code=500, detail=f"delete failed: {exc}") from exc
        return summary

    @app.get("/api/account/policy")
    async def api_account_policy(service: str) -> dict[str, object]:
        """Return the retention policy text for a cloud service."""
        try:
            text = get_policy_text(service)
            notice = build_deletion_notice(service)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{exc}; supported services: {sorted(SUPPORTED_SERVICES)}"
                ),
            ) from exc
        return {
            "service": service.lower().strip().replace(" ", "").replace("_", "-").replace("z.ai", "zai"),
            "policy": text,
            "notice": notice,
        }

    @app.post("/api/account/create")
    async def api_account_create(req: CreateAccountRequest) -> dict[str, object]:
        """Provision a cloud account (optionally ephemeral).

        Ephemeral accounts are scoped to ``budget`` USD and recorded for
        automatic cleanup via ``POST /api/account/cleanup``. Non-ephemeral
        creation is a no-op shim that returns 501 until a persistent-account
        backend is wired — the EphemeralAccountManager path is the only
        supported cloud-account provisioning surface today.
        """
        if req.provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unsupported provider {req.provider!r}; "
                    f"supported: {sorted(SUPPORTED_PROVIDERS)}"
                ),
            )
        if not req.ephemeral:
            raise HTTPException(
                status_code=501,
                detail=(
                    "non-ephemeral account creation is not implemented; "
                    "pass ephemeral=true to provision a scoped, auto-cleaned account"
                ),
            )
        mgr = getattr(app.state, "_ephemeral_account_manager", None)
        if mgr is None:
            raise HTTPException(
                status_code=503,
                detail="ephemeral account manager not wired on this daemon",
            )
        try:
            creds = mgr.create_account(provider=req.provider, budget=req.budget)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("ephemeral account creation failed")
            raise HTTPException(
                status_code=500, detail=f"create failed: {exc}"
            ) from exc
        return {
            "account_id": creds.account_id,
            "provider": creds.provider,
            "access_key_id": creds.access_key_id,
            "budget_limit": creds.budget_limit,
            "ephemeral": True,
        }

    @app.post("/api/account/cleanup")
    async def api_account_cleanup() -> dict[str, object]:
        """Sweep all ephemeral accounts past their retention window.

        Returns a ``{deleted: [...], kept: [...]}`` report. Never raises —
        per-account provider failures are recorded in the per-account result
        dict and the sweep continues.
        """
        mgr: EphemeralAccountManager | None = getattr(
            app.state, "_ephemeral_account_manager", None
        )
        if mgr is None:
            raise HTTPException(
                status_code=503,
                detail="ephemeral account manager not wired on this daemon",
            )
        try:
            return mgr.cleanup_expired()
        except Exception as exc:
            logger.exception("ephemeral cleanup failed")
            raise HTTPException(
                status_code=500, detail=f"cleanup failed: {exc}"
            ) from exc
