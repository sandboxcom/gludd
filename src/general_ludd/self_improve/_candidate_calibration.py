"""Durable public evidence and prequential calibration for model predictions."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve._candidate_attempt import (
    CandidateAttempt,
    CandidateAttemptOutcome,
)
from general_ludd.self_improve._candidate_prediction import (
    CandidatePrediction,
    require_digest,
    stable_digest,
    stratum_key,
)
from general_ludd.self_improve.model_candidates import ModelCandidateProvider
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_COLLECTION = "self_improve.model_routing"
_SCHEMA_VERSION = 1


class CalibrationSkipReason(StrEnum):
    """Reasons an observable attempt must not change model-quality evidence."""

    PRIVATE_SCOPE = "private_scope"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


@dataclass(frozen=True, slots=True)
class CalibrationUpdate:
    """Persistence decision and event-ready trace for one observed attempt."""

    persisted: bool
    record_count: int | None
    skip_reason: CalibrationSkipReason | None
    trace: dict[str, object]


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Prequential Brier skill against the task-stratum empirical baseline."""

    evaluated_attempts: int
    model_brier_score: float | None
    baseline_brier_score: float | None
    brier_skill_score: float | None
    mean_predicted_acceptance: float | None
    observed_acceptance_rate: float | None


def _attempt_trace(
    attempt: CandidateAttempt,
    *,
    persisted: bool,
    skip_reason: CalibrationSkipReason | None,
) -> dict[str, object]:
    prediction = attempt.prediction
    return {
        "attempt_digest": attempt.attempt_digest,
        "candidate_identity_digest": prediction.candidate_identity_digest,
        "evaluation_stratum_digest": prediction.evaluation_stratum_digest,
        "event": (
            "SELF_IMPROVE_MODEL_CALIBRATION_UPDATED"
            if persisted
            else "SELF_IMPROVE_MODEL_CALIBRATION_SKIPPED"
        ),
        "outcome": attempt.outcome.value,
        "persisted": persisted,
        "prediction_digest": prediction.prediction_digest,
        "provider": prediction.provider.value,
        "schema_version": _SCHEMA_VERSION,
        "skip_reason": None if skip_reason is None else skip_reason.value,
        "task_type": prediction.task_type.value,
    }


def _evidence_payload(attempt: CandidateAttempt) -> dict[str, object]:
    prediction = attempt.prediction
    return {
        "accepted": attempt.accepted,
        "attempt_digest": attempt.attempt_digest,
        "blocker_count": attempt.blocker_count,
        "candidate_identity_digest": prediction.candidate_identity_digest,
        "collection": _COLLECTION,
        "evaluation_score": attempt.evaluation_score,
        "evaluation_stratum_digest": prediction.evaluation_stratum_digest,
        "evaluator_digest": prediction.evaluator_digest,
        "observed_cost_microusd": attempt.observed_cost_microusd,
        "observed_input_tokens": attempt.observed_input_tokens,
        "observed_latency_ms": attempt.observed_latency_ms,
        "observed_output_tokens": attempt.observed_output_tokens,
        "prediction_digest": prediction.prediction_digest,
        "predicted_acceptance": prediction.predicted_acceptance,
        "predicted_cost_microusd": prediction.predicted_cost_microusd,
        "predicted_input_tokens": prediction.predicted_input_tokens,
        "predicted_latency_ms": prediction.predicted_latency_ms,
        "predicted_output_tokens": prediction.predicted_output_tokens,
        "privacy_policy_digest": prediction.privacy_policy_digest,
        "prompt_protocol_digest": prediction.prompt_protocol_digest,
        "provider": prediction.provider.value,
        "sampling_digest": prediction.sampling_digest,
        "schema_version": _SCHEMA_VERSION,
        "task_kind": prediction.task_kind,
        "task_type": prediction.task_type.value,
    }


def record_calibration_attempt(
    store: CapabilityEvidenceStore,
    attempt: CandidateAttempt,
    *,
    privacy_approved: bool,
    trace_sink: Callable[[Mapping[str, object]], None] | None = None,
) -> CalibrationUpdate:
    """Persist evaluated public evidence and always expose a censored decision trace."""
    if not isinstance(store, CapabilityEvidenceStore):
        raise ValueError("store must be a CapabilityEvidenceStore")
    if not isinstance(attempt, CandidateAttempt):
        raise ValueError("attempt must be a CandidateAttempt")
    if not isinstance(privacy_approved, bool):
        raise ValueError("privacy_approved must be an explicit boolean")
    skip_reason: CalibrationSkipReason | None = None
    if not privacy_approved:
        skip_reason = CalibrationSkipReason.PRIVATE_SCOPE
    elif not attempt.is_evaluated:
        skip_reason = CalibrationSkipReason.INFRASTRUCTURE_FAILURE

    record_count: int | None = None
    if skip_reason is None:
        payload = _evidence_payload(attempt)
        record = {**payload, "evidence_digest": stable_digest(payload)}
        record_count = store.register_evidence(record)
    trace = _attempt_trace(
        attempt,
        persisted=record_count is not None,
        skip_reason=skip_reason,
    )
    if trace_sink is not None:
        trace_sink(trace)
    return CalibrationUpdate(
        persisted=record_count is not None,
        record_count=record_count,
        skip_reason=skip_reason,
        trace=trace,
    )


_EVIDENCE_KEYS = frozenset(
    {
        "accepted",
        "attempt_digest",
        "blocker_count",
        "candidate_identity_digest",
        "collection",
        "evaluation_score",
        "evaluation_stratum_digest",
        "evaluator_digest",
        "evidence_digest",
        "observed_cost_microusd",
        "observed_input_tokens",
        "observed_latency_ms",
        "observed_output_tokens",
        "prediction_digest",
        "predicted_acceptance",
        "predicted_cost_microusd",
        "predicted_input_tokens",
        "predicted_latency_ms",
        "predicted_output_tokens",
        "privacy_policy_digest",
        "prompt_protocol_digest",
        "provider",
        "registered_at",
        "sampling_digest",
        "schema_version",
        "task_kind",
        "task_type",
    }
)


def _valid_record_header(record: Mapping[str, Any]) -> bool:
    registered_at = record.get("registered_at")
    return (
        set(record) == _EVIDENCE_KEYS
        and not isinstance(registered_at, bool)
        and isinstance(registered_at, (int, float))
        and math.isfinite(float(registered_at))
        and registered_at >= 0
        and record.get("collection") == _COLLECTION
        and record.get("schema_version") == _SCHEMA_VERSION
    )


def _prediction_from_record(record: Mapping[str, Any]) -> CandidatePrediction:
    return CandidatePrediction(
        candidate_identity_digest=cast(str, record["candidate_identity_digest"]),
        provider=ModelCandidateProvider(cast(str, record["provider"])),
        task_type=TaskType(cast(str, record["task_type"])),
        task_kind=cast(str, record["task_kind"]),
        evaluation_stratum_digest=cast(str, record["evaluation_stratum_digest"]),
        prompt_protocol_digest=cast(str, record["prompt_protocol_digest"]),
        evaluator_digest=cast(str, record["evaluator_digest"]),
        sampling_digest=cast(str, record["sampling_digest"]),
        privacy_policy_digest=cast(str, record["privacy_policy_digest"]),
        predicted_acceptance=cast(float, record["predicted_acceptance"]),
        predicted_latency_ms=cast(int, record["predicted_latency_ms"]),
        predicted_input_tokens=cast(int, record["predicted_input_tokens"]),
        predicted_output_tokens=cast(int, record["predicted_output_tokens"]),
        predicted_cost_microusd=cast(int, record["predicted_cost_microusd"]),
    )


def _attempt_from_record(record: Mapping[str, Any]) -> CandidateAttempt | None:
    if not _valid_record_header(record):
        return None
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"evidence_digest", "registered_at"}
    }
    if record.get("evidence_digest") != stable_digest(payload):
        return None
    try:
        prediction = _prediction_from_record(record)
        accepted = record["accepted"]
        if not isinstance(accepted, bool):
            return None
        attempt = CandidateAttempt(
            prediction=prediction,
            outcome=(
                CandidateAttemptOutcome.ACCEPTED
                if accepted
                else CandidateAttemptOutcome.REJECTED
            ),
            evaluation_score=cast(float, record["evaluation_score"]),
            blocker_count=cast(int, record["blocker_count"]),
            observed_latency_ms=cast(int, record["observed_latency_ms"]),
            observed_input_tokens=cast(int, record["observed_input_tokens"]),
            observed_output_tokens=cast(int, record["observed_output_tokens"]),
            observed_cost_microusd=cast(int, record["observed_cost_microusd"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        record.get("prediction_digest") != prediction.prediction_digest
        or record.get("attempt_digest") != attempt.attempt_digest
    ):
        return None
    return attempt


def load_calibration_attempts(
    store: CapabilityEvidenceStore,
    *,
    evaluation_stratum_digest: str,
) -> tuple[CandidateAttempt, ...]:
    """Load only untampered records for one exact evaluation stratum."""
    if not isinstance(store, CapabilityEvidenceStore):
        raise ValueError("store must be a CapabilityEvidenceStore")
    stratum = require_digest(
        evaluation_stratum_digest,
        "evaluation_stratum_digest",
    )
    attempts: list[CandidateAttempt] = []
    for record in store.list_all():
        if record.get("evaluation_stratum_digest") != stratum:
            continue
        attempt = _attempt_from_record(record)
        if attempt is not None:
            attempts.append(attempt)
    return tuple(attempts)


def prequential_brier_skill(
    attempts: Sequence[CandidateAttempt],
) -> CalibrationReport:
    """Compare pre-call probabilities with a causal task-stratum base rate."""
    observations = tuple(attempts)
    if not all(isinstance(item, CandidateAttempt) for item in observations):
        raise ValueError("attempts must contain CandidateAttempt values")
    evaluated = tuple(item for item in observations if item.is_evaluated)
    if not evaluated:
        return CalibrationReport(0, None, None, None, None, None)
    first_key = stratum_key(evaluated[0].prediction)
    if any(stratum_key(item.prediction) != first_key for item in evaluated[1:]):
        raise ValueError("calibration attempts must share one exact evaluation stratum")
    accepted = 0
    rejected = 0
    model_errors: list[float] = []
    baseline_errors: list[float] = []
    probabilities: list[float] = []
    labels: list[float] = []
    for attempt in evaluated:
        label = 1.0 if attempt.accepted else 0.0
        probability = attempt.prediction.predicted_acceptance
        baseline = (1.0 + accepted) / (2.0 + accepted + rejected)
        model_errors.append((probability - label) ** 2)
        baseline_errors.append((baseline - label) ** 2)
        probabilities.append(probability)
        labels.append(label)
        if attempt.accepted:
            accepted += 1
        else:
            rejected += 1
    model_brier = sum(model_errors) / len(model_errors)
    baseline_brier = sum(baseline_errors) / len(baseline_errors)
    skill = None if baseline_brier == 0.0 else 1.0 - model_brier / baseline_brier
    return CalibrationReport(
        evaluated_attempts=len(evaluated),
        model_brier_score=model_brier,
        baseline_brier_score=baseline_brier,
        brier_skill_score=skill,
        mean_predicted_acceptance=sum(probabilities) / len(probabilities),
        observed_acceptance_rate=sum(labels) / len(labels),
    )


__all__ = (
    "CalibrationReport",
    "CalibrationSkipReason",
    "CalibrationUpdate",
    "load_calibration_attempts",
    "prequential_brier_skill",
    "record_calibration_attempt",
)
