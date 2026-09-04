"""Conservative rankings and explicit bounded cross-model trial plans."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import NormalDist

from general_ludd.self_improve._candidate_attempt import CandidateAttempt
from general_ludd.self_improve._candidate_prediction import (
    _MAX_TRIALS,
    CandidatePrediction,
    bounded_integer,
    stable_digest,
    stratum_key,
)

_PRIOR_STRENGTH = 2.0
_LOWER_QUANTILE_Z = NormalDist().inv_cdf(0.1)


class CandidateTrialPurpose(StrEnum):
    """Why a candidate appears in a bounded, explicit trial plan."""

    PREFERRED = "preferred"
    CHALLENGE = "challenge"
    RANKED = "ranked"


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
        return stable_digest(
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


def _validated_predictions(
    predictions: Sequence[CandidatePrediction],
) -> tuple[CandidatePrediction, ...]:
    items = tuple(predictions)
    if not all(isinstance(item, CandidatePrediction) for item in items):
        raise ValueError("predictions must contain CandidatePrediction values")
    identities = [item.candidate_identity_digest for item in items]
    if len(identities) != len(set(identities)):
        raise ValueError("predictions must not contain duplicate candidate identities")
    if items and any(stratum_key(item) != stratum_key(items[0]) for item in items[1:]):
        raise ValueError("predictions must share one exact evaluation stratum")
    return items


def _ranking_for(
    prediction: CandidatePrediction,
    observations: tuple[CandidateAttempt, ...],
) -> CandidateRanking:
    matching = tuple(
        attempt
        for attempt in observations
        if attempt.is_evaluated
        and attempt.prediction.candidate_identity_digest
        == prediction.candidate_identity_digest
        and attempt.prediction.provider is prediction.provider
        and stratum_key(attempt.prediction) == stratum_key(prediction)
    )
    accepted = sum(attempt.accepted for attempt in matching)
    rejected = len(matching) - accepted
    alpha = 1.0 + _PRIOR_STRENGTH * prediction.predicted_acceptance + accepted
    beta = 1.0 + _PRIOR_STRENGTH * (1.0 - prediction.predicted_acceptance) + rejected
    posterior = alpha / (alpha + beta)
    variance = alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1.0))
    return CandidateRanking(
        prediction=prediction,
        evaluated_trials=len(matching),
        accepted_trials=accepted,
        posterior_acceptance=posterior,
        conservative_acceptance=max(
            0.0,
            posterior + _LOWER_QUANTILE_Z * math.sqrt(variance),
        ),
    )


def rank_candidate_predictions(
    predictions: Sequence[CandidatePrediction],
    attempts: Sequence[CandidateAttempt],
) -> tuple[CandidateRanking, ...]:
    """Rank one stratum by a beta-posterior lower bound, then resource cost."""
    candidates = _validated_predictions(predictions)
    observations = tuple(attempts)
    if not all(isinstance(item, CandidateAttempt) for item in observations):
        raise ValueError("attempts must contain CandidateAttempt values")
    rankings = [_ranking_for(prediction, observations) for prediction in candidates]
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
    maximum = bounded_integer(
        max_trials,
        "max_trials",
        minimum=1,
        maximum=_MAX_TRIALS,
    )
    challenges = bounded_integer(
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


__all__ = (
    "CandidateRanking",
    "CandidateTrial",
    "CandidateTrialPlan",
    "CandidateTrialPurpose",
    "plan_bounded_candidate_trials",
    "rank_candidate_predictions",
)
