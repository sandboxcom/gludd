"""HTTP router: deployment health endpoints.

PSK-gated (admin-only). Surfaces:

  - ``GET  /admin/deployments/health``
        Current health status of all deployments.
  - ``POST /admin/deployments/{deployment_id}/remediate``
        Force remediation of a deployment.
  - ``GET  /admin/deployments/incidents``
        Incident log for deployment health events.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from general_ludd.models.deployment_health import (
    DeploymentHealthChecker,
    DeploymentHealthIncident,
    DeploymentStatus,
    SelfHealingRouter,
)

logger = logging.getLogger(__name__)


class DeploymentHealthResponse(BaseModel):
    deployment_id: str
    healthy: bool
    consecutive_failures: int
    last_error: str | None
    last_check: float


class DeploymentHealthListResponse(BaseModel):
    deployments: list[DeploymentHealthResponse]
    total: int


class IncidentResponse(BaseModel):
    deployment_id: str
    timestamp: float
    kind: str
    detail: str
    was_remediated: bool


class IncidentListResponse(BaseModel):
    incidents: list[IncidentResponse]
    total: int


def _get_health_checker(app: FastAPI) -> DeploymentHealthChecker | None:
    router: SelfHealingRouter | None = getattr(
        app.state, "_deployment_health_router", None
    )
    if router is None:
        return None
    return router.health_checker


def _status_to_dict(s: DeploymentStatus) -> dict[str, Any]:
    return {
        "deployment_id": s.deployment_id,
        "healthy": s.healthy,
        "consecutive_failures": s.consecutive_failures,
        "last_error": s.last_error,
        "last_check": s.last_check,
    }


def _incident_to_dict(i: DeploymentHealthIncident) -> dict[str, Any]:
    return {
        "deployment_id": i.deployment_id,
        "timestamp": i.timestamp,
        "kind": i.kind,
        "detail": i.detail,
        "was_remediated": i.was_remediated,
    }


def register(app: FastAPI, daemon_state: dict[str, Any]) -> None:
    @app.get(
        "/admin/deployments/health",
        response_model=None,
    )
    async def get_deployment_health() -> dict[str, Any]:
        """Return current health status of all tracked deployments."""
        checker = _get_health_checker(app)
        if checker is None:
            raise HTTPException(
                status_code=503,
                detail="DeploymentHealthChecker not wired",
            )
        statuses = checker.all_statuses()
        return {
            "deployments": [
                _status_to_dict(s) for s in statuses.values()
            ],
            "total": len(statuses),
        }

    @app.post(
        "/admin/deployments/{deployment_id}/remediate",
        response_model=None,
    )
    async def post_force_remediate(deployment_id: str) -> dict[str, Any]:
        """Force remediation of a deployment — marks it healthy."""
        checker = _get_health_checker(app)
        if checker is None:
            raise HTTPException(
                status_code=503,
                detail="DeploymentHealthChecker not wired",
            )
        ok = checker.force_remediate(deployment_id)
        if not ok:
            # Deployment not tracked yet; create a healthy status for it.
            checker.get_status(deployment_id)
        status = checker.get_status(deployment_id)
        return _status_to_dict(status)

    @app.get(
        "/admin/deployments/incidents",
        response_model=None,
    )
    async def get_deployment_incidents(
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        """Return the incident log for deployment health events."""
        checker = _get_health_checker(app)
        if checker is None:
            raise HTTPException(
                status_code=503,
                detail="DeploymentHealthChecker not wired",
            )
        incidents = checker.get_incidents(limit=limit)
        return {
            "incidents": [
                _incident_to_dict(i) for i in incidents
            ],
            "total": len(incidents),
        }
