"""Identity-bound model predictions, trials, and empirical calibration.

This module contains no provider clients and accepts no source text, paths, or
credentials.  It can therefore compare local and remote candidates while keeping
project-private material outside durable evidence and shared event payloads.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import NormalDist
from typing import Any, cast

from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve.model_candidates import (
    BackendFailure,
    ModelCandidateProvider,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_TASK_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_COLLECTION = "self_improve.model_routing"
_SCHEMA_VERSION = 1
_MAX_TRIALS = 16
_MAX_TOKENS = 100_000_000
_MAX_COST_MICROUSD = 1_000_000_000_000
_MAX_LATENCY_MS = 86_400_000
_MAX_BLOCKERS = 10_000
_PRIOR_STRENGTH = 2.0
_LOWER_QUANTILE_Z = NormalDist().inv_cdf(0.1)


def _stable_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _bounded_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ValueError(f"{field_name} must be an integer in {minimum}..{maximum}")
    return value


def _bounded_probability(value: object, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"{field_name} must be a finite probability")
    return float(value)


class CandidateAttemptOutcome(StrEnum):
    """Quality and infrastructure dispositions for one explicit candidate call."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class CalibrationSkipReason(StrEnum):
    """Reasons an observable attempt must not change model-quality evidence."""

    PRIVATE_SCOPE = "private_scope"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class CandidateTrialPurpose(StrEnum):
    """Why a candidate appears in a bounded, explicit trial plan."""

    PREFERRED = "preferred"
    CHALLENGE = "challenge"
    RANKED = "ranked"


@dataclass(frozen=True)
class CandidatePrediction:
    """Pre-execution behavior prediction for one immutable model identity."""

    candidate_identity_digest: str
    provider: ModelCandidateProvider
    task_type: TaskType
    task_kind: str
    evaluation_stratum_digest: str
    prompt_protocol_digest: str
    evaluator_digest: str
    sampling_digest: str
    privacy_policy_digest: str
    predicted_acceptance: float
    predicted_latency_ms: int
    predicted_input_tokens: int
    predicted_output_tokens: int
    predicted_cost_microusd: int

    def __post_init__(self) -> None:
        """Reject mutable identities, raw work labels, and unbounded estimates."""
        _require_digest(self.candidate_identity_digest, "candidate_identity_digest")
        if not isinstance(self.provider, ModelCandidateProvider):
            raise ValueError("provider must be a ModelCandidateProvider")
        if not isinstance(self.task_type, TaskType):
            raise ValueError("task_type must be a TaskType")
        if not isinstance(self.task_kind, str) or _TASK_KIND_RE.fullmatch(self.task_kind) is None:
            raise ValueError("task_kind must be one bounded categorical label")
        _require_digest(self.evaluation_stratum_digest, "evaluation_stratum_digest")
        _require_digest(self.prompt_protocol_digest, "prompt_protocol_digest")
        _require_digest(self.evaluator_digest, "evaluator_digest")
        _require_digest(self.sampling_digest, "sampling_digest")
        _require_digest(self.privacy_policy_digest, "privacy_policy_digest")
        object.__setattr__(
            self,
            "predicted_acceptance",
            _bounded_probability(self.predicted_acceptance, "predicted_acceptance"),
        )
        _bounded_integer(
            self.predicted_latency_ms,
            "predicted_latency_ms",
            minimum=1,
            maximum=_MAX_LATENCY_MS,
        )
        _bounded_integer(
            self.predicted_input_tokens,
            "predicted_input_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        _bounded_integer(
            self.predicted_output_tokens,
            "predicted_output_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        _bounded_integer(
            self.predicted_cost_microusd,
            "predicted_cost_microusd",
            minimum=0,
            maximum=_MAX_COST_MICROUSD,
        )

    def _payload(self) -> dict[str, object]:
        return {
            "candidate_identity_digest": self.candidate_identity_digest,
            "evaluation_stratum_digest": self.evaluation_stratum_digest,
            "evaluator_digest": self.evaluator_digest,
            "predicted_acceptance": self.predicted_acceptance,
            "predicted_cost_microusd": self.predicted_cost_microusd,
            "predicted_input_tokens": self.predicted_input_tokens,
            "predicted_latency_ms": self.predicted_latency_ms,
            "predicted_output_tokens": self.predicted_output_tokens,
            "privacy_policy_digest": self.privacy_policy_digest,
            "prompt_protocol_digest": self.prompt_protocol_digest,
            "provider": self.provider.value,
            "sampling_digest": self.sampling_digest,
            "task_kind": self.task_kind,
            "task_type": self.task_type.value,
        }

    @property
    def prediction_digest(self) -> str:
        """Return a stable digest binding the pre-execution prediction."""
        return _stable_digest(
            {"protocol": "gludd-candidate-prediction-v1", **self._payload()}
        )


@dataclass(frozen=True, slots=True)
class CandidateAttempt:
    """Observed behavior for one prediction, without prompts or source content."""

    prediction: CandidatePrediction
    outcome: CandidateAttemptOutcome
    evaluation_score: float | None
    blocker_count: int
    observed_latency_ms: int
    observed_input_tokens: int
    observed_output_tokens: int
    observed_cost_microusd: int
    failure: BackendFailure | None = None

    def __post_init__(self) -> None:
        """Keep quality outcomes distinct from censored infrastructure failures."""
        if not isinstance(self.prediction, CandidatePrediction):
            raise ValueError("prediction must be a CandidatePrediction")
        if not isinstance(self.outcome, CandidateAttemptOutcome):
            raise ValueError("outcome must be a CandidateAttemptOutcome")
        _bounded_integer(
            self.observed_latency_ms,
            "observed_latency_ms",
            minimum=0,
            maximum=_MAX_LATENCY_MS,
        )
        _bounded_integer(
            self.observed_input_tokens,
            "observed_input_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        _bounded_integer(
            self.observed_output_tokens,
            "observed_output_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        _bounded_integer(
            self.observed_cost_microusd,
            "observed_cost_microusd",
            minimum=0,
            maximum=_MAX_COST_MICROUSD,
        )
        self._validate_disposition()

    def _validate_disposition(self) -> None:
        if self.outcome is CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE:
            if (
                self.evaluation_score is not None
                or not isinstance(self.failure, BackendFailure)
                or self.blocker_count != 0
            ):
                raise ValueError(
                    "infrastructure failures require one censored failure and no quality result"
                )
            return
        if self.failure is not None:
            raise ValueError("evaluated outcomes must not contain infrastructure failure data")
        score = _bounded_probability(self.evaluation_score, "evaluation_score")
        object.__setattr__(self, "evaluation_score", score)
        minimum_blockers = 0 if self.outcome is CandidateAttemptOutcome.ACCEPTED else 1
        blockers = _bounded_integer(
            self.blocker_count,
            "blocker_count",
            minimum=minimum_blockers,
            maximum=_MAX_BLOCKERS,
        )
        if self.outcome is CandidateAttemptOutcome.ACCEPTED and blockers != 0:
            raise ValueError("accepted outcomes must not contain blockers")

    @property
    def is_evaluated(self) -> bool:
        """Return whether deterministic evaluation produced a quality label."""
        return self.outcome is not CandidateAttemptOutcome.INFRASTRUCTURE_FAILURE

    @property
    def accepted(self) -> bool:
        """Return the binary quality label; reject censored infrastructure calls."""
        if not self.is_evaluated:
            raise ValueError("infrastructure failures have no acceptance label")
        return self.outcome is CandidateAttemptOutcome.ACCEPTED

    def _payload(self) -> dict[str, object]:
        return {
            "blocker_count": self.blocker_count,
            "evaluation_score": self.evaluation_score,
            "failure": None if self.failure is None else self.failure.value,
            "observed_cost_microusd": self.observed_cost_microusd,
            "observed_input_tokens": self.observed_input_tokens,
            "observed_latency_ms": self.observed_latency_ms,
            "observed_output_tokens": self.observed_output_tokens,
            "outcome": self.outcome.value,
            "prediction_digest": self.prediction.prediction_digest,
        }

    @property
    def attempt_digest(self) -> str:
        """Return a stable digest for the censored attempt facts."""
        return _stable_digest({"protocol": "gludd-candidate-attempt-v1", **self._payload()})


@dataclass(frozen=True, slots=True)
class CalibrationUpdate:
    """Persistence decision and event-ready trace for one observed attempt."""

    persisted: bool
    record_count: int | None
    skip_reason: CalibrationSkipReason | None
    trace: dict[str, object]


@dataclass(frozen=True, slots=True)
class CandidateRanking:
    """Conservative empirical rank for one immutable candidate prediction."""

    prediction: CandidatePrediction
    evaluated_trials: int
    accepted_trials: int
    posterior_acceptance: float
    conservative_acceptance: float


@dataclass(frozen=True, slots=True)
class CandidateTrial:
    """One explicit candidate invocation in a bounded plan."""

    ordinal: int
    purpose: CandidateTrialPurpose
    prediction: CandidatePrediction
    ranking: CandidateRanking


@dataclass(frozen=True, slots=True)
class CandidateTrialPlan:
    """Immutable local, Azure, or mixed candidate set with explicit concurrency."""

    trials: tuple[CandidateTrial, ...]
    concurrent: bool

    @property
    def candidate_identity_digests(self) -> tuple[str, ...]:
        """Return the only candidates authorized by this plan, in stable order."""
        return tuple(trial.prediction.candidate_identity_digest for trial in self.trials)

    @property
    def plan_digest(self) -> str:
        """Bind order, purpose, predictions, and execution mode."""
        return _stable_digest(
            {
                "concurrent": self.concurrent,
                "protocol": "gludd-candidate-trial-plan-v1",
                "trials": [
                    {
                        "ordinal": trial.ordinal,
                        "prediction_digest": trial.prediction.prediction_digest,
                        "purpose": trial.purpose.value,
                    }
                    for trial in self.trials
                ],
            }
        )


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
        record = {**payload, "evidence_digest": _stable_digest(payload)}
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


def _attempt_from_record(record: Mapping[str, Any]) -> CandidateAttempt | None:
    if set(record) != _EVIDENCE_KEYS:
        return None
    registered_at = record.get("registered_at")
    if (
        isinstance(registered_at, bool)
        or not isinstance(registered_at, (int, float))
        or not math.isfinite(float(registered_at))
        or registered_at < 0
        or record.get("collection") != _COLLECTION
        or record.get("schema_version") != _SCHEMA_VERSION
    ):
        return None
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"evidence_digest", "registered_at"}
    }
    if record.get("evidence_digest") != _stable_digest(payload):
        return None
    try:
        prediction = CandidatePrediction(
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
    stratum = _require_digest(
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


def _stratum_key(prediction: CandidatePrediction) -> tuple[object, ...]:
    return (
        prediction.task_type,
        prediction.task_kind,
        prediction.evaluation_stratum_digest,
        prediction.prompt_protocol_digest,
        prediction.evaluator_digest,
        prediction.sampling_digest,
        prediction.privacy_policy_digest,
    )


def _validated_predictions(
    predictions: Sequence[CandidatePrediction],
) -> tuple[CandidatePrediction, ...]:
    items = tuple(predictions)
    if not all(isinstance(item, CandidatePrediction) for item in items):
        raise ValueError("predictions must contain CandidatePrediction values")
    identities = [item.candidate_identity_digest for item in items]
    if len(identities) != len(set(identities)):
        raise ValueError("predictions must not contain duplicate candidate identities")
    if items and any(_stratum_key(item) != _stratum_key(items[0]) for item in items[1:]):
        raise ValueError("predictions must share one exact evaluation stratum")
    return items


def rank_candidate_predictions(
    predictions: Sequence[CandidatePrediction],
    attempts: Sequence[CandidateAttempt],
) -> tuple[CandidateRanking, ...]:
    """Rank one stratum by a beta-posterior lower bound, then resource cost."""
    candidates = _validated_predictions(predictions)
    observations = tuple(attempts)
    if not all(isinstance(item, CandidateAttempt) for item in observations):
        raise ValueError("attempts must contain CandidateAttempt values")
    rankings: list[CandidateRanking] = []
    for prediction in candidates:
        matching = tuple(
            attempt
            for attempt in observations
            if attempt.is_evaluated
            and attempt.prediction.candidate_identity_digest
            == prediction.candidate_identity_digest
            and attempt.prediction.provider is prediction.provider
            and _stratum_key(attempt.prediction) == _stratum_key(prediction)
        )
        accepted = sum(attempt.accepted for attempt in matching)
        rejected = len(matching) - accepted
        alpha = 1.0 + _PRIOR_STRENGTH * prediction.predicted_acceptance + accepted
        beta = 1.0 + _PRIOR_STRENGTH * (1.0 - prediction.predicted_acceptance) + rejected
        posterior = alpha / (alpha + beta)
        variance = alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1.0))
        rankings.append(
            CandidateRanking(
                prediction=prediction,
                evaluated_trials=len(matching),
                accepted_trials=accepted,
                posterior_acceptance=posterior,
                conservative_acceptance=max(
                    0.0,
                    posterior + _LOWER_QUANTILE_Z * math.sqrt(variance),
                ),
            )
        )
    return tuple(
        sorted(
            rankings,
            key=lambda item: (
                -item.conservative_acceptance,
                item.prediction.predicted_cost_microusd,
                item.prediction.predicted_latency_ms,
                item.prediction.predicted_input_tokens
                + item.prediction.predicted_output_tokens,
                item.prediction.candidate_identity_digest,
            ),
        )
    )


def plan_bounded_candidate_trials(
    predictions: Sequence[CandidatePrediction],
    attempts: Sequence[CandidateAttempt],
    *,
    max_trials: int,
    challenge_trials: int,
    concurrent: bool,
) -> CandidateTrialPlan:
    """Choose explicit preferred and least-tested challengers under a hard bound."""
    candidates = _validated_predictions(predictions)
    if not candidates:
        raise ValueError("at least one candidate prediction is required")
    maximum = _bounded_integer(
        max_trials,
        "max_trials",
        minimum=1,
        maximum=_MAX_TRIALS,
    )
    challenges = _bounded_integer(
        challenge_trials,
        "challenge_trials",
        minimum=0,
        maximum=max(0, maximum - 1),
    )
    if not isinstance(concurrent, bool):
        raise ValueError("concurrent must be an explicit boolean")
    ranked = rank_candidate_predictions(candidates, attempts)
    selected: list[tuple[CandidateRanking, CandidateTrialPurpose]] = [
        (ranked[0], CandidateTrialPurpose.PREFERRED)
    ]
    remaining = list(ranked[1:])
    challenger_order = sorted(
        remaining,
        key=lambda item: (
            item.evaluated_trials,
            item.prediction.predicted_cost_microusd,
            item.prediction.predicted_latency_ms,
            item.prediction.candidate_identity_digest,
        ),
    )
    for challenger in challenger_order[:challenges]:
        selected.append((challenger, CandidateTrialPurpose.CHALLENGE))
        remaining.remove(challenger)
    for candidate in ranked:
        if len(selected) >= min(maximum, len(ranked)):
            break
        if candidate in remaining:
            selected.append((candidate, CandidateTrialPurpose.RANKED))
            remaining.remove(candidate)
    trials = tuple(
        CandidateTrial(
            ordinal=ordinal,
            purpose=purpose,
            prediction=ranking.prediction,
            ranking=ranking,
        )
        for ordinal, (ranking, purpose) in enumerate(selected)
    )
    return CandidateTrialPlan(trials=trials, concurrent=concurrent)


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
    first_key = _stratum_key(evaluated[0].prediction)
    if any(_stratum_key(item.prediction) != first_key for item in evaluated[1:]):
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
    "CandidateAttempt",
    "CandidateAttemptOutcome",
    "CandidatePrediction",
    "CandidateRanking",
    "CandidateTrial",
    "CandidateTrialPlan",
    "CandidateTrialPurpose",
    "load_calibration_attempts",
    "plan_bounded_candidate_trials",
    "prequential_brier_skill",
    "rank_candidate_predictions",
    "record_calibration_attempt",
)
