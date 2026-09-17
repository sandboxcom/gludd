"""Censored observed outcomes for identity-bound model predictions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from general_ludd.self_improve._candidate_prediction import (
    CandidatePrediction,
    bounded_integer,
    bounded_probability,
    stable_digest,
)
from general_ludd.self_improve.model_candidates import BackendFailure

_MAX_TOKENS = 100_000_000
_MAX_COST_MICROUSD = 1_000_000_000_000
_MAX_LATENCY_MS = 86_400_000
_MAX_BLOCKERS = 10_000


class CandidateAttemptOutcome(StrEnum):
    """Quality and infrastructure dispositions for one explicit candidate call."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


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
        bounded_integer(
            self.observed_latency_ms,
            "observed_latency_ms",
            minimum=0,
            maximum=_MAX_LATENCY_MS,
        )
        bounded_integer(
            self.observed_input_tokens,
            "observed_input_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        bounded_integer(
            self.observed_output_tokens,
            "observed_output_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        bounded_integer(
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
        score = bounded_probability(self.evaluation_score, "evaluation_score")
        object.__setattr__(self, "evaluation_score", score)
        minimum_blockers = 0 if self.outcome is CandidateAttemptOutcome.ACCEPTED else 1
        blockers = bounded_integer(
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

    def payload(self) -> dict[str, object]:
        """Return content-free observed facts suitable for canonical hashing."""
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
        return stable_digest(
            {"protocol": "gludd-candidate-attempt-v1", **self.payload()}
        )


__all__ = ("CandidateAttempt", "CandidateAttemptOutcome")
