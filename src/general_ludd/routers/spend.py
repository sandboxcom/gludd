"""Spend-limiter API endpoints.

Endpoints:
    GET  /api/spend           — current window spend, limit, remaining
    POST /api/spend/configure — update limit_usd and window_seconds at runtime
    GET  /api/costs           — combined model API + infra cost breakdown
    GET  /api/credits         — prepaid service credit balances (per provider)
    GET  /admin/costs         — administrative cost-accounting summary

PSK authentication is applied globally by the daemon middleware, so these
endpoints follow the same pattern as the rest of the router modules: they are
only reachable to clients that present a valid Bearer PSK.

The in-process :class:`~general_ludd.controllers.spend_limiter.SpendLimiter`
instance is stored on ``app.state._spend_limiter``.  If no instance exists
(daemon not yet started, or limiter not configured), the endpoints return safe
default values rather than raising.

``GET /api/costs`` prefers a :class:`~general_ludd.budget.combined_cost.CombinedCostTracker`
on ``app.state._combined_cost_tracker`` (which merges per-project model + infra
spend); when absent it falls back to reading ``_spend_limiter`` and
``_infra_cost_tracker`` / ``_infra_tracker`` directly.
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

    @app.get("/admin/costs")
    async def admin_costs() -> dict[str, Any]:
        """Return cost-accounting summary across API and infrastructure spend.

        Returns:
            JSON with ``total_api_spend``, ``total_infra_spend``,
            ``breakdown_by_project``, ``breakdown_by_provider``, and
            ``burn_rate_24h``.
        """
        limiter = _get_limiter(app)
        infra_tracker = getattr(app.state, "_infra_tracker", None)

        total_api_spend = 0.0
        by_project: dict[str, float] = {}
        burn_rate_24h = 0.0

        if limiter is not None:
            total_api_spend = limiter.window_spend()
            by_project = limiter.project_breakdown()
            last_hour = limiter.spend_in_last_seconds(3600.0)
            burn_rate_24h = last_hour * 24.0

        total_infra_spend = 0.0
        by_provider: dict[str, float] = {}
        if infra_tracker is not None:
            total_infra_spend = infra_tracker.get_total_infra_cost()
            by_provider = infra_tracker.get_infra_cost_by_provider()

        return {
            "total_api_spend": total_api_spend,
            "total_infra_spend": total_infra_spend,
            "total_combined_spend": total_api_spend + total_infra_spend,
            "breakdown_by_project": by_project,
            "breakdown_by_provider": by_provider,
            "burn_rate_24h": burn_rate_24h,
        }

    @app.get("/api/costs")
    async def api_costs() -> dict[str, Any]:
        """Return combined model API + infrastructure cost breakdown.

        Resolution order:
          1. ``app.state._combined_cost_tracker`` (:class:`CombinedCostTracker`)
             — the unified facade over SpendLimiter + InfraCostTracker. Preferred
             because it merges per-project spend across model and infra sides.
          2. Inline fallback: SpendLimiter (``_spend_limiter``) for model-API
             cost + InfraCostTracker v2 (``_infra_cost_tracker``) or v1
             (``_infra_tracker``) for infra cost.

        Returns:
            JSON with ``model_api``, ``infrastructure``, ``total``,
            ``breakdown_by_provider``, ``breakdown_by_resource_type``,
            ``breakdown_by_project``, and ``record_count``.
        """
        combined = getattr(app.state, "_combined_cost_tracker", None)
        if combined is not None:
            breakdown: dict[str, Any] = combined.get_cost_breakdown()
            return breakdown

        limiter = _get_limiter(app)
        infra_tracker = getattr(app.state, "_infra_tracker", None)
        cost_tracker_v2 = getattr(app.state, "_infra_cost_tracker", None)

        model_api_cost = limiter.window_spend() if limiter is not None else 0.0
        infra_cost = 0.0
        by_provider: dict[str, float] = {}
        by_resource_type: dict[str, float] = {}
        by_project: dict[str, float] = {}
        record_count = 0

        if cost_tracker_v2 is not None:
            infra_cost = cost_tracker_v2.total_cost()
            by_provider = cost_tracker_v2.cost_by_provider()
            by_resource_type = cost_tracker_v2.cost_by_resource_type()
            by_project = cost_tracker_v2.cost_by_project()
            record_count = len(cost_tracker_v2.records())
        elif infra_tracker is not None:
            infra_cost = infra_tracker.get_total_infra_cost()
            by_provider = infra_tracker.get_infra_cost_by_provider()
            by_project = infra_tracker.get_infra_cost_by_project()

        return {
            "model_api": model_api_cost,
            "infrastructure": infra_cost,
            "total": model_api_cost + infra_cost,
            "breakdown_by_provider": by_provider,
            "breakdown_by_resource_type": by_resource_type,
            "breakdown_by_project": by_project,
            "record_count": record_count,
        }

    @app.get("/api/credits")
    async def api_credits(refresh: bool = False) -> dict[str, Any]:
        """Return prepaid service credit balances across all configured providers.

        Resolves a :class:`~general_ludd.budget.credit_tracker.CreditTracker`
        from ``app.state._credit_tracker`` (wired by the daemon at startup).
        When no tracker is wired the endpoint returns an empty dict so callers
        can always read a stable ``{}`` shape.

        Each value is the dict shape produced by
        :meth:`CreditTracker.check_balance`:
        ``{service, balance_usd, currency, fetched_at, error, raw}``.

        The balance figures are read from the tracker's in-memory cache —
        populated by the EventLoop's periodic ``check_service_credits`` phase
        (so this endpoint does NOT issue a fresh HTTP request per call in
        steady-state). When the cache is empty (e.g. before the first tick
        completes) the endpoint issues a single ``check_all_balances()`` call
        to seed it. Pass ``?refresh=1`` to force a fresh probe regardless of
        cache state.
        """
        tracker = getattr(app.state, "_credit_tracker", None)
        if tracker is None:
            return {}
        if refresh or not getattr(tracker, "_last_balance", None):
            try:
                balances: dict[str, Any] = dict(tracker.check_all_balances())
                return balances
            except Exception as exc:
                logger.warning("balance probe failed: %s", exc)
                return {}
        cached: dict[str, Any] = {
            svc: result
            for svc, result in (
                (svc, tracker.last_balance(svc)) for svc in tracker._last_balance
            )
            if result is not None
        }
        return cached
