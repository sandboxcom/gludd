"""Validated, content-free predictions for immutable model candidates."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from general_ludd.schemas.benchmark import TaskType
from general_ludd.self_improve.model_candidates import ModelCandidateProvider

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_TASK_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_TRIALS = 16
_MAX_TOKENS = 100_000_000
_MAX_COST_MICROUSD = 1_000_000_000_000
_MAX_LATENCY_MS = 86_400_000


def stable_digest(payload: Mapping[str, object]) -> str:
    """Hash one JSON-compatible payload in a canonical representation."""
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_digest(value: object, field_name: str) -> str:
    """Return a canonical SHA-256 digest or fail closed."""
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def bounded_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Return a non-boolean integer inside an explicit closed interval."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ValueError(f"{field_name} must be an integer in {minimum}..{maximum}")
    return value


def bounded_probability(value: object, field_name: str) -> float:
    """Return one finite probability without accepting booleans or NaN."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"{field_name} must be a finite probability")
    return float(value)


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
        require_digest(self.candidate_identity_digest, "candidate_identity_digest")
        if not isinstance(self.provider, ModelCandidateProvider):
            raise ValueError("provider must be a ModelCandidateProvider")
        if not isinstance(self.task_type, TaskType):
            raise ValueError("task_type must be a TaskType")
        if not isinstance(self.task_kind, str) or _TASK_KIND_RE.fullmatch(self.task_kind) is None:
            raise ValueError("task_kind must be one bounded categorical label")
        require_digest(self.evaluation_stratum_digest, "evaluation_stratum_digest")
        require_digest(self.prompt_protocol_digest, "prompt_protocol_digest")
        require_digest(self.evaluator_digest, "evaluator_digest")
        require_digest(self.sampling_digest, "sampling_digest")
        require_digest(self.privacy_policy_digest, "privacy_policy_digest")
        object.__setattr__(
            self,
            "predicted_acceptance",
            bounded_probability(self.predicted_acceptance, "predicted_acceptance"),
        )
        bounded_integer(
            self.predicted_latency_ms,
            "predicted_latency_ms",
            minimum=1,
            maximum=_MAX_LATENCY_MS,
        )
        bounded_integer(
            self.predicted_input_tokens,
            "predicted_input_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        bounded_integer(
            self.predicted_output_tokens,
            "predicted_output_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        bounded_integer(
            self.predicted_cost_microusd,
            "predicted_cost_microusd",
            minimum=0,
            maximum=_MAX_COST_MICROUSD,
        )

    def payload(self) -> dict[str, object]:
        """Return the complete content-free prediction payload."""
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
        return stable_digest(
            {"protocol": "gludd-candidate-prediction-v1", **self.payload()}
        )


def stratum_key(prediction: CandidatePrediction) -> tuple[object, ...]:
    """Return every field that makes evaluation evidence comparable."""
    return (
        prediction.task_type,
        prediction.task_kind,
        prediction.evaluation_stratum_digest,
        prediction.prompt_protocol_digest,
        prediction.evaluator_digest,
        prediction.sampling_digest,
        prediction.privacy_policy_digest,
    )


__all__ = (
    "CandidatePrediction",
    "bounded_integer",
    "bounded_probability",
    "require_digest",
    "stable_digest",
    "stratum_key",
)
