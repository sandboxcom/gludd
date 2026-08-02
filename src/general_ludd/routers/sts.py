"""STS token endpoints — mint, validate, revoke daemon-side token operations.

Mints tokens via TokenMinter, validates via TokenStore lookup, revokes via
TokenRevoker. All endpoints require PSK auth (on /admin/ prefix).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from general_ludd.db.models import AgentTokenModel

logger = logging.getLogger(__name__)


class MintRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    parent_agent_id: str = Field(default="root", max_length=128)


class RevokeRequest(BaseModel):
    terminal_state: str = Field(default="completed", max_length=32)


class MintResponse(BaseModel):
    token_id: str
    agent_id: str
    parent_agent_id: str
    role_name: str
    role_id: str
    created_at: str
    expires_at: str | None


class ValidateResponse(BaseModel):
    valid: bool
    token_id: str
    agent_id: str | None
    revoked: bool
    revoked_at: str | None


def _token_to_dict(token: AgentTokenModel) -> dict[str, Any]:
    return {
        "token_id": token.token_id,
        "agent_id": token.agent_id,
        "parent_agent_id": token.parent_agent_id,
        "role_name": token.role_name,
        "role_id": token.role_id,
        "scope_hash": token.scope_hash,
        "scope_actions": token.scope_actions,
        "created_at": token.created_at.isoformat() if token.created_at else None,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
        "hydration_count": token.hydration_count,
    }


def _get_store(app: FastAPI):
    """Return the TokenStore from daemon state, or None if unwired."""
    ds = getattr(app.state, "daemon_state", None) or {}
    reaper = ds.get("_sts_reaper")
    if reaper is not None:
        return getattr(reaper, "_store", None)
    return None


def _get_revoker(app: FastAPI):
    """Return the TokenRevoker from daemon state, or None if unwired."""
    ds = getattr(app.state, "daemon_state", None) or {}
    reaper = ds.get("_sts_reaper")
    if reaper is not None:
        return getattr(reaper, "_revoker", None)
    return None


def _get_minter(app: FastAPI):
    """Build a TokenMinter from the app's secrets resolver."""
    resolver = getattr(app.state, "_secrets_resolver", None)
    if resolver is None:
        return None
    from general_ludd.sts.minter import TokenMinter

    return TokenMinter(secrets_manager=resolver)


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post(
        "/admin/sts/mint",
        response_model=MintResponse,
        status_code=201,
        summary="Mint a new STS token for an agent",
    )
    async def sts_mint(req: MintRequest) -> dict[str, Any]:
        minter = _get_minter(app)
        if minter is None:
            raise HTTPException(status_code=503, detail="Secrets resolver not wired")
        store = _get_store(app)
        if store is None:
            raise HTTPException(status_code=503, detail="STS token store not wired")

        creds = await minter.mint(
            agent_id=req.agent_id,
            parent_agent_id=req.parent_agent_id,
        )

        token_id = f"tok-{req.agent_id}"
        role_name = f"agent-{req.agent_id}"

        record = AgentTokenModel(
            token_id=token_id,
            agent_id=req.agent_id,
            parent_agent_id=req.parent_agent_id,
            role_name=role_name,
            role_id=creds.role_id,
            scope_hash="",
        )
        await store.store(record)
        logger.info(
            "STS endpoint — mint: agent=%s parent=%s token=%s",
            req.agent_id,
            req.parent_agent_id,
            token_id,
        )
        return {
            "token_id": token_id,
            "agent_id": req.agent_id,
            "parent_agent_id": req.parent_agent_id,
            "role_name": role_name,
            "role_id": creds.role_id,
            "created_at": record.created_at.isoformat() if record.created_at else "",
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        }

    @app.get(
        "/admin/sts/validate/{agent_id}",
        response_model=ValidateResponse,
        summary="Validate an STS token by agent_id",
    )
    async def sts_validate(agent_id: str) -> dict[str, Any]:
        store = _get_store(app)
        if store is None:
            raise HTTPException(status_code=503, detail="STS token store not wired")

        record = await store.get(agent_id)
        if record is None:
            return {
                "valid": False,
                "token_id": "",
                "agent_id": None,
                "revoked": False,
                "revoked_at": None,
            }

        is_revoked = record.revoked_at is not None
        return {
            "valid": not is_revoked,
            "token_id": record.token_id,
            "agent_id": record.agent_id,
            "revoked": is_revoked,
            "revoked_at": record.revoked_at.isoformat() if record.revoked_at else None,
        }

    @app.post(
        "/admin/sts/revoke/{agent_id}",
        status_code=200,
        summary="Revoke an STS token by agent_id",
    )
    async def sts_revoke(agent_id: str, req: RevokeRequest) -> dict[str, Any]:
        revoker = _get_revoker(app)
        if revoker is None:
            raise HTTPException(status_code=503, detail="STS revoker not wired")

        try:
            await revoker.revoke(agent_id, terminal_state=req.terminal_state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        logger.info(
            "STS endpoint — revoke: agent=%s terminal_state=%s",
            agent_id,
            req.terminal_state,
        )
        return {"status": "revoked", "agent_id": agent_id}

    @app.get(
        "/admin/sts/tokens/{agent_id}",
        summary="Get a single STS token record by agent_id",
    )
    async def sts_get_token(agent_id: str) -> dict[str, Any]:
        store = _get_store(app)
        if store is None:
            raise HTTPException(status_code=503, detail="STS token store not wired")

        record = await store.get(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Token not found")
        return _token_to_dict(record)

    @app.get(
        "/admin/sts/tokens",
        summary="List all STS token records",
    )
    async def sts_list_tokens() -> list[dict[str, Any]]:
        store = _get_store(app)
        if store is None:
            raise HTTPException(status_code=503, detail="STS token store not wired")

        records = await store.list_all()
        return [_token_to_dict(r) for r in records]
