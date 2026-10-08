"""Durable, fixed-cardinality evidence for deterministic decision reuse."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)

from general_ludd.decision_codification.rollout import GenerationPointer
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionKind,
    DecisionRuleBundleV1,
    FallbackReason,
    HmacSha256Digest,
    RolloutStage,
    canonical_sha256,
)
from general_ludd.decision_codification.telemetry import DecisionCodificationTelemetry
from general_ludd.schemas.execution_identity import BoundedIdentifier, Sha256Digest

MAX_OBSERVABILITY_RECEIPT_BYTES: Final[int] = 64 * 1024
MAX_OBSERVATION_LATENCY_US: Final[int] = 300_000_000
MAX_OBSERVABILITY_COUNTER: Final[int] = 2**63 - 1
_MAX_TOTAL_OBSERVATIONS: Final[int] = MAX_OBSERVABILITY_COUNTER * len(DecisionKind)
_STATUS_SCHEMA: Final[str] = "gludd.decision-reuse-status/v1"
_PROJECT = TypeAdapter(BoundedIdentifier, config=ConfigDict(strict=True))
_DIGEST = TypeAdapter(Sha256Digest, config=ConfigDict(strict=True))
_DRIFT_REASONS: Final[frozenset[FallbackReason]] = frozenset(
    {
        FallbackReason.DRIFT_HOLD,
        FallbackReason.INTEGRITY_FAILURE,
        FallbackReason.MULTIPLE_LEAVES,
        FallbackReason.POLICY_CHANGED,
    }
)


class DecisionReuseObservabilityError(RuntimeError):
    """Raised when durable observability evidence cannot be trusted."""


class DecisionResolutionPath(StrEnum):
    """Closed resolution paths retained by durable aggregation."""

    EXACT_RULE = "exact_rule"
    AGENT_FALLBACK = "agent_fallback"


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class DecisionReuseObservation(_StrictModel):
    """One content-free resolution observation, never persisted verbatim."""

    project_id: BoundedIdentifier
    policy_digest: Sha256Digest
    decision_kind: DecisionKind
    path: DecisionResolutionPath
    abstention_reason: FallbackReason | None
    candidate_digest: Sha256Digest | None
    rollout_stage: RolloutStage | None
    latency_us: int = Field(ge=0, le=MAX_OBSERVATION_LATENCY_US)
    observed_at: datetime

    @model_validator(mode="after")
    def _validate_path(self) -> DecisionReuseObservation:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observation time must be timezone-aware")
        if self.path is DecisionResolutionPath.EXACT_RULE:
            if (
                self.abstention_reason is not None
                or self.candidate_digest is None
                or self.rollout_stage is None
            ):
                raise ValueError("exact-rule observation is incomplete")
        elif self.abstention_reason is None or self.rollout_stage is not None:
            raise ValueError("fallback observation is incomplete")
        return self


class DecisionAbstentionCount(_StrictModel):
    """One closed abstention-reason counter."""

    reason: FallbackReason
    count: int = Field(ge=1, le=MAX_OBSERVABILITY_COUNTER)


class DecisionReuseAggregate(_StrictModel):
    """Validated fixed-row aggregate loaded from durable state."""

    decision_kind: DecisionKind
    sequence: int = Field(ge=0, le=MAX_OBSERVABILITY_COUNTER)
    exact_rule_hits: int = Field(ge=0, le=MAX_OBSERVABILITY_COUNTER)
    typed_abstentions: int = Field(ge=0, le=MAX_OBSERVABILITY_COUNTER)
    fallback_calls: int = Field(ge=0, le=MAX_OBSERVABILITY_COUNTER)
    avoided_agent_calls: int = Field(ge=0, le=MAX_OBSERVABILITY_COUNTER)
    latency_observations: int = Field(ge=0, le=MAX_OBSERVABILITY_COUNTER)
    latency_total_us: int = Field(ge=0, le=MAX_OBSERVABILITY_COUNTER)
    latency_max_us: int = Field(ge=0, le=MAX_OBSERVATION_LATENCY_US)
    rule_version_changes: int = Field(ge=0, le=MAX_OBSERVABILITY_COUNTER)
    drift_events: int = Field(ge=0, le=MAX_OBSERVABILITY_COUNTER)
    abstentions: tuple[DecisionAbstentionCount, ...] = Field(
        max_length=len(FallbackReason)
    )
    last_candidate_digest: Sha256Digest | None
    last_observed_at: datetime | None

    @model_validator(mode="after")
    def _validate_aggregate(self) -> DecisionReuseAggregate:
        reasons = tuple(item.reason for item in self.abstentions)
        if reasons != tuple(sorted(set(reasons), key=lambda reason: reason.value)):
            raise ValueError("abstention counters must be sorted and unique")
        if self.last_observed_at is not None and (
            self.last_observed_at.tzinfo is None
            or self.last_observed_at.utcoffset() is None
        ):
            raise ValueError("last observation time must be timezone-aware")
        if (
            self.exact_rule_hits != self.avoided_agent_calls
            or self.typed_abstentions != self.fallback_calls
            or self.sequence != self.exact_rule_hits + self.fallback_calls
            or self.latency_observations != self.sequence
            or sum(item.count for item in self.abstentions)
            != self.typed_abstentions
            or (self.latency_observations == 0 and self.latency_max_us != 0)
            or self.latency_max_us > self.latency_total_us
        ):
            raise ValueError("decision-reuse aggregate invariants failed")
        return self


class DecisionReuseKindSummary(DecisionReuseAggregate):
    """One fixed decision-kind status projection plus current rule version."""

    current_candidate_digest: Sha256Digest | None
    current_receipt_digest: Sha256Digest | None
    current_stage: RolloutStage | None
    current_epoch: int | None = Field(default=None, ge=1)
    current_drift_held: bool = False

    @model_validator(mode="after")
    def _validate_current_version(self) -> DecisionReuseKindSummary:
        fields = (
            self.current_candidate_digest,
            self.current_receipt_digest,
            self.current_stage,
            self.current_epoch,
        )
        if any(item is None for item in fields) != all(item is None for item in fields):
            raise ValueError("current rule version must be complete or absent")
        if self.current_candidate_digest is None and self.current_drift_held:
            raise ValueError("an absent rule cannot be drift-held")
        return self


class DecisionReuseStatusReceipt(_StrictModel):
    """Tamper-evident bounded snapshot for operators and release evidence."""

    schema_version: Literal["gludd.decision-reuse-status/v1"] = Field(
        default="gludd.decision-reuse-status/v1",
        alias="schema",
        serialization_alias="schema",
    )
    project_id: BoundedIdentifier
    policy_digest: Sha256Digest
    total_observations: int = Field(ge=0, le=_MAX_TOTAL_OBSERVATIONS)
    summaries: tuple[DecisionReuseKindSummary, ...] = Field(
        min_length=len(DecisionKind),
        max_length=len(DecisionKind),
    )
    receipt_digest: Sha256Digest
    authentication_tag: HmacSha256Digest

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        policy_digest: str,
        summaries: tuple[DecisionReuseKindSummary, ...],
        authentication_tag: str,
    ) -> DecisionReuseStatusReceipt:
        """Create a digest-bound status receipt from validated summaries."""
        unsigned = cls.unsigned_payload(
            project_id=project_id,
            policy_digest=policy_digest,
            summaries=summaries,
        )
        return cls(
            project_id=project_id,
            policy_digest=policy_digest,
            total_observations=sum(item.sequence for item in summaries),
            summaries=summaries,
            receipt_digest=canonical_sha256(unsigned),
            authentication_tag=authentication_tag,
        )

    @staticmethod
    def unsigned_payload(
        *,
        project_id: str,
        policy_digest: str,
        summaries: tuple[DecisionReuseKindSummary, ...],
    ) -> dict[str, object]:
        """Return the canonical bounded payload covered by digest and HMAC."""
        return {
            "schema": _STATUS_SCHEMA,
            "project_id": project_id,
            "policy_digest": policy_digest,
            "total_observations": sum(item.sequence for item in summaries),
            "summaries": [
                item.model_dump(mode="json", by_alias=True) for item in summaries
            ],
        }

    @model_validator(mode="after")
    def _verify_receipt(self) -> DecisionReuseStatusReceipt:
        kinds = tuple(summary.decision_kind for summary in self.summaries)
        if kinds != tuple(sorted(DecisionKind, key=lambda kind: kind.value)):
            raise ValueError("status summaries must contain every sorted decision kind")
        if self.total_observations != sum(item.sequence for item in self.summaries):
            raise ValueError("status total does not match its summaries")
        unsigned = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"receipt_digest", "authentication_tag"},
        )
        if self.receipt_digest != canonical_sha256(unsigned):
            raise ValueError("status receipt digest does not match")
        return self


class DecisionObservationStore(Protocol):
    """Fixed-cardinality durable aggregation required by the observer."""

    def bind_decision_observability(
        self,
        project_id: str,
        policy_digest: str,
    ) -> None:
        """Bind durable aggregation to one exact project and policy scope."""
        ...

    def record_decision_observation(
        self,
        observation: DecisionReuseObservation,
    ) -> None:
        """Atomically add one validated content-free observation."""
        ...

    def decision_observability_aggregates(
        self,
        project_id: str,
        policy_digest: str,
    ) -> tuple[DecisionReuseAggregate, ...]:
        """Load the bounded aggregate snapshot for one exact scope."""
        ...


class DecisionReceiptAuthenticator(Protocol):
    """Existing keyed artifact boundary used to authenticate status receipts."""

    def decision_observability_hmac(
        self,
        project_id: str,
        policy_digest: str,
        receipt_digest: str,
    ) -> str:
        """Authenticate a status digest within its exact scope."""
        ...

    def verify_decision_observability_hmac(
        self,
        project_id: str,
        policy_digest: str,
        receipt_digest: str,
        authentication_tag: str,
    ) -> None:
        """Verify one scope-bound status authentication tag."""
        ...


class DecisionVersionSource(Protocol):
    """Verified current-rule view used only while creating a status receipt."""

    def current(
        self,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> GenerationPointer | None:
        """Return the current pointer for one exact decision scope."""
        ...

    def verified_generation(
        self,
        pointer: GenerationPointer,
    ) -> tuple[DecisionRuleBundleV1, ApprovalReceiptV1]:
        """Load and verify the generation referenced by a pointer."""
        ...

    def is_drift_held(self, candidate_digest: str) -> bool:
        """Return whether one candidate is currently held for drift."""
        ...


class DecisionReuseObservability:
    """Aggregate resolution evidence without retaining request-level content."""

    def __init__(
        self,
        store: DecisionObservationStore,
        versions: DecisionVersionSource,
        authenticator: DecisionReceiptAuthenticator,
        *,
        project_id: str,
        policy_digest: str,
        telemetry: DecisionCodificationTelemetry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Bind one exact scope to the shared durable repository."""
        try:
            self._project_id = _PROJECT.validate_python(project_id, strict=True)
            self._policy_digest = _DIGEST.validate_python(policy_digest, strict=True)
            store.bind_decision_observability(
                self._project_id,
                self._policy_digest,
            )
        except Exception:
            raise DecisionReuseObservabilityError(
                "decision reuse observability scope is unavailable"
            ) from None
        self._store = store
        self._versions = versions
        self._authenticator = authenticator
        self._telemetry = telemetry or DecisionCodificationTelemetry()
        self._clock = clock or (lambda: datetime.now(UTC))

    def record_resolution(
        self,
        *,
        project_id: str,
        policy_digest: str,
        decision_kind: DecisionKind,
        path: DecisionResolutionPath,
        abstention_reason: FallbackReason | None,
        candidate_digest: str | None,
        rollout_stage: RolloutStage | None,
        latency_ns: int,
    ) -> bool:
        """Atomically aggregate one closed observation; never raise to callers."""
        try:
            self._require_scope(project_id, policy_digest)
            if type(latency_ns) is not int or not 0 <= latency_ns <= (
                MAX_OBSERVATION_LATENCY_US * 1_000
            ):
                return False
            observation = DecisionReuseObservation(
                project_id=self._project_id,
                policy_digest=self._policy_digest,
                decision_kind=decision_kind,
                path=path,
                abstention_reason=abstention_reason,
                candidate_digest=candidate_digest,
                rollout_stage=rollout_stage,
                latency_us=latency_ns // 1_000,
                observed_at=self._now(),
            )
            self._store.record_decision_observation(observation)
            self._telemetry.resolution_seconds(
                observation.path.value,
                observation.latency_us / 1_000_000,
            )
            if observation.path is DecisionResolutionPath.EXACT_RULE:
                self._telemetry.estimated_calls_avoided(
                    observation.decision_kind.value
                )
            return True
        except Exception:
            return False

    def status_receipt(
        self,
        *,
        project_id: str,
        policy_digest: str,
    ) -> DecisionReuseStatusReceipt:
        """Return one bounded digest-bound status snapshot or fail closed."""
        try:
            self._require_scope(project_id, policy_digest)
            loaded = self._store.decision_observability_aggregates(
                self._project_id,
                self._policy_digest,
            )
            aggregates = {item.decision_kind: item for item in loaded}
            if len(aggregates) != len(loaded):
                raise DecisionReuseObservabilityError
            summaries = tuple(
                self._summary(
                    aggregates.get(kind, self._empty_aggregate(kind)),
                )
                for kind in sorted(DecisionKind, key=lambda item: item.value)
            )
            unsigned = DecisionReuseStatusReceipt.unsigned_payload(
                project_id=self._project_id,
                policy_digest=self._policy_digest,
                summaries=summaries,
            )
            receipt_digest = canonical_sha256(unsigned)
            authentication_tag = self._authenticator.decision_observability_hmac(
                self._project_id,
                self._policy_digest,
                receipt_digest,
            )
            receipt = DecisionReuseStatusReceipt.create(
                project_id=self._project_id,
                policy_digest=self._policy_digest,
                summaries=summaries,
                authentication_tag=authentication_tag,
            )
            self._authenticator.verify_decision_observability_hmac(
                self._project_id,
                self._policy_digest,
                receipt.receipt_digest,
                receipt.authentication_tag,
            )
            if (
                len(receipt.model_dump_json().encode("utf-8"))
                > MAX_OBSERVABILITY_RECEIPT_BYTES
            ):
                raise DecisionReuseObservabilityError
            return receipt
        except DecisionReuseObservabilityError:
            raise
        except Exception:
            raise DecisionReuseObservabilityError(
                "decision reuse observability status is unavailable"
            ) from None

    def _summary(self, aggregate: DecisionReuseAggregate) -> DecisionReuseKindSummary:
        pointer = self._versions.current(self._project_id, aggregate.decision_kind)
        if pointer is None:
            current: dict[str, object] = {
                "current_candidate_digest": None,
                "current_receipt_digest": None,
                "current_stage": None,
                "current_epoch": None,
                "current_drift_held": False,
            }
        else:
            bundle, receipt = self._versions.verified_generation(pointer)
            if (
                pointer.project_id != self._project_id
                or bundle.project_id != self._project_id
                or pointer.decision_kind is not aggregate.decision_kind
                or bundle.decision_kind is not aggregate.decision_kind
                or pointer.candidate_digest != bundle.candidate_digest
                or pointer.receipt_digest != receipt.receipt_digest
                or receipt.policy_digest != self._policy_digest
                or self._policy_digest not in bundle.policy_compatibility
            ):
                raise DecisionReuseObservabilityError
            current = {
                "current_candidate_digest": pointer.candidate_digest,
                "current_receipt_digest": pointer.receipt_digest,
                "current_stage": pointer.stage,
                "current_epoch": pointer.epoch,
                "current_drift_held": self._versions.is_drift_held(
                    pointer.candidate_digest
                ),
            }
        return DecisionReuseKindSummary.model_validate(
            {**aggregate.model_dump(mode="python"), **current}
        )

    def _require_scope(self, project_id: object, policy_digest: object) -> None:
        if (
            type(project_id) is not str
            or type(policy_digest) is not str
            or not hmac.compare_digest(project_id, self._project_id)
            or not hmac.compare_digest(policy_digest, self._policy_digest)
        ):
            raise DecisionReuseObservabilityError(
                "decision reuse observability scope does not match"
            )

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise DecisionReuseObservabilityError(
                "decision reuse observability clock is invalid"
            )
        return now.astimezone(UTC)

    @staticmethod
    def _empty_aggregate(kind: DecisionKind) -> DecisionReuseAggregate:
        return DecisionReuseAggregate(
            decision_kind=kind,
            sequence=0,
            exact_rule_hits=0,
            typed_abstentions=0,
            fallback_calls=0,
            avoided_agent_calls=0,
            latency_observations=0,
            latency_total_us=0,
            latency_max_us=0,
            rule_version_changes=0,
            drift_events=0,
            abstentions=(),
            last_candidate_digest=None,
            last_observed_at=None,
        )


def is_drift_reason(reason: FallbackReason | None) -> bool:
    """Return whether a closed abstention reason represents rule/version drift."""
    return reason in _DRIFT_REASONS


__all__ = [
    "MAX_OBSERVABILITY_COUNTER",
    "MAX_OBSERVABILITY_RECEIPT_BYTES",
    "MAX_OBSERVATION_LATENCY_US",
    "DecisionAbstentionCount",
    "DecisionObservationStore",
    "DecisionReceiptAuthenticator",
    "DecisionResolutionPath",
    "DecisionReuseAggregate",
    "DecisionReuseKindSummary",
    "DecisionReuseObservability",
    "DecisionReuseObservabilityError",
    "DecisionReuseObservation",
    "DecisionReuseStatusReceipt",
    "DecisionVersionSource",
    "is_drift_reason",
]
