"""Spend-limiter API endpoints.

Endpoints:
    GET  /api/spend          — current window spend, limit, remaining
    POST /api/spend/configure — update limit_usd and window_seconds at runtime

PSK authentication is applied globally by the daemon middleware, so these
endpoints follow the same pattern as the rest of the router modules: they are
only reachable to clients that present a valid Bearer PSK.

The in-process :class:`~general_ludd.controllers.spend_limiter.SpendLimiter`
instance is stored on ``app.state._spend_limiter``.  If no instance exists
(daemon not yet started, or limiter not configured), the endpoints return safe
default values rather than raising.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT_USD: float = 20.0
_DEFAULT_WINDOW_SECONDS: float = 3600.0


class ConfigureSpendRequest(BaseModel):
    limit_usd: float = Field(gt=0.0, description="Spend cap in USD for the rolling window")
    window_seconds: float = Field(gt=0.0, description="Rolling window width in seconds")


def _get_limiter(app: FastAPI) -> Any:
    return getattr(app.state, "_spend_limiter", None)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get("/api/spend")
    async def api_spend_status() -> dict[str, Any]:
        """Return the current rolling-window spend summary.

        Returns:
            JSON with ``window_spend_usd``, ``limit_usd``, ``remaining_usd``,
            ``window_seconds``, and ``limiter_active`` flag.
        """
        limiter = _get_limiter(app)
        if limiter is None:
            return {
                "limiter_active": False,
                "window_spend_usd": 0.0,
                "limit_usd": _DEFAULT_LIMIT_USD,
                "remaining_usd": _DEFAULT_LIMIT_USD,
                "window_seconds": _DEFAULT_WINDOW_SECONDS,
            }
        return {
            "limiter_active": True,
            "window_spend_usd": limiter.window_spend(),
            "limit_usd": limiter._limit_usd,
            "remaining_usd": limiter.remaining(),
            "window_seconds": limiter._window_seconds,
        }

    @app.post("/api/spend/configure")
    async def api_spend_configure(req: ConfigureSpendRequest) -> dict[str, Any]:
        """Replace the spend limiter with new ``limit_usd`` / ``window_seconds``.

        Prior spend history is PRESERVED: the old limiter's in-window records
        are carried over via ``snapshot()`` / ``restore()`` so that
        reconfiguring the cap cannot be used to reset the rolling window and
        evade the spend cap (D-33).

        Returns:
            JSON with the new effective limits.
        """
        from general_ludd.controllers.spend_limiter import SpendLimiter

        old_limiter = _get_limiter(app)
        new_limiter = SpendLimiter(
            limit_usd=req.limit_usd,
            window_seconds=req.window_seconds,
        )
        if old_limiter is not None:
            new_limiter.restore(old_limiter.snapshot())
        app.state._spend_limiter = new_limiter
        logger.info(
            "SpendLimiter reconfigured: limit=%.4f USD window=%.0f s (history carried over)",
            req.limit_usd,
            req.window_seconds,
        )
        return {
            "configured": True,
            "limit_usd": req.limit_usd,
            "window_seconds": req.window_seconds,
        }
