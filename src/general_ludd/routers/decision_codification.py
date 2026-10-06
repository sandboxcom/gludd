"""Authenticated, content-safe decision-log analysis routes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import FastAPI, HTTPException, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from general_ludd.decision_codification.service import (
    AnalysisRejectionReason,
    DecisionAnalysis,
    DecisionAnalysisError,
    DecisionCodificationAdapter,
)
from general_ludd.replay.schema import (
    BoundedIdentifier,
    SafeRunId,
    Sha256Digest,
    decode_replay_json_object,
)

MAX_API_ANALYSIS_BODY_BYTES = 64 * 1024
MAX_API_ANALYSIS_RUN_IDS = 256
MAX_API_ANALYSIS_CANDIDATES = 128
_MAX_CANDIDATE_LIFETIME = timedelta(days=366)
_INVALID_DETAIL = "invalid decision analysis request"
_NOT_FOUND_DETAIL = "decision analysis not found"
_TOO_LARGE_DETAIL = "decision analysis request too large"
_UNAVAILABLE_DETAIL = "decision analysis unavailable"


class _StrictApiModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class DecisionAnalysisRequest(_StrictApiModel):
    """Bounded analysis inputs; lifecycle approval is deliberately absent."""

    project_id: BoundedIdentifier
    run_ids: Annotated[
        tuple[SafeRunId, ...],
        Field(min_length=1, max_length=MAX_API_ANALYSIS_RUN_IDS),
    ]
    training_recipe_digest: Sha256Digest
    dependency_lock_digest: Sha256Digest
    created_at: datetime
    expires_at: datetime
    maximum_use_count: Annotated[int, Field(ge=1, le=1_000_000)]
    estimated_tokens_per_call: Annotated[int, Field(ge=0, le=10_000_000)] = 0

    @field_validator("run_ids", mode="before")
    @classmethod
    def _json_run_ids_to_tuple(cls, value: object) -> object:
        if type(value) is list:
            return tuple(cast("list[object]", value))
        return value

    @field_validator("created_at", "expires_at", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: object) -> object:
        if type(value) is not str:
            return value
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return value
        return parsed.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_bounds(self) -> DecisionAnalysisRequest:
        if len(set(self.run_ids)) != len(self.run_ids):
            raise ValueError("analysis run IDs must be unique")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("analysis timestamps must be timezone-aware")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("analysis timestamps must be timezone-aware")
        lifetime = self.expires_at - self.created_at
        if lifetime <= timedelta(0) or lifetime > _MAX_CANDIDATE_LIFETIME:
            raise ValueError("candidate lifetime is outside the API bound")
        return self


class DecisionCandidateSummary(_StrictApiModel):
    """Digest/count projection of one candidate; no evidence is serialized."""

    cluster_digest: Sha256Digest
    evidence_count: Annotated[int, Field(ge=0)]
    candidate_digest: Sha256Digest
    validation_report_digest: Sha256Digest
    holdout_report_digest: Sha256Digest


class DecisionRejectionSummary(_StrictApiModel):
    """Closed rejection reason and aggregate count."""

    reason: AnalysisRejectionReason
    count: Annotated[int, Field(ge=1)]


class DecisionAnalysisResponse(_StrictApiModel):
    """Bounded, content-free projection of an offline analysis result."""

    bundles_read: Annotated[int, Field(ge=0)]
    events_seen: Annotated[int, Field(ge=0)]
    events_eligible: Annotated[int, Field(ge=0)]
    candidate_count: Annotated[int, Field(ge=0, le=MAX_API_ANALYSIS_CANDIDATES)]
    candidates: Annotated[
        tuple[DecisionCandidateSummary, ...],
        Field(max_length=MAX_API_ANALYSIS_CANDIDATES),
    ]
    rejection_counts: Annotated[
        tuple[DecisionRejectionSummary, ...],
        Field(max_length=len(AnalysisRejectionReason)),
    ]

    @model_validator(mode="after")
    def _candidate_count_matches(self) -> DecisionAnalysisResponse:
        if self.candidate_count != len(self.candidates):
            raise ValueError("candidate count does not match summaries")
        return self


async def _parse_request(request: Request) -> DecisionAnalysisRequest:
    """Read one JSON body without retaining or reflecting oversized content."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            raise HTTPException(status_code=422, detail=_INVALID_DETAIL) from None
        if declared_length < 0:
            raise HTTPException(status_code=422, detail=_INVALID_DETAIL)
        if declared_length > MAX_API_ANALYSIS_BODY_BYTES:
            raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)

    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_API_ANALYSIS_BODY_BYTES:
            raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)
        chunks.append(chunk)
    if received == 0:
        raise HTTPException(status_code=422, detail=_INVALID_DETAIL)

    try:
        decoded = decode_replay_json_object(b"".join(chunks))
        return DecisionAnalysisRequest.model_validate(decoded, strict=True)
    except (TypeError, ValueError, ValidationError):
        raise HTTPException(status_code=422, detail=_INVALID_DETAIL) from None


def _adapter(app: FastAPI) -> DecisionCodificationAdapter:
    """Return the opt-in adapter or fail without constructing a substitute."""
    value = getattr(app.state, "decision_codification", None)
    if value is None or not callable(getattr(value, "analyze", None)):
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
    if not isinstance(getattr(value, "project_id", None), str):
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
    return cast("DecisionCodificationAdapter", value)


def _enforce_project_scope(
    request: Request,
    adapter: DecisionCodificationAdapter,
    requested_project_id: str,
) -> None:
    """Bind request, authenticated claim, and injected adapter to one project."""
    bound_project_id = adapter.project_id
    claimed_project_id = getattr(request.state, "project_id", None)
    if requested_project_id != bound_project_id:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    if claimed_project_id is not None and (
        not isinstance(claimed_project_id, str)
        or claimed_project_id != bound_project_id
    ):
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)


def _project_result(result: DecisionAnalysis) -> DecisionAnalysisResponse:
    """Drop all executable artifacts and source evidence from the wire result."""
    if len(result.candidates) > MAX_API_ANALYSIS_CANDIDATES:
        raise ValueError("decision analysis response exceeds candidate bound")
    candidates = tuple(
        DecisionCandidateSummary(
            cluster_digest=candidate.cluster_digest,
            evidence_count=candidate.evidence_count,
            candidate_digest=candidate.bundle.candidate_digest,
            validation_report_digest=candidate.validation_report.report_digest,
            holdout_report_digest=candidate.holdout_report.report_digest,
        )
        for candidate in result.candidates
    )
    rejections = tuple(
        DecisionRejectionSummary(reason=reason, count=count)
        for reason, count in result.rejection_counts
    )
    return DecisionAnalysisResponse(
        bundles_read=result.bundles_read,
        events_seen=result.events_seen,
        events_eligible=result.events_eligible,
        candidate_count=len(candidates),
        candidates=candidates,
        rejection_counts=rejections,
    )


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register the authenticated, analysis-only decision-codification API."""

    @app.post(
        "/api/v1/decision-codification/analyze",
        response_model=DecisionAnalysisResponse,
    )
    async def analyze_decision_logs(request: Request) -> DecisionAnalysisResponse:
        payload = await _parse_request(request)
        adapter = _adapter(app)
        _enforce_project_scope(request, adapter, payload.project_id)
        try:
            result = await asyncio.to_thread(
                adapter.analyze,
                payload.run_ids,
                training_recipe_digest=payload.training_recipe_digest,
                dependency_lock_digest=payload.dependency_lock_digest,
                created_at=payload.created_at,
                expires_at=payload.expires_at,
                maximum_use_count=payload.maximum_use_count,
                estimated_tokens_per_call=payload.estimated_tokens_per_call,
            )
            return _project_result(result)
        except DecisionAnalysisError:
            raise HTTPException(status_code=422, detail=_INVALID_DETAIL) from None
        except Exception:
            raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from None


__all__ = [
    "MAX_API_ANALYSIS_BODY_BYTES",
    "MAX_API_ANALYSIS_CANDIDATES",
    "MAX_API_ANALYSIS_RUN_IDS",
    "DecisionAnalysisRequest",
    "DecisionAnalysisResponse",
    "register",
]
