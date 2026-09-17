"""Secret-safe model lifecycle event rendering for self-improvement."""

from __future__ import annotations

import json

from general_ludd.local_model import LocalModelConfig
from general_ludd.self_improve.model_candidate_planner import PlannedModelCandidate
from general_ludd.self_improve.model_lifecycle import (
    AcquiredModel,
    ModelAcquisitionEvent,
    ModelArtifactIdentity,
)


def report_model_resolution_failure(
    model: LocalModelConfig,
    reason: str,
) -> None:
    """Publish one bounded, JSON-escaped catalog resolution failure."""
    print(
        "SELF_IMPROVE_MODEL_UNAVAILABLE "
        f"model={model.name} error={json.dumps(reason[:1000])}",
        flush=True,
    )


def planned_artifact_identity(
    candidate: PlannedModelCandidate,
) -> ModelArtifactIdentity:
    """Adapt a planner result to the lifecycle's immutable artifact boundary."""
    return ModelArtifactIdentity(
        model_id=candidate.config.name,
        repo_id=candidate.config.repo,
        filename=candidate.config.filename,
        revision=candidate.resolved_revision,
    )


def report_model_acquisition_event(event: ModelAcquisitionEvent) -> None:
    """Publish one secret-safe, bounded acquisition phase marker."""
    print(
        "SELF_IMPROVE_MODEL_ACQUISITION "
        f"phase={event.phase.value} operation={event.operation_id} "
        f"repository={event.repository_key} model={event.model_key or 'none'} "
        f"revision={event.revision or 'none'} "
        f"elapsed_seconds={event.elapsed_seconds:.2f} "
        f"failure={event.failure.value if event.failure is not None else 'none'}",
        flush=True,
    )


def report_model_release(model: AcquiredModel) -> None:
    """Publish whether an acquired model's lease was released."""
    try:
        released = not model.lease_path.exists()
    except OSError:
        released = False
    print(
        "SELF_IMPROVE_MODEL_RELEASED "
        f"model={model.model_id} lease_released={str(released).lower()}",
        flush=True,
    )


__all__ = [
    "planned_artifact_identity",
    "report_model_acquisition_event",
    "report_model_release",
    "report_model_resolution_failure",
]
