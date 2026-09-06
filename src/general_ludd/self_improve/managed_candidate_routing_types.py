"""Validated public value types for provider-neutral candidate routing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

from general_ludd.self_improve._candidate_execution_types import (
    MAX_CANDIDATE_TRIALS,
    CandidateExecutionResult,
)
from general_ludd.self_improve._candidate_prediction import (
    CandidatePrediction,
    bounded_integer,
    bounded_probability,
    require_digest,
)
from general_ludd.self_improve.model_candidates import BoundedCandidateSession

_ProposalT = TypeVar("_ProposalT")
_MAX_TOKENS = 100_000_000
_MAX_COST_MICROUSD = 1_000_000_000_000
_MAX_BLOCKERS = 10_000


class CandidateProposalDecodeRejected(ValueError):
    """Signal one deterministic, content-free proposal protocol rejection."""

    def __init__(self) -> None:
        """Expose no parser detail or provider response content."""
        super().__init__("candidate proposal did not satisfy its approved protocol")


class ManagedCandidateRouteFailure(StrEnum):
    """Fixed terminal routing categories safe for public progress events."""

    NO_EVALUATED_PROPOSAL = "no_evaluated_proposal"
    TRACE_FAILURE = "trace_failure"


class ManagedCandidateRoutingError(RuntimeError):
    """Censored routing failure without prompt, response, or provider details."""

    def __init__(self, failure: ManagedCandidateRouteFailure) -> None:
        """Retain only one typed public failure category."""
        if not isinstance(failure, ManagedCandidateRouteFailure):
            raise ValueError("failure must be a ManagedCandidateRouteFailure")
        super().__init__(f"managed candidate routing failed: {failure.value}")
        self.failure = failure


class ManagedCandidateRoutingEvent(StrEnum):
    """Content-free orchestration events outside individual trial execution."""

    PLAN_CREATED = "managed_candidate_plan_created"
    CANDIDATE_SELECTED = "managed_candidate_selected"
    NO_EVALUATED_PROPOSAL = "managed_candidate_no_evaluated_proposal"


@dataclass(frozen=True, slots=True)
class ManagedCandidateRoutingTrace:
    """One request- and response-free routing transition."""

    event: ManagedCandidateRoutingEvent
    plan_digest: str
    trial_count: int
    candidate_identity_digest: str | None = None
    provider: str | None = None
    accepted: bool | None = None

    def __post_init__(self) -> None:
        """Validate the bounded public trace shape."""
        if not isinstance(self.event, ManagedCandidateRoutingEvent):
            raise ValueError("event must be a ManagedCandidateRoutingEvent")
        require_digest(self.plan_digest, "plan_digest")
        bounded_integer(
            self.trial_count,
            "trial_count",
            minimum=1,
            maximum=MAX_CANDIDATE_TRIALS,
        )
        if self.candidate_identity_digest is not None:
            require_digest(
                self.candidate_identity_digest,
                "candidate_identity_digest",
            )
        if self.provider is not None and (
            type(self.provider) is not str or not self.provider
        ):
            raise ValueError("provider must be one non-empty categorical label")
        if self.accepted is not None and not isinstance(self.accepted, bool):
            raise ValueError("accepted must be an explicit boolean")


@dataclass(frozen=True, slots=True)
class CandidateObservedUsage:
    """Content-free usage facts derived after one provider response."""

    input_tokens: int
    output_tokens: int
    cost_microusd: int

    def __post_init__(self) -> None:
        """Reject negative, boolean, and effectively unbounded observations."""
        bounded_integer(
            self.input_tokens,
            "input_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        bounded_integer(
            self.output_tokens,
            "output_tokens",
            minimum=0,
            maximum=_MAX_TOKENS,
        )
        bounded_integer(
            self.cost_microusd,
            "cost_microusd",
            minimum=0,
            maximum=_MAX_COST_MICROUSD,
        )


@dataclass(frozen=True, slots=True)
class CandidateProposalAssessment:
    """Deterministic full-task quality facts for one decoded proposal."""

    accepted: bool
    score: float
    blocker_count: int

    def __post_init__(self) -> None:
        """Require a coherent accepted or rejected quality label."""
        if not isinstance(self.accepted, bool):
            raise ValueError("accepted must be an explicit boolean")
        bounded_probability(self.score, "score")
        blockers = bounded_integer(
            self.blocker_count,
            "blocker_count",
            minimum=0 if self.accepted else 1,
            maximum=_MAX_BLOCKERS,
        )
        if self.accepted and blockers != 0:
            raise ValueError("accepted assessments must not contain blockers")


@dataclass(frozen=True, slots=True)
class ManagedCandidateProposalCodec(Generic[_ProposalT]):
    """One bounded remote request and its approval-bound response decoder."""

    request_text: str = field(repr=False)
    decoder: Callable[[str], _ProposalT] = field(repr=False, compare=False)
    protocol_digest: str
    sampling_digest: str

    def __post_init__(self) -> None:
        """Reject empty, oversized, mutable, or unidentifiable protocol state."""
        if (
            type(self.request_text) is not str
            or not self.request_text.strip()
            or "\x00" in self.request_text
            or len(self.request_text.encode("utf-8")) > 1_048_576
        ):
            raise ValueError("request_text must be bounded non-empty UTF-8 text")
        if not callable(self.decoder):
            raise ValueError("decoder must be callable")
        require_digest(self.protocol_digest, "protocol_digest")
        require_digest(self.sampling_digest, "sampling_digest")


@dataclass(frozen=True, slots=True)
class ManagedCandidateTrialSpec(Generic[_ProposalT]):
    """Opaque invocation, decode, evaluation, and resource predictions."""

    session: BoundedCandidateSession[Any, Any] = field(repr=False, compare=False)
    request: object = field(repr=False, compare=False)
    decoder: Callable[[object], _ProposalT] = field(repr=False, compare=False)
    usage_reader: Callable[[object], CandidateObservedUsage] = field(
        repr=False,
        compare=False,
    )
    assessor: Callable[[_ProposalT], CandidateProposalAssessment] = field(
        repr=False,
        compare=False,
    )
    predicted_latency_ms: int
    predicted_input_tokens: int
    predicted_output_tokens: int
    predicted_cost_microusd: int
    prior_acceptance: float = 0.5

    def __post_init__(self) -> None:
        """Validate all adapters and predictions before planning any effect."""
        if not isinstance(self.session, BoundedCandidateSession):
            raise ValueError("session must be a BoundedCandidateSession")
        if not all(
            callable(adapter)
            for adapter in (self.decoder, self.usage_reader, self.assessor)
        ):
            raise ValueError("decoder, usage_reader, and assessor must be callable")
        bounded_integer(
            self.predicted_latency_ms,
            "predicted_latency_ms",
            minimum=1,
            maximum=86_400_000,
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
            minimum=1,
            maximum=_MAX_TOKENS,
        )
        bounded_integer(
            self.predicted_cost_microusd,
            "predicted_cost_microusd",
            minimum=0,
            maximum=_MAX_COST_MICROUSD,
        )
        bounded_probability(self.prior_acceptance, "prior_acceptance")


@dataclass(frozen=True, slots=True)
class ManagedCandidateRoutingResult(Generic[_ProposalT]):
    """Selected decoded proposal plus complete content-free trial evidence."""

    selected: _ProposalT = field(repr=False, compare=False)
    selected_prediction: CandidatePrediction
    selected_assessment: CandidateProposalAssessment
    execution: CandidateExecutionResult


__all__ = (
    "CandidateObservedUsage",
    "CandidateProposalAssessment",
    "CandidateProposalDecodeRejected",
    "ManagedCandidateProposalCodec",
    "ManagedCandidateRouteFailure",
    "ManagedCandidateRoutingError",
    "ManagedCandidateRoutingEvent",
    "ManagedCandidateRoutingResult",
    "ManagedCandidateRoutingTrace",
    "ManagedCandidateTrialSpec",
)
