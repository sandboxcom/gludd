"""Empirical challenger selection for eligible Azure model deployments."""

from __future__ import annotations

from collections.abc import Sequence

from general_ludd.self_improve._candidate_attempt import CandidateAttempt
from general_ludd.self_improve.azure_model_selection_types import (
    AzureModelSelectionReason,
    EligibleAzureModel,
)
from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
)
from general_ludd.self_improve.model_candidates import ModelCandidateProvider


def _observations(
    candidate: EligibleAzureModel,
    classification: CandidateTaskClassification,
    attempts: Sequence[CandidateAttempt],
) -> tuple[int, int]:
    matched = tuple(
        attempt
        for attempt in attempts
        if attempt.is_evaluated
        and attempt.prediction.provider is ModelCandidateProvider.AZURE_CONTAINER_APP
        and attempt.prediction.task_type is classification.task_type
        and attempt.prediction.task_kind == classification.task_kind
        and attempt.prediction.candidate_identity_digest == candidate.identity_digest
    )
    return len(matched), sum(attempt.accepted for attempt in matched)


def choose_azure_model(
    candidates: tuple[EligibleAzureModel, ...],
    classification: CandidateTaskClassification,
    attempts: Sequence[CandidateAttempt],
) -> tuple[EligibleAzureModel, AzureModelSelectionReason, int, int, float]:
    """Choose an untested challenger or the best empirical deployment."""
    scored = tuple(
        (candidate, *_observations(candidate, classification, attempts))
        for candidate in candidates
    )
    untested = tuple(item for item in scored if item[1] == 0)
    if untested:
        candidate, trials, accepted = min(
            untested,
            key=lambda item: (
                -item[0].operational_availability,
                item[0].hourly_cost_microusd,
                item[0].model.storage_bytes,
                -item[0].model.downloads,
                item[0].identity_digest,
            ),
        )
        reason = (
            AzureModelSelectionReason.OPERATIONAL_FAILOVER
            if candidate.operational_failover
            else AzureModelSelectionReason.LEAST_TESTED_CHALLENGER
        )
        return candidate, reason, trials, accepted, 0.5
    ranked = tuple(
        (candidate, trials, accepted, (1.0 + accepted) / (2.0 + trials))
        for candidate, trials, accepted in scored
    )
    candidate, trials, accepted, posterior = min(
        ranked,
        key=lambda item: (
            -item[3],
            -item[0].operational_availability,
            item[0].hourly_cost_microusd,
            item[0].model.storage_bytes,
            item[0].identity_digest,
        ),
    )
    reason = (
        AzureModelSelectionReason.OPERATIONAL_FAILOVER
        if candidate.operational_failover
        else AzureModelSelectionReason.EMPIRICAL_QUALITY
    )
    return candidate, reason, trials, accepted, posterior


__all__ = ["choose_azure_model"]
