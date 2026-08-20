"""HTTP router: expert collection endpoint stubs.

Surfaces domain expert collections over HTTP so they are accessible
via the daemon alongside the existing PSK-gated admin and public API routes:

  - ``POST /api/materials/select``   -- screen/rank material candidates
    (delegates to :func:`general_ludd.materials.select_materials`).
  - ``POST /api/chemistry/resolve``  -- dispatch one typed, allowlisted
    chemistry operation with bounded execution and idempotent replay.
  - ``POST /api/ai_ml/query``        -- route an AI/ML expert request to the
    smallest qualified role set (delegates to
    :class:`general_ludd.ai_ml.router.ExpertRouter`).
  - ``POST /api/language/execute``   -- execute one bounded language operation
    through the daemon-owned implementation.
  - ``GET  /api/git_release/assess`` -- collect read-only repo evidence
    (delegates to :func:`general_ludd.git_release.collect_repo_evidence`).

These are stubs: they translate a JSON body into the collection's typed
entry point and serialize the result back to JSON. PSK auth is applied by
the daemon middleware (none of these paths are in ``_PUBLIC_PATHS_FROZEN``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, StrictFloat, StrictStr, field_validator

from general_ludd.routers._runtime import IdempotencyStore, StrictRuntimeRequest

logger = logging.getLogger(__name__)


class ChemistryRequestError(ValueError):
    """Signal malformed typed chemistry input without exposing daemon faults."""


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class MaterialsSelectRequest(BaseModel):
    """Body for ``POST /api/materials/select``.

    ``requirements`` is the raw design-requirements dict consumed by
    :func:`select_materials` (load cases, environment, life, manufacturing
    constraints). ``candidates`` optionally narrows the screened set.
    """

    requirements: dict[str, Any] = Field(default_factory=dict)
    candidates: list[str] | None = None


class ChemistryResolveRequest(StrictRuntimeRequest):
    """Body for ``POST /api/chemistry/resolve``.

    ``operation`` selects a fixed daemon-owned chemistry function and
    ``request`` carries its bounded typed inputs.
    """

    operation: Literal[
        "route",
        "identity",
        "reaction",
        "molar_mass",
        "moles",
        "dilution",
        "yield",
        "hazard",
    ] = "route"
    request: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: StrictFloat = Field(default=15.0, ge=0.1, le=30.0)
    idempotency_key: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )

    @field_validator("request")
    @classmethod
    def _bound_request(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 128 or len(json.dumps(value, default=str)) > 262_144:
            raise ValueError("chemistry request exceeds the bounded payload size")
        return value


def _dispatch_chemistry(
    operation: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch one allowlisted chemistry operation inside the daemon plane."""
    from general_ludd.chemistry import (
        analyze_reaction,
        molar_mass,
        resolve_identity,
        route_chemistry_task,
        screen_hazards,
        stoichiometry_dilution,
        stoichiometry_moles,
        stoichiometry_yield,
    )

    if operation == "route":
        return route_chemistry_task(request)
    if operation == "identity":
        return resolve_identity(request)
    if operation == "reaction":
        return analyze_reaction(request)
    try:
        if operation == "molar_mass":
            return molar_mass(str(request.get("formula", "")))
        if operation == "moles":
            return stoichiometry_moles(
                mass_g=float(request.get("mass_g", 0.0)),
                formula=str(request.get("formula", "")),
                mass_uncertainty=float(request.get("mass_uncertainty", 0.0)),
            )
        if operation == "dilution":
            return stoichiometry_dilution(
                request.get("c1"),
                request.get("v1"),
                request.get("c2"),
                request.get("v2"),
            )
        if operation == "yield":
            return stoichiometry_yield(
                actual_g=float(request.get("actual_g", 0.0)),
                theoretical_g=float(request.get("theoretical_g", 0.0)),
                actual_unc=float(request.get("actual_unc", 0.0)),
                theoretical_unc=float(request.get("theoretical_unc", 0.0)),
            )
    except (TypeError, ValueError) as exc:
        raise ChemistryRequestError("invalid chemistry operation input") from exc
    if operation == "hazard":
        return screen_hazards(request)
    raise ChemistryRequestError(f"unsupported chemistry operation: {operation}")


class ExpertQueryRequest(BaseModel):
    """Body for ``POST /api/ai_ml/query``.

    Mirrors the required identifying fields of
    :class:`general_ludd.ai_ml.schemas.ExpertRequest`. Optional fields
    (``approval_token``, constraints, inputs) are accepted but not required
    by the stub.
    """

    request_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    task: str = Field(default="question")
    query: str = Field(min_length=1)
    approval_token: str | None = None
    offline: bool = False
    deadline_s: int = Field(default=300, ge=1)
    budget_usd: float = Field(default=0.0, ge=0.0)


class LanguageOperationRequest(BaseModel):
    """Body for authenticated ``POST /api/language/execute``."""

    operation: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register authenticated domain-expert and language routes on ``app``."""
    chemistry_store = IdempotencyStore()

    @app.post("/api/materials/select")
    async def materials_select(body: MaterialsSelectRequest) -> dict[str, Any]:
        from general_ludd.materials import select_materials

        try:
            result = select_materials(body.requirements, body.candidates)
        except Exception as err:
            logger.exception("materials.select_materials failed")
            raise HTTPException(status_code=500, detail="materials select failed") from err
        return result

    @app.post("/api/chemistry/resolve")
    async def chemistry_resolve(body: ChemistryResolveRequest) -> dict[str, Any]:
        async def _run() -> dict[str, Any]:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        _dispatch_chemistry,
                        body.operation,
                        body.request,
                    ),
                    timeout=body.timeout_seconds,
                )
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=504,
                    detail="chemistry operation timed out",
                ) from exc
            except ChemistryRequestError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except Exception as err:
                logger.exception("chemistry operation failed")
                raise HTTPException(
                    status_code=500,
                    detail="chemistry resolve failed",
                ) from err

        return await chemistry_store.run(
            key=body.idempotency_key,
            payload=body.model_dump(mode="json", exclude_none=True),
            producer=_run,
        )

    @app.post("/api/ai_ml/query")
    async def ai_ml_query(body: ExpertQueryRequest) -> dict[str, Any]:
        from general_ludd.ai_ml.router import ExpertRouter
        from general_ludd.ai_ml.schemas import Constraints, ExpertRequest, ExpertTask

        try:
            try:
                task = ExpertTask(body.task)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"invalid task: {body.task!r}",
                ) from exc
            request = ExpertRequest(
                request_id=body.request_id,
                tenant_id=body.tenant_id,
                task=task,
                query=body.query,
                approval_token=body.approval_token,
                constraints=Constraints(
                    deadline_s=body.deadline_s,
                    budget_usd=body.budget_usd,
                    offline=body.offline,
                ),
            )
            decision = ExpertRouter().route(request)
        except HTTPException:
            raise
        except Exception as err:
            logger.exception("ai_ml router failed")
            raise HTTPException(status_code=500, detail="ai_ml query failed") from err
        return {
            "request_id": decision.request_id,
            "matched_roles": list(decision.matched_roles),
            "refusal_reason": decision.refusal_reason,
        }

    @app.post("/api/language/execute")
    async def language_execute(body: LanguageOperationRequest) -> dict[str, Any]:
        from general_ludd.language.operations import execute_language_operation

        try:
            result = execute_language_operation(body.operation, body.payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as err:
            logger.exception("language operation failed")
            raise HTTPException(status_code=500, detail="language operation failed") from err
        return {"result": result}

    @app.get("/api/git_release/assess")
    async def git_release_assess(
        path: str = Query(..., description="Repository path to assess"),
    ) -> dict[str, Any]:
        from general_ludd.git_release import collect_repo_evidence

        try:
            evidence = collect_repo_evidence(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (NotADirectoryError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as err:
            logger.exception("git_release.collect_repo_evidence failed")
            raise HTTPException(status_code=500, detail="repo assess failed") from err
        return {
            "path": evidence.path,
            "head_sha": evidence.head_sha,
            "branch": evidence.branch,
            "is_dirty": evidence.is_dirty,
            "is_detached": evidence.is_detached,
        }
