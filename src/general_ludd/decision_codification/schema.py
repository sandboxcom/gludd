"""Strict, content-safe contracts for deterministic decision codification.

The models in this module are deliberately data-only.  They bind every durable
artifact to canonical JSON and reject unknown fields, non-finite numbers,
mutable model assignment, unbounded collections, and free-form evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, ClassVar, Literal, Self, TypeAlias, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from general_ludd.replay.schema import (
    BoundedIdentifier,
    SafeRunId,
    Sha256Digest,
    canonical_replay_json,
    decode_replay_json_object,
)

DECISION_ENVELOPE_SCHEMA_V1 = "gludd.decision-envelope/v1"
DECISION_CONTEXT_SCHEMA_V1 = "gludd.decision-context/v1"
DECISION_RULE_BUNDLE_SCHEMA_V1 = "gludd.decision-rule-bundle/v1"
DECISION_EVALUATION_REPORT_SCHEMA_V1 = "gludd.decision-evaluation-report/v1"
DECISION_APPROVAL_RECEIPT_SCHEMA_V1 = "gludd.decision-approval-receipt/v1"
DECISION_ABSTENTION_SCHEMA_V1 = "gludd.decision-abstention/v1"
NORMALIZATION_REFUSAL_SCHEMA_V1 = "gludd.decision-normalization-refusal/v1"

MAX_ENVELOPE_BYTES = 16 * 1024
MAX_RULE_NODES = 31
MAX_RULE_LEAVES = 16
MAX_RULE_DEPTH = 4
MAX_RULE_CONTEXTS = 128
_ZERO_SHA256 = "sha256:" + ("0" * 64)

HmacSha256Digest = Annotated[str, Field(pattern=r"^hmac-sha256:[0-9a-f]{64}$")]
FeatureValue: TypeAlias = bool | Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._:-]*$"),
]
RedactionKind: TypeAlias = Literal[
    "credential_text",
    "credential_url",
    "hidden_reasoning",
    "secret_key",
    "unsupported_key",
    "unsupported_type",
]
Rate = Annotated[float, Field(ge=0.0, le=1.0)]
NonNegativeCount = Annotated[int, Field(ge=0)]
PositiveCount = Annotated[int, Field(ge=1)]


class DecisionKind(StrEnum):
    """Closed decision families eligible for deterministic codification."""

    REVIEW = "review"
    POLICY = "policy"
    BUDGET = "budget"
    ROUTING = "routing"
    RECONCILE = "reconcile"


class VerifiedOutcome(StrEnum):
    """Closed terminal outcome vocabulary."""

    SUCCESS = "success"
    FAILURE = "failure"
    REVERTED = "reverted"
    UNKNOWN = "unknown"
    UNSAFE = "unsafe"


class NormalizationRefusalReason(StrEnum):
    """Content-free reasons that prevent an envelope from being created."""

    UNSUPPORTED_EVENT = "unsupported_event"
    INVALID_SOURCE = "invalid_source"
    MISSING_PROJECT = "missing_project"
    PROJECT_MISMATCH = "project_mismatch"
    UNKNOWN_FIELD = "unknown_field"
    REDACTION_REQUIRED = "redaction_required"
    UNSUPPORTED_REDACTION = "unsupported_redaction"
    MISSING_POLICY_DIGEST = "missing_policy_digest"
    INVALID_FEATURE = "invalid_feature"
    RISK_NOT_ELIGIBLE = "risk_not_eligible"
    INVALID_DECISION = "invalid_decision"
    INVALID_OUTCOME = "invalid_outcome"
    INVALID_OUTCOME_EVIDENCE = "invalid_outcome_evidence"
    BOUNDS_EXCEEDED = "bounds_exceeded"


class FallbackReason(StrEnum):
    """Closed runtime abstention reasons from the feature specification."""

    NO_ACTIVE_RULE = "no_active_rule"
    SCOPE_MISS = "scope_miss"
    NORMALIZATION_REFUSED = "normalization_refused"
    NO_LEAF = "no_leaf"
    MULTIPLE_LEAVES = "multiple_leaves"
    POLICY_CHANGED = "policy_changed"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CANARY_EXCLUDED = "canary_excluded"
    DRIFT_HOLD = "drift_hold"
    INTEGRITY_FAILURE = "integrity_failure"
    RUNTIME_ERROR = "runtime_error"


class ReceiptType(StrEnum):
    """Append-only lifecycle operations."""

    APPROVAL = "approval"
    RENEWAL = "renewal"
    PROMOTION = "promotion"
    ROLLBACK = "rollback"
    REVOCATION = "revocation"
    EXPIRY = "expiry"


class LifecycleState(StrEnum):
    """Persisted decision-candidate lifecycle states."""

    SHADOW = "shadow"
    CANARY = "canary"
    CANARY_10 = "canary_10"
    CANARY_50 = "canary_50"
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    ROLLED_BACK = "rolled_back"


class RolloutStage(StrEnum):
    """Approved ZDD rollout stages in monotonic order."""

    SHADOW = "shadow"
    CANARY = "canary"
    CANARY_10 = "canary_10"
    CANARY_50 = "canary_50"
    ACTIVE = "active"


DECISION_ACTIONS_V1: Mapping[DecisionKind, frozenset[str]] = MappingProxyType({
    DecisionKind.REVIEW: frozenset({"approve", "request_changes", "reject"}),
    DecisionKind.POLICY: frozenset({"allow", "deny", "require_approval"}),
    DecisionKind.BUDGET: frozenset({"within_limit", "reduce", "deny"}),
    DecisionKind.ROUTING: frozenset(
        {"route_local", "route_remote", "route_specialist"}
    ),
    DecisionKind.RECONCILE: frozenset({"keep_current", "retry", "rollback"}),
})


class _StrictDecisionModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        strict=True,
    )


def canonical_decision_json(value: object) -> str:
    """Serialize decision data with the replay/integrity canonical convention."""
    return canonical_replay_json(value)


def canonical_sha256(value: object) -> str:
    """Return the bounded lowercase SHA-256 identifier for canonical JSON."""
    import hashlib

    canonical = canonical_decision_json(value).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def decode_decision_json_object(
    data: str | bytes | bytearray,
) -> dict[str, object]:
    """Decode strict JSON, including duplicate-key and non-finite rejection."""
    return decode_replay_json_object(data)


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("decision timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _sorted_unique(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    return tuple(sorted(values))


class _CanonicalDigestModel(_StrictDecisionModel):
    """Base for immutable artifacts whose identifier covers every other field."""

    _digest_field: ClassVar[str]
    _maximum_canonical_bytes: ClassVar[int | None] = None

    @classmethod
    def create(cls, **data: object) -> Self:
        """Validate fields, calculate the canonical identifier, and revalidate."""
        staged = dict(data)
        staged[cls._digest_field] = _ZERO_SHA256
        validated = cls.model_validate(
            staged,
            context={"skip_digest_check": True},
        )
        unsigned = validated.model_dump(
            mode="json",
            by_alias=True,
            exclude={cls._digest_field},
        )
        sealed = validated.model_dump(mode="python", by_alias=True)
        sealed[cls._digest_field] = canonical_sha256(unsigned)
        return cls.model_validate(sealed)

    @model_validator(mode="after")
    def _canonical_digest_is_valid(self, info: ValidationInfo) -> Self:
        unsigned = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={self._digest_field},
        )
        expected = canonical_sha256(unsigned)
        context = info.context or {}
        if not context.get("skip_digest_check"):
            stored = cast(str, getattr(self, self._digest_field))
            if stored != expected:
                raise ValueError(
                    f"{self._digest_field} does not match the canonical digest"
                )
        maximum = self._maximum_canonical_bytes
        if maximum is not None:
            size = len(canonical_decision_json(self).encode("utf-8"))
            if size > maximum:
                raise ValueError(
                    f"canonical artifact exceeds {maximum} bytes ({size} bytes)"
                )
        return self


class VerifiedDecisionSourceV1(_StrictDecisionModel):
    """Capability marker produced only for a verified, signed, complete bundle."""

    source_run_id: SafeRunId
    source_bundle_digest: Sha256Digest
    project_id: BoundedIdentifier
    integrity: Literal["signed"]
    complete: Literal[True]


class RedactionSummaryV1(_StrictDecisionModel):
    """Content-free redaction counts with a closed category vocabulary."""

    count: NonNegativeCount
    kinds: Annotated[
        tuple[RedactionKind, ...],
        Field(max_length=6),
    ]

    @field_validator("kinds")
    @classmethod
    def _sort_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, label="redaction kinds")

    @model_validator(mode="after")
    def _count_is_coherent(self) -> Self:
        if (self.count == 0) != (not self.kinds):
            raise ValueError(
                "redaction kinds must be present exactly when count is non-zero"
            )
        return self


class OutcomeEvidenceV1(_StrictDecisionModel):
    """Content-free terminal evidence bound to the source decision event."""

    decision_event_digest: Sha256Digest
    outcome: VerifiedOutcome
    terminal_event_ids: Annotated[
        tuple[BoundedIdentifier, ...], Field(max_length=16)
    ]
    gate_digests: Annotated[tuple[Sha256Digest, ...], Field(max_length=16)]
    status_digests: Annotated[tuple[Sha256Digest, ...], Field(max_length=16)]

    @field_validator("outcome", mode="before")
    @classmethod
    def _parse_outcome(cls, value: object) -> object:
        if type(value) is str:
            return VerifiedOutcome(value)
        return value

    @field_validator("terminal_event_ids", "gate_digests", "status_digests")
    @classmethod
    def _sort_evidence(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _sorted_unique(value, label=info.field_name or "outcome evidence")

    @model_validator(mode="after")
    def _terminal_evidence_is_present(self) -> Self:
        has_terminal = bool(self.terminal_event_ids)
        has_digest = bool(self.gate_digests or self.status_digests)
        if self.outcome is VerifiedOutcome.UNKNOWN:
            if has_terminal or has_digest:
                raise ValueError("unknown outcomes must not claim terminal evidence")
        elif not (has_terminal and has_digest):
            raise ValueError(
                "terminal evidence requires an event ID and a gate/status digest"
            )
        return self


class DecisionContextV1(_CanonicalDigestModel):
    """Canonical pre-decision input for deterministic runtime lookup."""

    _digest_field = "context_id"
    _maximum_canonical_bytes = MAX_ENVELOPE_BYTES

    schema_version: Literal["gludd.decision-context/v1"] = Field(alias="schema")
    context_id: Sha256Digest
    project_id: BoundedIdentifier
    decision_kind: DecisionKind
    feature_schema: Sha256Digest
    policy_digest: Sha256Digest
    exact_guards: Annotated[
        dict[BoundedIdentifier, FeatureValue], Field(min_length=1, max_length=16)
    ]
    features: Annotated[
        dict[BoundedIdentifier, FeatureValue], Field(min_length=1, max_length=64)
    ]

    @field_validator("decision_kind", mode="before")
    @classmethod
    def _parse_kind(cls, value: object) -> object:
        if type(value) is str:
            return DecisionKind(value)
        return value

    @field_validator("exact_guards", "features")
    @classmethod
    def _maps_are_sorted(
        cls, value: dict[str, FeatureValue]
    ) -> dict[str, FeatureValue]:
        return dict(sorted(value.items()))

    @property
    def context_signature(self) -> str:
        """Return the exact guards/features signature used for runtime admission."""
        return canonical_sha256({
            "exact_guards": self.exact_guards,
            "features": self.features,
        })


class DecisionEnvelopeV1(_CanonicalDigestModel):
    """Canonical, content-safe evidence for one verified decision."""

    _digest_field = "envelope_id"
    _maximum_canonical_bytes = MAX_ENVELOPE_BYTES

    schema_version: Literal["gludd.decision-envelope/v1"] = Field(alias="schema")
    envelope_id: Sha256Digest
    source_run_id: SafeRunId
    source_event_digest: Sha256Digest
    source_bundle_digest: Sha256Digest
    project_id: BoundedIdentifier
    decision_kind: DecisionKind
    feature_schema: Sha256Digest
    policy_digest: Sha256Digest
    occurred_at: datetime
    exact_guards: Annotated[
        dict[BoundedIdentifier, FeatureValue], Field(min_length=1, max_length=16)
    ]
    features: Annotated[
        dict[BoundedIdentifier, FeatureValue], Field(min_length=1, max_length=64)
    ]
    decision: Annotated[
        str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    ]
    verified_outcome: VerifiedOutcome
    outcome_evidence: OutcomeEvidenceV1
    redaction: RedactionSummaryV1

    @field_validator("decision_kind", mode="before")
    @classmethod
    def _parse_kind(cls, value: object) -> object:
        if type(value) is str:
            return DecisionKind(value)
        return value

    @field_validator("verified_outcome", mode="before")
    @classmethod
    def _parse_verified_outcome(cls, value: object) -> object:
        if type(value) is str:
            return VerifiedOutcome(value)
        return value

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at_is_utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("exact_guards", "features")
    @classmethod
    def _maps_are_sorted(
        cls, value: dict[str, FeatureValue]
    ) -> dict[str, FeatureValue]:
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def _evidence_and_action_are_coherent(self) -> Self:
        if self.outcome_evidence.decision_event_digest != self.source_event_digest:
            raise ValueError("outcome evidence is not bound to the source event")
        if self.outcome_evidence.outcome is not self.verified_outcome:
            raise ValueError("outcome evidence does not match verified_outcome")
        if self.decision not in DECISION_ACTIONS_V1[self.decision_kind]:
            raise ValueError("decision is outside the decision-kind action vocabulary")
        return self

    @property
    def context_signature(self) -> str:
        """Return the exact runtime-context signature represented by this evidence."""
        return canonical_sha256({
            "exact_guards": self.exact_guards,
            "features": self.features,
        })

    def to_context(self) -> DecisionContextV1:
        """Project terminal evidence into the pre-decision runtime contract."""
        return DecisionContextV1.create(
            schema=DECISION_CONTEXT_SCHEMA_V1,
            project_id=self.project_id,
            decision_kind=self.decision_kind,
            feature_schema=self.feature_schema,
            policy_digest=self.policy_digest,
            exact_guards=self.exact_guards,
            features=self.features,
        )


class OutcomeCountsV1(_StrictDecisionModel):
    """Closed outcome counters attached to a proposed leaf."""

    success: NonNegativeCount
    failure: NonNegativeCount
    reverted: NonNegativeCount
    unknown: NonNegativeCount
    unsafe: NonNegativeCount

    @property
    def total(self) -> int:
        """Return the total number of terminal observations."""
        return self.success + self.failure + self.reverted + self.unknown + self.unsafe


class DecisionRuleNodeV1(_StrictDecisionModel):
    """One bounded binary split in an exported deterministic tree."""

    node_id: BoundedIdentifier
    feature_id: BoundedIdentifier
    operator: Literal["eq", "not_eq"]
    value: FeatureValue
    match_id: BoundedIdentifier
    miss_id: BoundedIdentifier

    @model_validator(mode="after")
    def _children_are_distinct(self) -> Self:
        if self.match_id == self.miss_id:
            raise ValueError("rule node children must be distinct")
        return self


class DecisionRuleLeafV1(_StrictDecisionModel):
    """One executable decision or explicit abstention leaf."""

    leaf_id: BoundedIdentifier
    decision: Annotated[
        str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    ] | None
    support: NonNegativeCount
    confidence: Rate
    outcome_counts: OutcomeCountsV1
    abstain: bool

    @model_validator(mode="after")
    def _leaf_is_coherent(self) -> Self:
        if self.abstain != (self.decision is None):
            raise ValueError("abstaining leaves must omit decision")
        if self.outcome_counts.total != self.support:
            raise ValueError("leaf outcome counts must equal support")
        return self


class DecisionRuleBundleV1(_CanonicalDigestModel):
    """Strict non-executable export of one bounded decision tree."""

    _digest_field = "candidate_digest"
    _maximum_canonical_bytes = 64 * 1024

    schema_version: Literal["gludd.decision-rule-bundle/v1"] = Field(alias="schema")
    candidate_digest: Sha256Digest
    project_id: BoundedIdentifier
    decision_kind: DecisionKind
    feature_schema: Sha256Digest
    policy_compatibility: Annotated[
        tuple[Sha256Digest, ...], Field(min_length=1, max_length=16)
    ]
    risk_scope: Literal["low", "medium"]
    observed_context_digests: Annotated[
        tuple[Sha256Digest, ...], Field(min_length=1, max_length=MAX_RULE_CONTEXTS)
    ]
    root_id: BoundedIdentifier
    default_leaf_id: BoundedIdentifier
    nodes: Annotated[
        tuple[DecisionRuleNodeV1, ...], Field(min_length=1, max_length=MAX_RULE_NODES)
    ]
    leaves: Annotated[
        tuple[DecisionRuleLeafV1, ...], Field(min_length=2, max_length=MAX_RULE_LEAVES)
    ]
    corpus_digest: Sha256Digest
    training_recipe_digest: Sha256Digest
    dependency_lock_digest: Sha256Digest
    created_at: datetime
    expires_at: datetime
    maximum_use_count: Annotated[int, Field(ge=1, le=1_000_000_000)]

    @field_validator("decision_kind", mode="before")
    @classmethod
    def _parse_kind(cls, value: object) -> object:
        if type(value) is str:
            return DecisionKind(value)
        return value

    @field_validator("policy_compatibility")
    @classmethod
    def _sort_policy_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, label="policy compatibility digests")

    @field_validator("observed_context_digests")
    @classmethod
    def _sort_context_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, label="observed context digests")

    @field_validator("nodes")
    @classmethod
    def _sort_nodes(
        cls, value: tuple[DecisionRuleNodeV1, ...]
    ) -> tuple[DecisionRuleNodeV1, ...]:
        return tuple(sorted(value, key=lambda node: node.node_id))

    @field_validator("leaves")
    @classmethod
    def _sort_leaves(
        cls, value: tuple[DecisionRuleLeafV1, ...]
    ) -> tuple[DecisionRuleLeafV1, ...]:
        return tuple(sorted(value, key=lambda leaf: leaf.leaf_id))

    @field_validator("created_at", "expires_at")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @model_validator(mode="after")
    def _tree_is_safe_and_bounded(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("rule bundle expiry must follow creation")

        nodes = {node.node_id: node for node in self.nodes}
        leaves = {leaf.leaf_id: leaf for leaf in self.leaves}
        if len(nodes) != len(self.nodes) or len(leaves) != len(self.leaves):
            raise ValueError("rule node and leaf IDs must be unique")
        if set(nodes) & set(leaves):
            raise ValueError("rule node and leaf IDs must not overlap")
        if self.root_id not in nodes:
            raise ValueError("rule tree root must reference a node")

        default = leaves.get(self.default_leaf_id)
        if default is None or not default.abstain:
            raise ValueError("default leaf must abstain")

        all_ids = set(nodes) | set(leaves)
        for node in self.nodes:
            if node.match_id not in all_ids or node.miss_id not in all_ids:
                raise ValueError("rule node references an unknown child")

        reached: set[str] = set()

        def visit(identifier: str, depth: int, active: frozenset[str]) -> None:
            if identifier in active:
                raise ValueError("rule tree must not contain cycles")
            reached.add(identifier)
            node = nodes.get(identifier)
            if node is None:
                return
            if depth >= MAX_RULE_DEPTH:
                raise ValueError(f"rule tree depth exceeds {MAX_RULE_DEPTH}")
            next_active = active | {identifier}
            visit(node.match_id, depth + 1, next_active)
            visit(node.miss_id, depth + 1, next_active)

        visit(self.root_id, 0, frozenset())
        if reached != all_ids:
            raise ValueError("rule tree contains unreachable nodes or leaves")

        if not any(not leaf.abstain for leaf in self.leaves):
            raise ValueError("rule tree must contain a non-abstaining leaf")
        actions = DECISION_ACTIONS_V1[self.decision_kind]
        if any(
            leaf.decision not in actions for leaf in self.leaves if not leaf.abstain
        ):
            raise ValueError("rule leaf decision is outside the action vocabulary")
        return self


class LeafEvaluationV1(_StrictDecisionModel):
    """Bounded, content-free held-out result for one exported leaf."""

    leaf_id: BoundedIdentifier
    support: NonNegativeCount
    confidence: Rate


class EvaluationReportV1(_CanonicalDigestModel):
    """Deterministic offline replay report without source payloads."""

    _digest_field = "report_digest"
    _maximum_canonical_bytes = 64 * 1024

    schema_version: Literal["gludd.decision-evaluation-report/v1"] = Field(
        alias="schema"
    )
    report_digest: Sha256Digest
    candidate_digest: Sha256Digest
    corpus_digest: Sha256Digest
    project_id: BoundedIdentifier
    decision_kind: DecisionKind
    created_at: datetime
    support_count: NonNegativeCount
    root_task_count: NonNegativeCount
    utc_day_count: NonNegativeCount
    source_agent_count: NonNegativeCount
    exact_match_count: NonNegativeCount
    abstention_count: NonNegativeCount
    precision: Rate
    leaf_results: Annotated[
        tuple[LeafEvaluationV1, ...], Field(max_length=MAX_RULE_LEAVES)
    ]
    false_automation_count: NonNegativeCount
    conflict_count: NonNegativeCount
    unknown_feature_count: NonNegativeCount
    policy_mismatch_count: NonNegativeCount
    verified_failure_count: NonNegativeCount
    rollback_count: NonNegativeCount
    safety_violation_count: NonNegativeCount
    estimated_agent_calls_avoided: NonNegativeCount
    estimated_tokens_avoided: NonNegativeCount
    historical_policy_digest: Sha256Digest
    current_policy_digest: Sha256Digest
    deterministic_replay_runs: Annotated[int, Field(ge=2, le=16)]

    @field_validator("decision_kind", mode="before")
    @classmethod
    def _parse_kind(cls, value: object) -> object:
        if type(value) is str:
            return DecisionKind(value)
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_is_utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("leaf_results")
    @classmethod
    def _sort_leaf_results(
        cls, value: tuple[LeafEvaluationV1, ...]
    ) -> tuple[LeafEvaluationV1, ...]:
        identifiers = [result.leaf_id for result in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("leaf evaluation IDs must be unique")
        return tuple(sorted(value, key=lambda result: result.leaf_id))

    @model_validator(mode="after")
    def _coverage_counts_are_coherent(self) -> Self:
        if self.exact_match_count + self.abstention_count > self.support_count:
            raise ValueError("evaluation coverage counts exceed support")
        return self


class ApprovalReceiptV1(_CanonicalDigestModel):
    """Immutable digest-bound approval or lifecycle receipt."""

    _digest_field = "receipt_digest"
    _maximum_canonical_bytes = 32 * 1024

    schema_version: Literal["gludd.decision-approval-receipt/v1"] = Field(
        alias="schema"
    )
    receipt_digest: Sha256Digest
    receipt_type: ReceiptType
    lifecycle_state: LifecycleState
    previous_receipt_digest: Sha256Digest | None
    candidate_digest: Sha256Digest
    corpus_digest: Sha256Digest
    evaluator_report_digest: Sha256Digest
    feature_schema: Sha256Digest
    policy_digest: Sha256Digest
    source_code_digest: Sha256Digest
    dependency_lock_digest: Sha256Digest
    training_recipe_digest: Sha256Digest
    project_id: BoundedIdentifier
    decision_kind: DecisionKind
    approver_identity_hmac: HmacSha256Digest
    authorization_evidence_digest: Sha256Digest
    created_at: datetime
    expires_at: datetime
    risk_class: Literal["low", "medium"]
    rollout_plan: Annotated[
        tuple[RolloutStage, ...], Field(min_length=1, max_length=5)
    ]
    maximum_use_count: Annotated[int, Field(ge=1, le=1_000_000_000)]

    @field_validator("receipt_type", mode="before")
    @classmethod
    def _parse_receipt_type(cls, value: object) -> object:
        if type(value) is str:
            return ReceiptType(value)
        return value

    @field_validator("lifecycle_state", mode="before")
    @classmethod
    def _parse_lifecycle_state(cls, value: object) -> object:
        if type(value) is str:
            return LifecycleState(value)
        return value

    @field_validator("decision_kind", mode="before")
    @classmethod
    def _parse_kind(cls, value: object) -> object:
        if type(value) is str:
            return DecisionKind(value)
        return value

    @field_validator("rollout_plan", mode="before")
    @classmethod
    def _parse_rollout_plan(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(
                RolloutStage(item) if type(item) is str else item for item in value
            )
        return value

    @field_validator("created_at", "expires_at")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @model_validator(mode="after")
    def _receipt_is_coherent(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("receipt expiry must follow creation")
        if self.receipt_type is ReceiptType.APPROVAL:
            if self.previous_receipt_digest is not None:
                raise ValueError("initial approval must not name a previous receipt")
        elif self.previous_receipt_digest is None:
            raise ValueError("lifecycle receipts require the previous receipt digest")

        permitted_states: dict[ReceiptType, frozenset[LifecycleState]] = {
            ReceiptType.APPROVAL: frozenset({LifecycleState.SHADOW}),
            ReceiptType.RENEWAL: frozenset({LifecycleState.SHADOW}),
            ReceiptType.PROMOTION: frozenset(
                {
                    LifecycleState.CANARY,
                    LifecycleState.CANARY_10,
                    LifecycleState.CANARY_50,
                    LifecycleState.ACTIVE,
                }
            ),
            ReceiptType.ROLLBACK: frozenset({LifecycleState.ROLLED_BACK}),
            ReceiptType.REVOCATION: frozenset({LifecycleState.REVOKED}),
            ReceiptType.EXPIRY: frozenset({LifecycleState.EXPIRED}),
        }
        if self.lifecycle_state not in permitted_states[self.receipt_type]:
            raise ValueError("receipt type and lifecycle state are incompatible")

        order = {stage: index for index, stage in enumerate(RolloutStage)}
        if self.rollout_plan[0] is not RolloutStage.SHADOW:
            raise ValueError("rollout plan must begin in shadow")
        indexes = [order[stage] for stage in self.rollout_plan]
        if indexes != sorted(set(indexes)):
            raise ValueError("rollout plan must be unique and monotonic")
        return self


class NormalizationRefusalV1(_StrictDecisionModel):
    """Typed refusal that cannot echo rejected source content."""

    schema_version: Literal["gludd.decision-normalization-refusal/v1"] = Field(
        default="gludd.decision-normalization-refusal/v1",
        alias="schema",
    )
    reason: NormalizationRefusalReason
    decision_kind: DecisionKind | None

    @field_validator("reason", mode="before")
    @classmethod
    def _parse_reason(cls, value: object) -> object:
        if type(value) is str:
            return NormalizationRefusalReason(value)
        return value

    @field_validator("decision_kind", mode="before")
    @classmethod
    def _parse_kind(cls, value: object) -> object:
        if type(value) is str:
            return DecisionKind(value)
        return value


class DecisionAbstentionV1(_StrictDecisionModel):
    """Typed runtime abstention with digest-only optional provenance."""

    schema_version: Literal["gludd.decision-abstention/v1"] = Field(
        default="gludd.decision-abstention/v1",
        alias="schema",
    )
    reason: FallbackReason
    normalization_reason: NormalizationRefusalReason | None = None
    context_id: Sha256Digest | None = None
    candidate_digest: Sha256Digest | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _parse_reason(cls, value: object) -> object:
        if type(value) is str:
            return FallbackReason(value)
        return value

    @field_validator("normalization_reason", mode="before")
    @classmethod
    def _parse_normalization_reason(cls, value: object) -> object:
        if type(value) is str:
            return NormalizationRefusalReason(value)
        return value

    @model_validator(mode="after")
    def _normalization_reason_is_scoped(self) -> Self:
        is_normalization = self.reason is FallbackReason.NORMALIZATION_REFUSED
        if is_normalization != (self.normalization_reason is not None):
            raise ValueError(
                "normalization_reason is required only for normalization_refused"
            )
        return self


def parse_decision_envelope(
    data: str | bytes | bytearray,
) -> DecisionEnvelopeV1:
    """Parse one strict v1 envelope, rejecting duplicate JSON keys."""
    payload = decode_decision_json_object(data)
    return DecisionEnvelopeV1.model_validate_json(canonical_decision_json(payload))


def parse_rule_bundle(
    data: str | bytes | bytearray,
) -> DecisionRuleBundleV1:
    """Parse one strict v1 exported rule bundle."""
    payload = decode_decision_json_object(data)
    return DecisionRuleBundleV1.model_validate_json(canonical_decision_json(payload))


def parse_evaluation_report(
    data: str | bytes | bytearray,
) -> EvaluationReportV1:
    """Parse one strict v1 offline evaluation report."""
    payload = decode_decision_json_object(data)
    return EvaluationReportV1.model_validate_json(canonical_decision_json(payload))


def parse_approval_receipt(
    data: str | bytes | bytearray,
) -> ApprovalReceiptV1:
    """Parse one strict v1 immutable lifecycle receipt."""
    payload = decode_decision_json_object(data)
    return ApprovalReceiptV1.model_validate_json(canonical_decision_json(payload))


__all__ = [
    "DECISION_ABSTENTION_SCHEMA_V1",
    "DECISION_ACTIONS_V1",
    "DECISION_APPROVAL_RECEIPT_SCHEMA_V1",
    "DECISION_CONTEXT_SCHEMA_V1",
    "DECISION_ENVELOPE_SCHEMA_V1",
    "DECISION_EVALUATION_REPORT_SCHEMA_V1",
    "DECISION_RULE_BUNDLE_SCHEMA_V1",
    "MAX_ENVELOPE_BYTES",
    "MAX_RULE_CONTEXTS",
    "MAX_RULE_DEPTH",
    "MAX_RULE_LEAVES",
    "MAX_RULE_NODES",
    "NORMALIZATION_REFUSAL_SCHEMA_V1",
    "ApprovalReceiptV1",
    "DecisionAbstentionV1",
    "DecisionContextV1",
    "DecisionEnvelopeV1",
    "DecisionKind",
    "DecisionRuleBundleV1",
    "DecisionRuleLeafV1",
    "DecisionRuleNodeV1",
    "EvaluationReportV1",
    "FallbackReason",
    "FeatureValue",
    "HmacSha256Digest",
    "LeafEvaluationV1",
    "LifecycleState",
    "NormalizationRefusalReason",
    "NormalizationRefusalV1",
    "OutcomeCountsV1",
    "OutcomeEvidenceV1",
    "ReceiptType",
    "RedactionKind",
    "RedactionSummaryV1",
    "RolloutStage",
    "VerifiedDecisionSourceV1",
    "VerifiedOutcome",
    "canonical_decision_json",
    "canonical_sha256",
    "decode_decision_json_object",
    "parse_approval_receipt",
    "parse_decision_envelope",
    "parse_evaluation_report",
    "parse_rule_bundle",
]
