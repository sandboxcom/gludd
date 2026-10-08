"""Adversarial tests for allowlist-first decision normalization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from general_ludd.decision_codification.normalize import (
    FEATURE_SCHEMA_V1_DIGEST,
    normalize_verified_decision_event,
    normalize_verified_decision_outcome_event,
)
from general_ludd.decision_codification.schema import (
    DecisionEnvelopeV1,
    NormalizationRefusalReason,
    NormalizationRefusalV1,
    OutcomeEvidenceV1,
    VerifiedDecisionSourceV1,
    canonical_decision_json,
)
from general_ludd.replay.schema import CorrelationV1, EventEnvelopeV1, RedactionV1

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def _source(*, project_id: str = "project-1") -> VerifiedDecisionSourceV1:
    return VerifiedDecisionSourceV1(
        source_run_id="run-1",
        source_bundle_digest=SHA_B,
        project_id=project_id,
        integrity="signed",
        complete=True,
    )


def _payload() -> dict[str, object]:
    return {
        "policy_digest": SHA_C,
        "features": {
            "work_type": "code",
            "queue": "batch",
            "risk_band": "low",
            "resource_profile": "cpu",
            "provider_class": "local",
            "operation_class": "review",
            "status": "succeeded",
            "approval_required": False,
            "fallback_allowed": True,
            "reversible": True,
            "retry_count": 2,
            "estimated_cost_microusd": 2_000_000,
            "latency_ms": 61_000,
            "required_evidence": True,
        },
        "decision": "approve",
        "verified_outcome": "success",
        "outcome_evidence": {
            "decision_event_digest": SHA_A,
            "outcome": "success",
            "terminal_event_ids": ["terminal-1"],
            "gate_digests": [SHA_B],
            "status_digests": [],
        },
    }


def _event(
    *,
    event_type: str = "review.decided",
    project_id: str | None = "project-1",
    payload: dict[str, object] | None = None,
    redaction: RedactionV1 | None = None,
) -> EventEnvelopeV1:
    return EventEnvelopeV1(
        schema="gludd.run-event/v1",
        sequence=2,
        event_id="event-2",
        occurred_at=NOW,
        recorded_at=NOW,
        type=event_type,  # type: ignore[arg-type]
        project_id=project_id,
        correlation=CorrelationV1(
            todo_id="todo-1", task_id="task-1", trace_id=None
        ),
        payload=_payload() if payload is None else payload,
        redaction=redaction or RedactionV1(count=0, kinds=()),
        digest=SHA_A,
    )


def _normalize(
    event: EventEnvelopeV1,
    *,
    source: VerifiedDecisionSourceV1 | None = None,
    expected_project_id: str = "project-1",
) -> DecisionEnvelopeV1 | NormalizationRefusalV1:
    return normalize_verified_decision_event(
        event,
        source=source or _source(),
        expected_project_id=expected_project_id,
    )


def _outcome_event(
    *,
    event_type: str = "decision.outcome",
    project_id: str | None = "project-1",
    payload: dict[str, object] | None = None,
    redaction: RedactionV1 | None = None,
) -> EventEnvelopeV1:
    return EventEnvelopeV1(
        schema="gludd.run-event/v1",
        sequence=3,
        event_id="outcome-3",
        occurred_at=NOW,
        recorded_at=NOW,
        type=event_type,  # type: ignore[arg-type]
        project_id=project_id,
        correlation=CorrelationV1(
            todo_id="todo-1", task_id="task-1", trace_id=None
        ),
        payload=(
            {
                "decision_event_digest": SHA_A,
                "verified_outcome": "success",
                "terminal_event_ids": ["terminal-1"],
                "gate_digests": [SHA_B],
                "status_digests": [],
            }
            if payload is None
            else payload
        ),
        redaction=redaction or RedactionV1(count=0, kinds=()),
        digest=SHA_C,
    )


def test_normalize_builds_content_safe_canonical_envelope() -> None:
    result = _normalize(_event())

    assert isinstance(result, DecisionEnvelopeV1)
    assert result.feature_schema == FEATURE_SCHEMA_V1_DIGEST
    assert result.decision_kind.value == "review"
    assert result.features["retry_bucket"] == "two_to_three"
    assert result.features["cost_bucket"] == "medium"
    assert result.features["latency_bucket"] == "minutes"
    assert result.features["required_evidence"] == "present"
    assert result.exact_guards == {
        "action_vocabulary": "review.v1",
        "operation_class": "review",
        "risk_band": "low",
    }
    assert "retry_count" not in result.features
    assert "estimated_cost_microusd" not in result.features

    payload = _payload()
    feature_payload = cast(dict[str, object], payload["features"])
    payload["features"] = dict(reversed(list(feature_payload.items())))
    reordered = _normalize(_event(payload=payload))
    assert isinstance(reordered, DecisionEnvelopeV1)
    assert canonical_decision_json(reordered) == canonical_decision_json(result)


def test_separate_verified_outcome_links_to_the_immutable_decision_digest() -> None:
    evidence = normalize_verified_decision_outcome_event(
        _outcome_event(),
        expected_project_id="project-1",
    )
    assert isinstance(evidence, OutcomeEvidenceV1)
    assert evidence.decision_event_digest == SHA_A
    assert evidence.outcome.value == "success"

    payload = _payload()
    payload.pop("verified_outcome")
    payload.pop("outcome_evidence")
    normalized = normalize_verified_decision_event(
        _event(payload=payload),
        source=_source(),
        expected_project_id="project-1",
        linked_outcome=evidence,
    )
    assert isinstance(normalized, DecisionEnvelopeV1)
    assert normalized.source_event_digest == SHA_A
    assert normalized.verified_outcome.value == "success"


@pytest.mark.parametrize(
    ("event", "expected_project", "reason"),
    [
        (
            _outcome_event(event_type="review.decided"),
            "project-1",
            NormalizationRefusalReason.UNSUPPORTED_EVENT,
        ),
        (
            _outcome_event(project_id=None),
            "project-1",
            NormalizationRefusalReason.MISSING_PROJECT,
        ),
        (
            _outcome_event(),
            "project-2",
            NormalizationRefusalReason.PROJECT_MISMATCH,
        ),
        (
            _outcome_event(payload={**_outcome_event().payload, "prompt": "nope"}),
            "project-1",
            NormalizationRefusalReason.UNKNOWN_FIELD,
        ),
        (
            _outcome_event(redaction=RedactionV1(count=1, kinds=("secret_key",))),
            "project-1",
            NormalizationRefusalReason.REDACTION_REQUIRED,
        ),
        (
            _outcome_event(
                payload={**_outcome_event().payload, "verified_outcome": 1}
            ),
            "project-1",
            NormalizationRefusalReason.INVALID_OUTCOME,
        ),
        (
            _outcome_event(
                payload={**_outcome_event().payload, "terminal_event_ids": []}
            ),
            "project-1",
            NormalizationRefusalReason.INVALID_OUTCOME_EVIDENCE,
        ),
    ],
)
def test_separate_outcome_event_refuses_ambiguous_or_unsafe_links(
    event: EventEnvelopeV1,
    expected_project: str,
    reason: NormalizationRefusalReason,
) -> None:
    result = normalize_verified_decision_outcome_event(
        event,
        expected_project_id=expected_project,
    )
    assert isinstance(result, NormalizationRefusalV1)
    assert result.reason is reason


@pytest.mark.parametrize(
    ("event", "source", "expected_project", "reason"),
    [
        (
            _event(event_type="gate.completed"),
            _source(),
            "project-1",
            NormalizationRefusalReason.UNSUPPORTED_EVENT,
        ),
        (
            _event(project_id=None),
            _source(),
            "project-1",
            NormalizationRefusalReason.MISSING_PROJECT,
        ),
        (
            _event(),
            _source(project_id="project-2"),
            "project-1",
            NormalizationRefusalReason.PROJECT_MISMATCH,
        ),
    ],
)
def test_normalize_refuses_ineligible_or_cross_project_sources(
    event: EventEnvelopeV1,
    source: VerifiedDecisionSourceV1,
    expected_project: str,
    reason: NormalizationRefusalReason,
) -> None:
    result = _normalize(
        event, source=source, expected_project_id=expected_project
    )
    assert isinstance(result, NormalizationRefusalV1)
    assert result.reason is reason


def test_allowlist_rejects_unknown_payload_before_persisting_its_value() -> None:
    secret = "Bearer tenant-secret-value"
    payload = _payload()
    payload["prompt"] = secret

    result = _normalize(_event(payload=payload))

    assert isinstance(result, NormalizationRefusalV1)
    assert result.reason is NormalizationRefusalReason.UNKNOWN_FIELD
    assert secret not in canonical_decision_json(result)
    assert "prompt" not in canonical_decision_json(result)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda payload: dict(payload, policy_digest=None),
            NormalizationRefusalReason.MISSING_POLICY_DIGEST,
        ),
        (
            lambda payload: dict(payload, policy_digest="not-a-digest"),
            NormalizationRefusalReason.MISSING_POLICY_DIGEST,
        ),
        (
            lambda payload: dict(payload, decision="model-supplied-action"),
            NormalizationRefusalReason.INVALID_DECISION,
        ),
        (
            lambda payload: {
                **payload,
                "features": {**dict(payload["features"]), "hostname": "private.local"},
            },
            NormalizationRefusalReason.INVALID_FEATURE,
        ),
        (
            lambda payload: {
                **payload,
                "features": {**dict(payload["features"]), "risk_band": "high"},
            },
            NormalizationRefusalReason.RISK_NOT_ELIGIBLE,
        ),
        (
            lambda payload: {
                **payload,
                "features": {**dict(payload["features"]), "retry_count": -1},
            },
            NormalizationRefusalReason.INVALID_FEATURE,
        ),
        (
            lambda payload: {**payload, "features": None},
            NormalizationRefusalReason.INVALID_FEATURE,
        ),
        (
            lambda payload: {
                **payload,
                "features": {
                    "operation_class": "review",
                },
            },
            NormalizationRefusalReason.INVALID_FEATURE,
        ),
        (
            lambda payload: {
                **payload,
                "features": {**dict(payload["features"]), "queue": "secret-queue"},
            },
            NormalizationRefusalReason.INVALID_FEATURE,
        ),
        (
            lambda payload: {
                **payload,
                "features": {
                    **dict(payload["features"]),
                    "approval_required": "false",
                },
            },
            NormalizationRefusalReason.INVALID_FEATURE,
        ),
        (
            lambda payload: {
                **payload,
                "features": {
                    **dict(payload["features"]),
                    "work_type": "token=tenant-secret-value",
                },
            },
            NormalizationRefusalReason.REDACTION_REQUIRED,
        ),
        (
            lambda payload: {
                **payload,
                "outcome_evidence": {
                    **dict(payload["outcome_evidence"]),
                    "decision_event_digest": SHA_C,
                },
            },
            NormalizationRefusalReason.INVALID_OUTCOME_EVIDENCE,
        ),
        (
            lambda payload: {**payload, "verified_outcome": 1},
            NormalizationRefusalReason.INVALID_OUTCOME,
        ),
        (
            lambda payload: {**payload, "verified_outcome": "unverified"},
            NormalizationRefusalReason.INVALID_OUTCOME,
        ),
        (
            lambda payload: {**payload, "outcome_evidence": None},
            NormalizationRefusalReason.INVALID_OUTCOME_EVIDENCE,
        ),
        (
            lambda payload: {**payload, "outcome_evidence": {}},
            NormalizationRefusalReason.INVALID_OUTCOME_EVIDENCE,
        ),
    ],
)
def test_normalize_returns_typed_content_free_refusals(
    mutate: Any, reason: NormalizationRefusalReason
) -> None:
    result = _normalize(_event(payload=mutate(_payload())))
    assert isinstance(result, NormalizationRefusalV1)
    assert result.reason is reason
    serialized = canonical_decision_json(result)
    assert "tenant-secret-value" not in serialized
    assert set(result.model_dump()) == {"schema", "reason", "decision_kind"}


def test_source_redaction_uses_only_bounded_known_categories() -> None:
    accepted = _normalize(
        _event(redaction=RedactionV1(count=2, kinds=("secret_key",)))
    )
    assert isinstance(accepted, DecisionEnvelopeV1)
    assert accepted.redaction.count == 2
    assert accepted.redaction.kinds == ("secret_key",)

    refused = _normalize(
        _event(redaction=RedactionV1(count=1, kinds=("model-chosen-category",)))
    )
    assert isinstance(refused, NormalizationRefusalV1)
    assert refused.reason is NormalizationRefusalReason.UNSUPPORTED_REDACTION


def test_missing_optional_features_are_explicit_not_false() -> None:
    payload = _payload()
    payload["features"] = {
        "risk_band": "medium",
        "operation_class": "review",
    }

    result = _normalize(_event(payload=payload))

    assert isinstance(result, DecisionEnvelopeV1)
    assert result.features["queue"] == "missing"
    assert result.features["approval_required"] == "missing"
    assert result.features["retry_bucket"] == "missing"


@pytest.mark.parametrize(
    ("feature", "value", "output_feature", "expected"),
    [
        ("retry_count", 0, "retry_bucket", "none"),
        ("retry_count", 1, "retry_bucket", "one"),
        ("retry_count", 4, "retry_bucket", "four_plus"),
        ("estimated_cost_microusd", 0, "cost_bucket", "zero"),
        ("estimated_cost_microusd", 1, "cost_bucket", "low"),
        ("estimated_cost_microusd", 100_000_001, "cost_bucket", "high"),
        ("latency_ms", 0, "latency_bucket", "subsecond"),
        ("latency_ms", 1_000, "latency_bucket", "seconds"),
        ("latency_ms", 3_600_000, "latency_bucket", "hours"),
    ],
)
def test_numeric_features_use_closed_named_buckets(
    feature: str,
    value: int,
    output_feature: str,
    expected: str,
) -> None:
    payload = _payload()
    features = cast(dict[str, object], payload["features"])
    features[feature] = value

    result = _normalize(_event(payload=payload))

    assert isinstance(result, DecisionEnvelopeV1)
    assert result.features[output_feature] == expected


def test_bounds_and_incoherent_source_redaction_fail_closed() -> None:
    payload = _payload()
    features = cast(dict[str, object], payload["features"])
    features["work_type"] = "x" * 300
    bounded = _normalize(_event(payload=payload))
    assert isinstance(bounded, NormalizationRefusalV1)
    assert bounded.reason is NormalizationRefusalReason.BOUNDS_EXCEEDED

    event = _event()
    event = event.model_copy(
        update={
            "redaction": RedactionV1.model_construct(
                count=0,
                kinds=("secret_key",),
            )
        }
    )
    incoherent = _normalize(event)
    assert isinstance(incoherent, NormalizationRefusalV1)
    assert incoherent.reason is NormalizationRefusalReason.UNSUPPORTED_REDACTION


def test_false_required_evidence_is_an_explicit_missing_category() -> None:
    payload = _payload()
    features = cast(dict[str, object], payload["features"])
    features["required_evidence"] = False

    result = _normalize(_event(payload=payload))

    assert isinstance(result, DecisionEnvelopeV1)
    assert result.features["required_evidence"] == "missing"
