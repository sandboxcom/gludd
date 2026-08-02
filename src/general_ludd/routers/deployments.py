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
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from general_ludd.infra.fix_approval import FixApprovalError, FixApprovalManager
from general_ludd.infra.fix_suggester import FixSuggester, make_fix_suggestion_fn
from general_ludd.infra.gpu_info_adapter import gpu_info_from_gpu_type
from general_ludd.infra.model_deploy_check import Finding, MisconfigDetector
from general_ludd.models.deployment_health import (
    DeploymentHealthChecker,
    DeploymentStatus,
    SelfHealingRouter,
)
from general_ludd.security.capability_guard import RequireCapability

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


class MisconfigCheckRequest(BaseModel):
    """Body for the static misconfig check.

    ``deployment`` is the serving config to lint. GPU context can be supplied
    either directly (``gpu_info``) or resolved from the static GPU tables via
    ``gpu_type`` (+ optional ``gpu_count``). When neither is given the detector
    runs config-only rules.
    """

    deployment: object
    gpu_info: dict[str, object] | None = None
    gpu_type: str | None = None
    gpu_count: int = Field(default=1, ge=1)


class FindingResponse(BaseModel):
    rule_id: str
    severity: str
    engine: str
    message: str
    remediation: str
    evidence: dict[str, object]


class RemediationResponse(BaseModel):
    rule_id: str
    format: str
    config_patch: dict[str, object]
    requires_restart: bool
    notes: str


class MisconfigCheckResponse(BaseModel):
    findings: list[FindingResponse]
    remediations: list[RemediationResponse]
    has_critical: bool


class SuggestFixRequest(BaseModel):
    """Body for the SLM-backed config-fix suggester.

    ``deployment`` is the serving config to fix. ``findings`` may be supplied
    directly (as finding dicts); when absent the detector re-derives them from
    ``deployment`` + GPU context (``gpu_info`` or ``gpu_type``/``gpu_count``).
    """

    deployment: object
    findings: list[dict[str, object]] | None = None
    gpu_info: dict[str, object] | None = None
    gpu_type: str | None = None
    gpu_count: int = Field(default=1, ge=1)


class FixDecisionRequest(BaseModel):
    """Body for approve/reject. ``retry`` triggers an optional redeploy."""

    retry: bool = False
    reason: str = ""


def _dict_to_finding(d: dict[str, object]) -> Finding:
    """Build a :class:`Finding` from a plain dict (fail-soft on missing keys)."""
    evidence = d.get("evidence")
    return Finding(
        rule_id=str(d.get("rule_id", "")),
        severity=str(d.get("severity", "warn")),
        engine=str(d.get("engine", "any")),
        message=str(d.get("message", "")),
        remediation=str(d.get("remediation", "")),
        evidence=dict(evidence) if isinstance(evidence, dict) else {},
    )


def _get_health_checker(app: FastAPI) -> DeploymentHealthChecker | None:
    router: SelfHealingRouter | None = getattr(app.state, "_deployment_health_router", None)
    if router is None:
        return None
    return router.health_checker


def _status_to_dict(s: DeploymentStatus) -> dict[str, object]:
    return {
        "deployment_id": s.deployment_id,
        "healthy": s.healthy,
        "consecutive_failures": s.consecutive_failures,
        "last_error": s.last_error,
        "last_check": s.last_check,
    }


def register(app: FastAPI, daemon_state: dict[str, object]) -> None:
    # One approval manager per app, held on app.state so state survives across
    # requests (suggest-fix -> approve/reject) and is isolated per app instance.
    fix_manager: FixApprovalManager | None = getattr(app.state, "_fix_approval_manager", None)
    if fix_manager is None:
        fix_manager = FixApprovalManager()
        app.state._fix_approval_manager = fix_manager

    @app.get(
        "/admin/deployments/health",
        response_model=DeploymentHealthListResponse,
    )
    async def get_deployment_health() -> DeploymentHealthListResponse:
        """Return current health status of all tracked deployments."""
        checker = _get_health_checker(app)
        if checker is None:
            raise HTTPException(
                status_code=503,
                detail="DeploymentHealthChecker not wired",
            )
        statuses = checker.all_statuses()
        deployment_responses = [
            DeploymentHealthResponse(
                deployment_id=s.deployment_id,
                healthy=s.healthy,
                consecutive_failures=s.consecutive_failures,
                last_error=s.last_error,
                last_check=s.last_check,
            )
            for s in statuses.values()
        ]
        return DeploymentHealthListResponse(
            deployments=deployment_responses,
            total=len(statuses),
        )

    @app.post(
        "/admin/deployments/{deployment_id}/remediate",
        dependencies=[Depends(RequireCapability(resource="admin:deploy", action="write"))],
        response_model=None,
    )
    async def post_force_remediate(deployment_id: str) -> dict[str, object]:
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
        response_model=IncidentListResponse,
    )
    async def get_deployment_incidents(
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> IncidentListResponse:
        """Return the incident log for deployment health events."""
        checker = _get_health_checker(app)
        if checker is None:
            raise HTTPException(
                status_code=503,
                detail="DeploymentHealthChecker not wired",
            )
        incidents = checker.get_incidents(limit=limit)
        incident_responses = [
            IncidentResponse(
                deployment_id=i.deployment_id,
                timestamp=i.timestamp,
                kind=i.kind,
                detail=i.detail,
                was_remediated=i.was_remediated,
            )
            for i in incidents
        ]
        return IncidentListResponse(
            incidents=incident_responses,
            total=len(incidents),
        )

    @app.post(
        "/admin/deployments/misconfig-check",
        response_model=MisconfigCheckResponse,
    )
    async def post_misconfig_check(body: MisconfigCheckRequest) -> MisconfigCheckResponse:
        """Statically lint a serving config for misconfigurations.

        Never returns 500 on a malformed ``deployment``: the detector surfaces
        that as a ``critical`` finding (HTTP 200).
        """
        detector = MisconfigDetector()
        if body.gpu_info is not None:
            gpu_info: dict[str, object] = body.gpu_info
        elif body.gpu_type:
            gpu_info = gpu_info_from_gpu_type(body.gpu_type, body.gpu_count)
        else:
            gpu_info = {}
        findings = detector.check(cast("dict[str, object]", body.deployment), gpu_info)
        finding_responses = [
            FindingResponse(
                rule_id=f.rule_id,
                severity=f.severity,
                engine=f.engine,
                message=f.message,
                remediation=f.remediation,
                evidence=f.evidence,
            )
            for f in findings
        ]
        remediation_dicts = [detector.remediate(f) for f in findings]
        remediation_responses = [
            RemediationResponse(
                rule_id=r["rule_id"],
                format=r["format"],
                config_patch=r["config_patch"],
                requires_restart=r["requires_restart"],
                notes=r["notes"],
            )
            for r in remediation_dicts
        ]
        return MisconfigCheckResponse(
            findings=finding_responses,
            remediations=remediation_responses,
            has_critical=any(f.severity == "critical" for f in findings),
        )

    @app.post(
        "/admin/deployments/suggest-fix",
        dependencies=[Depends(RequireCapability(resource="admin:deploy", action="write"))],
        response_model=None,
    )
    async def post_suggest_fix(body: SuggestFixRequest) -> dict[str, object]:
        """Propose a config-patch to fix a deployment's misconfigurations.

        Uses the SLM ``FixSuggester`` when a ``ModelGateway`` is wired on
        ``app.state._model_gateway``; otherwise falls back to the DETERMINISTIC
        ``MisconfigDetector.remediate()`` merge. NEVER 503s on a missing model —
        the fix loop is fail-soft by construction. Returns a ``fix_id`` for the
        parked proposal plus the ``patch`` and its ``source``.
        """
        detector = MisconfigDetector()
        if body.findings is not None:
            findings = [_dict_to_finding(f) for f in body.findings]
        else:
            if body.gpu_info is not None:
                gpu_info: dict[str, object] = body.gpu_info
            elif body.gpu_type:
                gpu_info = gpu_info_from_gpu_type(body.gpu_type, body.gpu_count)
            else:
                gpu_info = {}
            findings = detector.check(cast("dict[str, object]", body.deployment), gpu_info)

        gateway = getattr(app.state, "_model_gateway", None)
        suggest_fn = make_fix_suggestion_fn(gateway) if gateway is not None else None

        # Ask the SLM once (fail-soft — the wrapper never raises); label the
        # source by whether it actually produced a usable patch, NOT by whether
        # that patch differs from the deterministic merge (the SLM is steered to
        # echo the deterministic hint, so a value-compare would under-report it).
        slm_patch = suggest_fn(cast("dict[str, object]", body.deployment), findings) if suggest_fn is not None else None
        if isinstance(slm_patch, dict) and slm_patch:
            patch = slm_patch
            source = "slm"
        else:
            # Deterministic fallback: the guaranteed remediate() merge, no model.
            patch = FixSuggester(detector, None).suggest(cast("dict[str, object]", body.deployment), findings)
            source = "deterministic"

        # A non-dict deployment can't be merged; base the proposal on {} so the
        # endpoint stays fail-soft (never 500) — mirrors misconfig-check.
        base = body.deployment if isinstance(body.deployment, dict) else {}
        proposal = fix_manager.propose(base, patch)
        return {"fix_id": proposal.fix_id, "patch": patch, "source": source}

    @app.post(
        "/admin/deployments/fixes/{fix_id}/approve",
        dependencies=[Depends(RequireCapability(resource="admin:deploy", action="write"))],
        response_model=None,
    )
    async def post_approve_fix(fix_id: str, body: FixDecisionRequest | None = None) -> dict[str, object]:
        """Approve a parked fix and return its merged config to apply.

        With ``{"retry": true}`` and a redeploy hook wired on
        ``app.state._deployment_redeploy_fn``, re-runs the deploy path with the
        merged config; otherwise the merged config is returned for the caller to
        redeploy (never blocks).
        """
        decision = body or FixDecisionRequest()
        try:
            proposal = fix_manager.approve(fix_id)
        except FixApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        merged = proposal.merged_config()
        result: dict[str, object] = {
            "fix_id": fix_id,
            "status": proposal.status,
            "merged_config": merged,
        }
        if decision.retry:
            redeploy = getattr(app.state, "_deployment_redeploy_fn", None)
            if redeploy is not None:
                try:
                    result["retry_result"] = redeploy(merged)
                    result["retried"] = True
                except Exception as exc:  # fail-soft: never 500 on a redeploy error
                    logger.warning("fix redeploy failed", exc_info=True)
                    result["retried"] = False
                    result["retry_error"] = str(exc)
            else:
                result["retried"] = False
                result["note"] = "no redeploy hook wired; apply merged_config to redeploy"
        return result

    @app.post(
        "/admin/deployments/fixes/{fix_id}/reject",
        dependencies=[Depends(RequireCapability(resource="admin:deploy", action="write"))],
        response_model=None,
    )
    async def post_reject_fix(fix_id: str, body: FixDecisionRequest | None = None) -> dict[str, object]:
        """Reject a parked fix, recording an optional ``reason``."""
        decision = body or FixDecisionRequest()
        try:
            proposal = fix_manager.reject(fix_id, decision.reason)
        except FixApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "fix_id": fix_id,
            "status": proposal.status,
            "reason": proposal.reason,
        }
