"""Validated public value types for provider-neutral candidate routing."""

from __future__ import annotations

import hashlib
import json
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
    stable_digest,
)
from general_ludd.self_improve.model_candidates import BoundedCandidateSession

_ProposalT = TypeVar("_ProposalT")
_MAX_TOKENS = 100_000_000
_MAX_COST_MICROUSD = 1_000_000_000_000
_MAX_BLOCKERS = 10_000
_PROPOSAL_ENVELOPE_PROTOCOL = "gludd-managed-candidate-proposal-envelope-v1"
# Includes the worst-case JSON escaping of every independently bounded artifact.
_MAX_PROPOSAL_ENVELOPE_JSON_BYTES = 8 * 1_048_576


class CandidateProposalDecodeFailure(StrEnum):
    """Fixed, response-free reasons an output missed its proposal protocol."""

    JSON_CONTRACT = "proposal_json_contract"
    ROOT_CONTRACT = "proposal_root_contract"
    PROTOCOL_IDENTITY = "proposal_protocol_identity"
    PROPOSAL_COUNT = "proposal_count"
    PROPOSAL_SHAPE = "proposal_shape"
    PROPOSAL_SCOPE = "proposal_scope"
    EDIT_LINE_BUDGET = "edit_line_budget"
    EDIT_CONTENT_BUDGET = "edit_content_budget"
    EDIT_NO_CHANGE = "edit_no_change"
    EDIT_OVERLAP = "edit_overlap"
    PROPOSAL_VALIDATION = "proposal_validation"


class CandidateProposalDecodeRejected(ValueError):
    """Signal one deterministic, content-free proposal protocol rejection."""

    def __init__(
        self,
        failure: CandidateProposalDecodeFailure = (
            CandidateProposalDecodeFailure.PROPOSAL_VALIDATION
        ),
    ) -> None:
        """Expose no parser detail or provider response content."""
        if not isinstance(failure, CandidateProposalDecodeFailure):
            raise ValueError("failure must be a CandidateProposalDecodeFailure")
        super().__init__(
            "candidate proposal did not satisfy its approved protocol: "
            f"{failure.value}"
        )
        self.failure = failure


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
    CANDIDATE_PROTOCOL_REJECTED = "managed_candidate_protocol_rejected"
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
    protocol_failure: CandidateProposalDecodeFailure | None = None

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
        if self.protocol_failure is not None and not isinstance(
            self.protocol_failure,
            CandidateProposalDecodeFailure,
        ):
            raise ValueError(
                "protocol_failure must be a CandidateProposalDecodeFailure"
            )
        if self.event is ManagedCandidateRoutingEvent.CANDIDATE_PROTOCOL_REJECTED:
            if self.protocol_failure is None or self.accepted is not False:
                raise ValueError(
                    "protocol rejection requires a protocol_failure and accepted=false"
                )
        elif self.protocol_failure is not None:
            raise ValueError("protocol_failure is valid only for protocol rejection")


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
class ManagedCandidateProposalEnvelope:
    """Serializable model-visible and trusted artifacts shared by every worker."""

    request_text: str = field(repr=False)
    request_contract_json: str = field(repr=False)
    response_instruction: str = field(repr=False)
    response_schema_json: str = field(repr=False)
    protocol_digest: str
    sampling_digest: str

    def __post_init__(self) -> None:
        """Reject any artifact that cannot be transported byte-for-byte."""
        if (
            type(self.request_text) is not str
            or not self.request_text.strip()
            or "\x00" in self.request_text
            or len(self.request_text.encode("utf-8")) > 1_048_576
        ):
            raise ValueError("request_text must be bounded non-empty UTF-8 text")
        require_digest(self.protocol_digest, "protocol_digest")
        require_digest(self.sampling_digest, "sampling_digest")
        if (
            type(self.request_contract_json) is not str
            or not self.request_contract_json
            or "\x00" in self.request_contract_json
            or len(self.request_contract_json.encode("utf-8")) > 196_608
        ):
            raise ValueError("request contract must be bounded canonical JSON")
        try:
            contract = json.loads(self.request_contract_json)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("request contract must be bounded canonical JSON") from None
        canonical_contract = json.dumps(
            contract,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if not isinstance(contract, dict) or canonical_contract != self.request_contract_json:
            raise ValueError("request contract must be bounded canonical JSON")
        if (
            type(self.response_instruction) is not str
            or not self.response_instruction.strip()
            or "\x00" in self.response_instruction
            or len(self.response_instruction.encode("utf-8")) > 16_384
            or type(self.response_schema_json) is not str
            or len(self.response_schema_json.encode("utf-8")) > 262_144
        ):
            raise ValueError("response protocol metadata must be bounded UTF-8 text")
        try:
            schema = json.loads(self.response_schema_json)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("response schema must be canonical JSON") from None
        canonical_schema = json.dumps(
            schema,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if not isinstance(schema, dict) or canonical_schema != self.response_schema_json:
            raise ValueError("response schema must be one canonical JSON object")

    @property
    def envelope_digest(self) -> str:
        """Identify all exact artifacts without exposing their content."""
        return stable_digest(
            {
                "protocol": _PROPOSAL_ENVELOPE_PROTOCOL,
                "protocol_digest": self.protocol_digest,
                "request_contract_sha256": hashlib.sha256(
                    self.request_contract_json.encode("utf-8")
                ).hexdigest(),
                "request_sha256": hashlib.sha256(
                    self.request_text.encode("utf-8")
                ).hexdigest(),
                "response_instruction_sha256": hashlib.sha256(
                    self.response_instruction.encode("utf-8")
                ).hexdigest(),
                "response_schema_sha256": hashlib.sha256(
                    self.response_schema_json.encode("utf-8")
                ).hexdigest(),
                "sampling_digest": self.sampling_digest,
            }
        )

    def to_json(self) -> str:
        """Serialize one self-identifying canonical worker envelope."""
        return json.dumps(
            {
                "envelope_digest": self.envelope_digest,
                "protocol": _PROPOSAL_ENVELOPE_PROTOCOL,
                "protocol_digest": self.protocol_digest,
                "request_contract_json": self.request_contract_json,
                "request_text": self.request_text,
                "response_instruction": self.response_instruction,
                "response_schema_json": self.response_schema_json,
                "sampling_digest": self.sampling_digest,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> ManagedCandidateProposalEnvelope:
        """Decode and verify an exact canonical envelope at a worker boundary."""
        if (
            type(raw) is not str
            or not raw
            or len(raw.encode("utf-8")) > _MAX_PROPOSAL_ENVELOPE_JSON_BYTES
        ):
            raise ValueError("proposal envelope must be bounded canonical JSON")
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("proposal envelope must be bounded canonical JSON") from None
        expected_fields = {
            "envelope_digest",
            "protocol",
            "protocol_digest",
            "request_contract_json",
            "request_text",
            "response_instruction",
            "response_schema_json",
            "sampling_digest",
        }
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if not isinstance(value, dict) or set(value) != expected_fields or canonical != raw:
            raise ValueError("proposal envelope must be bounded canonical JSON")
        if value["protocol"] != _PROPOSAL_ENVELOPE_PROTOCOL:
            raise ValueError("proposal envelope protocol is unsupported")
        try:
            envelope = cls(
                request_text=value["request_text"],
                request_contract_json=value["request_contract_json"],
                response_instruction=value["response_instruction"],
                response_schema_json=value["response_schema_json"],
                protocol_digest=value["protocol_digest"],
                sampling_digest=value["sampling_digest"],
            )
        except (TypeError, ValueError):
            raise ValueError("proposal envelope artifacts are invalid") from None
        if value["envelope_digest"] != envelope.envelope_digest:
            raise ValueError("proposal envelope digest mismatch")
        return envelope


@dataclass(frozen=True, slots=True)
class ManagedCandidateProposalCodec(Generic[_ProposalT]):
    """One provider-neutral request/contract/response envelope and decoder."""

    request_text: str = field(repr=False)
    decoder: Callable[[str], _ProposalT] = field(repr=False, compare=False)
    protocol_digest: str
    sampling_digest: str
    request_contract_json: str | None = field(default=None, repr=False)
    response_instruction: str | None = field(default=None, repr=False)
    response_schema_json: str | None = field(default=None, repr=False)

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
        if self.request_contract_json is not None:
            if (
                type(self.request_contract_json) is not str
                or not self.request_contract_json
                or "\x00" in self.request_contract_json
                or len(self.request_contract_json.encode("utf-8")) > 196_608
            ):
                raise ValueError("request contract must be bounded canonical JSON")
            try:
                contract = json.loads(self.request_contract_json)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError("request contract must be bounded canonical JSON") from None
            canonical_contract = json.dumps(
                contract,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            if not isinstance(contract, dict) or canonical_contract != self.request_contract_json:
                raise ValueError("request contract must be bounded canonical JSON")
        if (self.response_instruction is None) != (self.response_schema_json is None):
            raise ValueError(
                "response instruction and schema must be provided together"
            )
        if self.response_instruction is None:
            return
        if (
            type(self.response_instruction) is not str
            or not self.response_instruction.strip()
            or "\x00" in self.response_instruction
            or len(self.response_instruction.encode("utf-8")) > 16_384
            or type(self.response_schema_json) is not str
            or len(self.response_schema_json.encode("utf-8")) > 262_144
        ):
            raise ValueError("response protocol metadata must be bounded UTF-8 text")
        try:
            schema = json.loads(self.response_schema_json)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("response schema must be canonical JSON") from None
        canonical = json.dumps(
            schema,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if not isinstance(schema, dict) or canonical != self.response_schema_json:
            raise ValueError("response schema must be one canonical JSON object")

    @property
    def envelope_digest(self) -> str:
        """Identify all model-visible and trusted transport artifacts without content."""
        if (
            self.request_contract_json is not None
            and self.response_instruction is not None
            and self.response_schema_json is not None
        ):
            return self.worker_envelope.envelope_digest
        return stable_digest(
            {
                "protocol": _PROPOSAL_ENVELOPE_PROTOCOL,
                "protocol_digest": self.protocol_digest,
                "request_contract_sha256": (
                    hashlib.sha256(self.request_contract_json.encode("utf-8")).hexdigest()
                    if self.request_contract_json is not None
                    else None
                ),
                "request_sha256": hashlib.sha256(
                    self.request_text.encode("utf-8")
                ).hexdigest(),
                "response_instruction_sha256": (
                    hashlib.sha256(self.response_instruction.encode("utf-8")).hexdigest()
                    if self.response_instruction is not None
                    else None
                ),
                "response_schema_sha256": (
                    hashlib.sha256(self.response_schema_json.encode("utf-8")).hexdigest()
                    if self.response_schema_json is not None
                    else None
                ),
                "sampling_digest": self.sampling_digest,
            }
        )

    @property
    def worker_envelope(self) -> ManagedCandidateProposalEnvelope:
        """Return the complete serialized artifact set required by an owned worker."""
        if (
            self.request_contract_json is None
            or self.response_instruction is None
            or self.response_schema_json is None
        ):
            raise ValueError("managed proposal codec has no complete worker envelope")
        return ManagedCandidateProposalEnvelope(
            request_text=self.request_text,
            request_contract_json=self.request_contract_json,
            response_instruction=self.response_instruction,
            response_schema_json=self.response_schema_json,
            protocol_digest=self.protocol_digest,
            sampling_digest=self.sampling_digest,
        )


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
    "CandidateProposalDecodeFailure",
    "CandidateProposalDecodeRejected",
    "ManagedCandidateProposalCodec",
    "ManagedCandidateProposalEnvelope",
    "ManagedCandidateRouteFailure",
    "ManagedCandidateRoutingError",
    "ManagedCandidateRoutingEvent",
    "ManagedCandidateRoutingResult",
    "ManagedCandidateRoutingTrace",
    "ManagedCandidateTrialSpec",
)
