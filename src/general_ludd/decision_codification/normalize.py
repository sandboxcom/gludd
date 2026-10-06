"""Allowlist-first normalization of verified replay decision events."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from general_ludd.decision_codification.schema import (
    DECISION_ACTIONS_V1,
    DECISION_CONTEXT_SCHEMA_V1,
    DECISION_ENVELOPE_SCHEMA_V1,
    DecisionContextV1,
    DecisionEnvelopeV1,
    DecisionKind,
    FeatureValue,
    NormalizationRefusalReason,
    NormalizationRefusalV1,
    OutcomeEvidenceV1,
    RedactionKind,
    RedactionSummaryV1,
    VerifiedDecisionSourceV1,
    VerifiedOutcome,
    canonical_decision_json,
    canonical_sha256,
)
from general_ludd.replay.schema import (
    BoundedIdentifier,
    EventEnvelopeV1,
    Sha256Digest,
)
from general_ludd.security.redaction import RedactionLimits, redact_for_persistence

FEATURE_REGISTRY_SCHEMA_V1 = "gludd.decision-feature-registry/v1"

_EVENT_KINDS: dict[str, DecisionKind] = {
    "review.decided": DecisionKind.REVIEW,
    "policy.decided": DecisionKind.POLICY,
    "budget.decided": DecisionKind.BUDGET,
    "reconcile.decided": DecisionKind.RECONCILE,
}
_ALLOWED_PAYLOAD_FIELDS = frozenset(
    {
        "policy_digest",
        "features",
        "decision",
        "verified_outcome",
        "outcome_evidence",
    }
)
_SUPPORTED_REDACTION_KINDS = frozenset(
    {
        "credential_text",
        "credential_url",
        "hidden_reasoning",
        "secret_key",
        "unsupported_key",
        "unsupported_type",
    }
)
_NORMALIZATION_LIMITS = RedactionLimits(
    max_depth=6,
    max_items=256,
    max_string_chars=256,
    max_total_bytes=16 * 1024,
)
_SHA256_ADAPTER = TypeAdapter(Sha256Digest, config=ConfigDict(strict=True))


class FeatureRuleV1(BaseModel):
    """One frozen, digest-addressed allowlist entry."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_key: BoundedIdentifier
    feature_key: BoundedIdentifier
    value_kind: Literal["categorical", "boolean", "retry", "cost", "latency"]
    allowed_values: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    required: bool = False


FEATURE_REGISTRY_V1: tuple[FeatureRuleV1, ...] = (
    FeatureRuleV1(
        source_key="work_type",
        feature_key="work_type",
        value_kind="categorical",
        allowed_values=(
            "budget",
            "code",
            "documentation",
            "operations",
            "policy",
            "reconcile",
            "review",
            "routing",
            "test",
        ),
    ),
    FeatureRuleV1(
        source_key="queue",
        feature_key="queue",
        value_kind="categorical",
        allowed_values=(
            "approval",
            "batch",
            "default",
            "interactive",
            "reconciliation",
        ),
    ),
    FeatureRuleV1(
        source_key="risk_band",
        feature_key="risk_band",
        value_kind="categorical",
        allowed_values=("low", "medium"),
        required=True,
    ),
    FeatureRuleV1(
        source_key="resource_profile",
        feature_key="resource_profile",
        value_kind="categorical",
        allowed_values=("accelerator", "cpu", "mixed", "network", "none", "storage"),
    ),
    FeatureRuleV1(
        source_key="provider_class",
        feature_key="provider_class",
        value_kind="categorical",
        allowed_values=("cloud", "hybrid", "local", "none"),
    ),
    FeatureRuleV1(
        source_key="operation_class",
        feature_key="operation_class",
        value_kind="categorical",
        allowed_values=(
            "read",
            "reconcile",
            "retry",
            "review",
            "rollback",
            "route",
            "update",
            "write",
        ),
        required=True,
    ),
    FeatureRuleV1(
        source_key="status",
        feature_key="status",
        value_kind="categorical",
        allowed_values=(
            "blocked",
            "cancelled",
            "failed",
            "pending",
            "running",
            "succeeded",
        ),
    ),
    FeatureRuleV1(
        source_key="approval_required",
        feature_key="approval_required",
        value_kind="boolean",
    ),
    FeatureRuleV1(
        source_key="fallback_allowed",
        feature_key="fallback_allowed",
        value_kind="boolean",
    ),
    FeatureRuleV1(
        source_key="reversible",
        feature_key="reversible",
        value_kind="boolean",
    ),
    FeatureRuleV1(
        source_key="retry_count",
        feature_key="retry_bucket",
        value_kind="retry",
    ),
    FeatureRuleV1(
        source_key="estimated_cost_microusd",
        feature_key="cost_bucket",
        value_kind="cost",
    ),
    FeatureRuleV1(
        source_key="latency_ms",
        feature_key="latency_bucket",
        value_kind="latency",
    ),
    FeatureRuleV1(
        source_key="required_evidence",
        feature_key="required_evidence",
        value_kind="boolean",
    ),
)

_FEATURE_RULES = {rule.source_key: rule for rule in FEATURE_REGISTRY_V1}
_FEATURE_SCHEMA_DOCUMENT: dict[str, object] = {
    "schema": FEATURE_REGISTRY_SCHEMA_V1,
    "features": [
        rule.model_dump(mode="json") for rule in sorted(
            FEATURE_REGISTRY_V1, key=lambda item: item.source_key
        )
    ],
    "actions": {
        kind.value: sorted(actions)
        for kind, actions in sorted(
            DECISION_ACTIONS_V1.items(), key=lambda item: item[0].value
        )
    },
    "exact_guards": ["action_vocabulary", "operation_class", "risk_band"],
    "numeric_units": {
        "estimated_cost_microusd": "integer_micro_usd",
        "latency_ms": "integer_milliseconds",
        "retry_count": "integer_attempts_after_first",
    },
}
FEATURE_SCHEMA_V1_DIGEST = canonical_sha256(_FEATURE_SCHEMA_DOCUMENT)


def _refusal(
    reason: NormalizationRefusalReason,
    kind: DecisionKind | None,
) -> NormalizationRefusalV1:
    return NormalizationRefusalV1(reason=reason, decision_kind=kind)


def _bucket_retry(value: object) -> str | None:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        return None
    if value == 0:
        return "none"
    if value == 1:
        return "one"
    if value <= 3:
        return "two_to_three"
    return "four_plus"


def _bucket_cost(value: object) -> str | None:
    if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
        return None
    if value == 0:
        return "zero"
    if value <= 1_000_000:
        return "low"
    if value <= 100_000_000:
        return "medium"
    return "high"


def _bucket_latency(value: object) -> str | None:
    if type(value) is not int or not 0 <= value <= 604_800_000:
        return None
    if value < 1_000:
        return "subsecond"
    if value < 60_000:
        return "seconds"
    if value < 3_600_000:
        return "minutes"
    return "hours"


def _normalize_features(
    raw: object,
) -> tuple[dict[str, FeatureValue] | None, NormalizationRefusalReason | None]:
    if type(raw) is not dict:
        return None, NormalizationRefusalReason.INVALID_FEATURE
    raw_features = cast(dict[str, object], raw)
    if set(raw_features) - set(_FEATURE_RULES):
        return None, NormalizationRefusalReason.INVALID_FEATURE

    risk = raw_features.get("risk_band")
    if risk in {"high", "critical"}:
        return None, NormalizationRefusalReason.RISK_NOT_ELIGIBLE

    normalized: dict[str, FeatureValue] = {}
    for rule in FEATURE_REGISTRY_V1:
        if rule.source_key not in raw_features or raw_features[rule.source_key] is None:
            if rule.required:
                return None, NormalizationRefusalReason.INVALID_FEATURE
            normalized[rule.feature_key] = "missing"
            continue

        value = raw_features[rule.source_key]
        if rule.value_kind == "categorical":
            if type(value) is not str or value not in rule.allowed_values:
                return None, NormalizationRefusalReason.INVALID_FEATURE
            normalized[rule.feature_key] = value
        elif rule.value_kind == "boolean":
            if type(value) is not bool:
                return None, NormalizationRefusalReason.INVALID_FEATURE
            if rule.source_key == "required_evidence":
                normalized[rule.feature_key] = "present" if value else "missing"
            else:
                normalized[rule.feature_key] = value
        else:
            bucket = {
                "retry": _bucket_retry,
                "cost": _bucket_cost,
                "latency": _bucket_latency,
            }[rule.value_kind](value)
            if bucket is None:
                return None, NormalizationRefusalReason.INVALID_FEATURE
            normalized[rule.feature_key] = bucket
    return dict(sorted(normalized.items())), None


def normalize_decision_context(
    *,
    project_id: str,
    expected_project_id: str,
    decision_kind: DecisionKind,
    policy_digest: str,
    features: object,
) -> DecisionContextV1 | NormalizationRefusalV1:
    """Normalize one structured pre-decision context without an answer or outcome."""
    if (
        not expected_project_id
        or not project_id
        or project_id != expected_project_id
    ):
        return _refusal(NormalizationRefusalReason.PROJECT_MISMATCH, decision_kind)
    if not isinstance(decision_kind, DecisionKind):
        return _refusal(NormalizationRefusalReason.UNSUPPORTED_EVENT, None)
    try:
        validated_policy_digest = _SHA256_ADAPTER.validate_python(
            policy_digest, strict=True
        )
    except ValidationError:
        return _refusal(
            NormalizationRefusalReason.MISSING_POLICY_DIGEST, decision_kind
        )
    if type(features) is not dict:
        return _refusal(NormalizationRefusalReason.INVALID_FEATURE, decision_kind)
    raw_features = cast(dict[str, object], features)
    if set(raw_features) - set(_FEATURE_RULES):
        return _refusal(NormalizationRefusalReason.INVALID_FEATURE, decision_kind)
    redacted = redact_for_persistence(raw_features, limits=_NORMALIZATION_LIMITS)
    if redacted.metadata.truncated:
        return _refusal(NormalizationRefusalReason.BOUNDS_EXCEEDED, decision_kind)
    if redacted.metadata.redaction_count:
        return _refusal(NormalizationRefusalReason.REDACTION_REQUIRED, decision_kind)
    normalized, feature_error = _normalize_features(redacted.value)
    if feature_error is not None or normalized is None:
        return _refusal(
            feature_error or NormalizationRefusalReason.INVALID_FEATURE,
            decision_kind,
        )
    exact_guards: dict[str, FeatureValue] = {
        "action_vocabulary": f"{decision_kind.value}.v1",
        "operation_class": normalized["operation_class"],
        "risk_band": normalized["risk_band"],
    }
    try:
        return DecisionContextV1.create(
            schema=DECISION_CONTEXT_SCHEMA_V1,
            project_id=project_id,
            decision_kind=decision_kind,
            feature_schema=FEATURE_SCHEMA_V1_DIGEST,
            policy_digest=validated_policy_digest,
            exact_guards=exact_guards,
            features=normalized,
        )
    except ValidationError:
        return _refusal(NormalizationRefusalReason.BOUNDS_EXCEEDED, decision_kind)


def normalize_verified_decision_event(
    event: EventEnvelopeV1,
    *,
    source: VerifiedDecisionSourceV1,
    expected_project_id: str,
) -> DecisionEnvelopeV1 | NormalizationRefusalV1:
    """Build an envelope or return a typed, content-free refusal.

    ``source`` is a capability marker emitted only after bundle signature and
    completeness verification.  Unknown keys are rejected before values cross
    the redaction boundary, and no rejected value is included in the result.
    """
    kind = _EVENT_KINDS.get(event.type)
    if kind is None:
        return _refusal(NormalizationRefusalReason.UNSUPPORTED_EVENT, None)

    if event.project_id is None:
        return _refusal(NormalizationRefusalReason.MISSING_PROJECT, kind)
    if (
        not expected_project_id
        or event.project_id != expected_project_id
        or source.project_id != expected_project_id
    ):
        return _refusal(NormalizationRefusalReason.PROJECT_MISMATCH, kind)

    payload = event.payload
    if set(payload) - _ALLOWED_PAYLOAD_FIELDS:
        return _refusal(NormalizationRefusalReason.UNKNOWN_FIELD, kind)

    redacted = redact_for_persistence(payload, limits=_NORMALIZATION_LIMITS)
    if redacted.metadata.truncated:
        return _refusal(NormalizationRefusalReason.BOUNDS_EXCEEDED, kind)
    if redacted.metadata.redaction_count:
        return _refusal(NormalizationRefusalReason.REDACTION_REQUIRED, kind)
    if type(redacted.value) is not dict:
        return _refusal(NormalizationRefusalReason.INVALID_SOURCE, kind)
    safe_payload = cast(dict[str, object], redacted.value)

    if set(event.redaction.kinds) - _SUPPORTED_REDACTION_KINDS:
        return _refusal(NormalizationRefusalReason.UNSUPPORTED_REDACTION, kind)
    try:
        redaction = RedactionSummaryV1(
            count=event.redaction.count,
            kinds=cast(tuple[RedactionKind, ...], tuple(event.redaction.kinds)),
        )
    except ValidationError:
        return _refusal(NormalizationRefusalReason.UNSUPPORTED_REDACTION, kind)

    policy_digest = safe_payload.get("policy_digest")
    if policy_digest is None:
        return _refusal(NormalizationRefusalReason.MISSING_POLICY_DIGEST, kind)
    try:
        validated_policy_digest = _SHA256_ADAPTER.validate_python(
            policy_digest, strict=True
        )
    except ValidationError:
        return _refusal(NormalizationRefusalReason.MISSING_POLICY_DIGEST, kind)

    features, feature_error = _normalize_features(safe_payload.get("features"))
    if feature_error is not None or features is None:
        return _refusal(
            feature_error or NormalizationRefusalReason.INVALID_FEATURE,
            kind,
        )

    decision = safe_payload.get("decision")
    if type(decision) is not str or decision not in DECISION_ACTIONS_V1[kind]:
        return _refusal(NormalizationRefusalReason.INVALID_DECISION, kind)

    raw_outcome = safe_payload.get("verified_outcome")
    if type(raw_outcome) is not str:
        return _refusal(NormalizationRefusalReason.INVALID_OUTCOME, kind)
    try:
        outcome = VerifiedOutcome(raw_outcome)
    except ValueError:
        return _refusal(NormalizationRefusalReason.INVALID_OUTCOME, kind)

    raw_evidence = safe_payload.get("outcome_evidence")
    if type(raw_evidence) is not dict:
        return _refusal(NormalizationRefusalReason.INVALID_OUTCOME_EVIDENCE, kind)
    try:
        evidence = OutcomeEvidenceV1.model_validate_json(
            canonical_decision_json(raw_evidence)
        )
    except ValidationError:
        return _refusal(NormalizationRefusalReason.INVALID_OUTCOME_EVIDENCE, kind)
    if evidence.decision_event_digest != event.digest or evidence.outcome is not outcome:
        return _refusal(NormalizationRefusalReason.INVALID_OUTCOME_EVIDENCE, kind)

    exact_guards: dict[str, FeatureValue] = {
        "action_vocabulary": f"{kind.value}.v1",
        "operation_class": features["operation_class"],
        "risk_band": features["risk_band"],
    }
    try:
        return DecisionEnvelopeV1.create(
            schema=DECISION_ENVELOPE_SCHEMA_V1,
            source_run_id=source.source_run_id,
            source_event_digest=event.digest,
            source_bundle_digest=source.source_bundle_digest,
            project_id=event.project_id,
            decision_kind=kind,
            feature_schema=FEATURE_SCHEMA_V1_DIGEST,
            policy_digest=validated_policy_digest,
            occurred_at=event.occurred_at,
            exact_guards=exact_guards,
            features=features,
            decision=decision,
            verified_outcome=outcome,
            outcome_evidence=evidence,
            redaction=redaction,
        )
    except ValidationError:
        return _refusal(NormalizationRefusalReason.BOUNDS_EXCEEDED, kind)


__all__ = [
    "FEATURE_REGISTRY_SCHEMA_V1",
    "FEATURE_REGISTRY_V1",
    "FEATURE_SCHEMA_V1_DIGEST",
    "FeatureRuleV1",
    "normalize_decision_context",
    "normalize_verified_decision_event",
]
