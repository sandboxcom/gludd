"""Contract tests for deterministic decision-codification wire schemas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from general_ludd.decision_codification.schema import (
    DECISION_ACTIONS_V1,
    MAX_ENVELOPE_BYTES,
    ApprovalReceiptV1,
    DecisionAbstentionV1,
    DecisionEnvelopeV1,
    DecisionKind,
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    DecisionRuleNodeV1,
    EvaluationReportV1,
    FallbackReason,
    LeafEvaluationV1,
    LifecycleState,
    NormalizationRefusalReason,
    NormalizationRefusalV1,
    OutcomeCountsV1,
    OutcomeEvidenceV1,
    ReceiptType,
    RedactionSummaryV1,
    RolloutStage,
    VerifiedDecisionSourceV1,
    VerifiedOutcome,
    canonical_decision_json,
    canonical_sha256,
    decode_decision_json_object,
    parse_approval_receipt,
    parse_decision_envelope,
    parse_evaluation_report,
    parse_rule_bundle,
)

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
SHA_E = "sha256:" + "e" * 64
SHA_F = "sha256:" + "f" * 64
HMAC_A = "hmac-sha256:" + "a" * 64
NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def _outcome(
    *, outcome: VerifiedOutcome = VerifiedOutcome.SUCCESS
) -> OutcomeEvidenceV1:
    return OutcomeEvidenceV1(
        decision_event_digest=SHA_A,
        outcome=outcome,
        terminal_event_ids=("terminal-1",),
        gate_digests=(SHA_B,),
        status_digests=(),
    )


def _envelope() -> DecisionEnvelopeV1:
    return DecisionEnvelopeV1.create(
        schema="gludd.decision-envelope/v1",
        source_run_id="run-1",
        source_event_digest=SHA_A,
        source_bundle_digest=SHA_C,
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        feature_schema=SHA_D,
        policy_digest=SHA_E,
        occurred_at=NOW,
        exact_guards={
            "risk_band": "low",
            "operation_class": "review",
            "action_vocabulary": "review.v1",
        },
        features={"risk_band": "low", "work_type": "code"},
        decision="approve",
        verified_outcome="success",
        outcome_evidence=_outcome(),
        redaction=RedactionSummaryV1(count=0, kinds=()),
    )


def _rule_bundle() -> DecisionRuleBundleV1:
    return DecisionRuleBundleV1.create(
        schema="gludd.decision-rule-bundle/v1",
        project_id="project-1",
        decision_kind="review",
        feature_schema=SHA_D,
        policy_compatibility=(SHA_E,),
        risk_scope="low",
        root_id="node-1",
        default_leaf_id="leaf-abstain",
        nodes=(
            DecisionRuleNodeV1(
                node_id="node-1",
                feature_id="risk_band",
                operator="eq",
                value="low",
                match_id="leaf-approve",
                miss_id="leaf-abstain",
            ),
        ),
        leaves=(
            DecisionRuleLeafV1(
                leaf_id="leaf-abstain",
                decision=None,
                support=0,
                confidence=0.0,
                outcome_counts=OutcomeCountsV1(
                    success=0, failure=0, reverted=0, unknown=0, unsafe=0
                ),
                abstain=True,
            ),
            DecisionRuleLeafV1(
                leaf_id="leaf-approve",
                decision="approve",
                support=16,
                confidence=0.99,
                outcome_counts=OutcomeCountsV1(
                    success=16, failure=0, reverted=0, unknown=0, unsafe=0
                ),
                abstain=False,
            ),
        ),
        corpus_digest=SHA_A,
        training_recipe_digest=SHA_B,
        dependency_lock_digest=SHA_C,
        evaluator_report_digest=SHA_F,
        created_at=NOW,
        expires_at=NOW + timedelta(days=90),
        maximum_use_count=10_000,
    )


def _report() -> EvaluationReportV1:
    return EvaluationReportV1.create(
        schema="gludd.decision-evaluation-report/v1",
        candidate_digest=_rule_bundle().candidate_digest,
        corpus_digest=SHA_A,
        project_id="project-1",
        decision_kind="review",
        created_at=NOW,
        support_count=20,
        root_task_count=8,
        utc_day_count=3,
        source_agent_count=2,
        exact_match_count=18,
        abstention_count=2,
        precision=1.0,
        leaf_results=(
            LeafEvaluationV1(
                leaf_id="leaf-approve", support=18, confidence=1.0
            ),
        ),
        false_automation_count=0,
        conflict_count=0,
        unknown_feature_count=0,
        policy_mismatch_count=0,
        verified_failure_count=0,
        rollback_count=0,
        safety_violation_count=0,
        estimated_agent_calls_avoided=18,
        estimated_tokens_avoided=2_000,
        historical_policy_digest=SHA_E,
        current_policy_digest=SHA_E,
        deterministic_replay_runs=2,
    )


def _receipt() -> ApprovalReceiptV1:
    report = _report()
    return ApprovalReceiptV1.create(
        schema="gludd.decision-approval-receipt/v1",
        receipt_type="approval",
        lifecycle_state="shadow",
        previous_receipt_digest=None,
        candidate_digest=_rule_bundle().candidate_digest,
        corpus_digest=SHA_A,
        evaluator_report_digest=report.report_digest,
        feature_schema=SHA_D,
        policy_digest=SHA_E,
        source_code_digest=SHA_B,
        dependency_lock_digest=SHA_C,
        training_recipe_digest=SHA_F,
        project_id="project-1",
        decision_kind="review",
        approver_identity_hmac=HMAC_A,
        authorization_evidence_digest=SHA_A,
        created_at=NOW,
        expires_at=NOW + timedelta(days=30),
        risk_class="low",
        rollout_plan=("shadow", "canary", "active"),
        maximum_use_count=1_000,
    )


def test_envelope_is_frozen_canonical_digest_bound_and_strict() -> None:
    envelope = _envelope()

    unsigned = envelope.model_dump(
        mode="json", by_alias=True, exclude={"envelope_id"}
    )
    assert envelope.envelope_id == canonical_sha256(unsigned)
    assert list(envelope.exact_guards) == sorted(envelope.exact_guards)
    assert list(envelope.features) == sorted(envelope.features)
    assert len(canonical_decision_json(envelope).encode()) <= MAX_ENVELOPE_BYTES

    with pytest.raises(ValidationError, match="frozen"):
        envelope.decision = "reject"

    payload = envelope.model_dump(mode="python", by_alias=True)
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DecisionEnvelopeV1.model_validate(payload)

    payload.pop("unexpected")
    payload["envelope_id"] = SHA_F
    with pytest.raises(ValidationError, match="canonical digest"):
        DecisionEnvelopeV1.model_validate(payload)


def test_envelope_round_trip_rejects_duplicate_keys_and_non_finite_numbers() -> None:
    envelope = _envelope()
    assert parse_decision_envelope(canonical_decision_json(envelope)) == envelope

    with pytest.raises(ValueError, match="duplicate key"):
        decode_decision_json_object('{"schema":"one","schema":"two"}')

    with pytest.raises(ValueError, match="finite"):
        decode_decision_json_object('{"precision":NaN}')


def test_outcome_evidence_must_be_content_free_bound_and_terminal() -> None:
    with pytest.raises(ValidationError, match="terminal evidence"):
        OutcomeEvidenceV1(
            decision_event_digest=SHA_A,
            outcome=VerifiedOutcome.SUCCESS,
            terminal_event_ids=(),
            gate_digests=(),
            status_digests=(),
        )

    envelope = _envelope()
    payload = envelope.model_dump(mode="python", by_alias=True)
    payload["outcome_evidence"]["decision_event_digest"] = SHA_B
    payload["envelope_id"] = SHA_F
    with pytest.raises(ValidationError, match="source event"):
        DecisionEnvelopeV1.model_validate(payload, context={"skip_digest_check": True})


def test_rule_bundle_requires_bounded_tree_and_abstaining_default() -> None:
    bundle = _rule_bundle()
    unsigned = bundle.model_dump(
        mode="json", by_alias=True, exclude={"candidate_digest"}
    )
    assert bundle.candidate_digest == canonical_sha256(unsigned)
    assert bundle.default_leaf_id == "leaf-abstain"

    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["leaves"][0]["abstain"] = False
    bad["leaves"][0]["decision"] = "approve"
    with pytest.raises(ValidationError, match="default leaf must abstain"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["nodes"][0]["match_id"] = "missing-leaf"
    with pytest.raises(ValidationError, match="unknown child"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )


def test_report_receipt_and_abstention_are_digest_bound_typed_contracts() -> None:
    report = _report()
    assert report.report_digest == canonical_sha256(
        report.model_dump(mode="json", by_alias=True, exclude={"report_digest"})
    )

    with pytest.raises(ValidationError):
        EvaluationReportV1.create(
            **{
                **report.model_dump(
                    mode="python", by_alias=True, exclude={"report_digest"}
                ),
                "precision": float("nan"),
            }
        )

    receipt = _receipt()
    assert receipt.receipt_digest == canonical_sha256(
        receipt.model_dump(mode="json", by_alias=True, exclude={"receipt_digest"})
    )

    abstention = DecisionAbstentionV1(
        reason=FallbackReason.NORMALIZATION_REFUSED,
        normalization_reason=NormalizationRefusalReason.INVALID_FEATURE,
        envelope_id=None,
        candidate_digest=None,
    )
    assert abstention.reason is FallbackReason.NORMALIZATION_REFUSED
    with pytest.raises(ValidationError, match="normalization_reason"):
        DecisionAbstentionV1(
            reason=FallbackReason.NO_ACTIVE_RULE,
            normalization_reason=NormalizationRefusalReason.INVALID_FEATURE,
        )

    refusal = NormalizationRefusalV1(
        reason=NormalizationRefusalReason.UNKNOWN_FIELD,
        decision_kind=DecisionKind.REVIEW,
    )
    assert "detail" not in refusal.model_dump()


def test_verified_source_requires_signed_complete_project_scoped_evidence() -> None:
    source = VerifiedDecisionSourceV1(
        source_run_id="run-1",
        source_bundle_digest=SHA_A,
        project_id="project-1",
        integrity="signed",
        complete=True,
    )
    assert source.complete is True

    with pytest.raises(ValidationError):
        VerifiedDecisionSourceV1(
            source_run_id="run-1",
            source_bundle_digest=SHA_A,
            project_id="project-1",
            integrity="unsigned",  # type: ignore[arg-type]
            complete=True,
        )

    with pytest.raises(TypeError):
        DECISION_ACTIONS_V1[DecisionKind.REVIEW] = frozenset({"mutated"})  # type: ignore[index]


def test_envelope_rejects_unsafe_evidence_actions_and_oversize_payloads() -> None:
    envelope = _envelope()

    bad = envelope.model_dump(mode="python", by_alias=True)
    bad["verified_outcome"] = "failure"
    with pytest.raises(ValidationError, match="does not match verified_outcome"):
        DecisionEnvelopeV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = envelope.model_dump(mode="python", by_alias=True)
    bad["decision"] = "route_remote"
    with pytest.raises(ValidationError, match="action vocabulary"):
        DecisionEnvelopeV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = envelope.model_dump(
        mode="python", by_alias=True, exclude={"envelope_id"}
    )
    bad["features"] = {
        f"f{index:02d}" + ("x" * 125): "v" * 128 for index in range(64)
    }
    with pytest.raises(ValidationError, match="exceeds 16384 bytes"):
        DecisionEnvelopeV1.create(**bad)


def test_timestamps_redaction_and_outcome_sets_are_strict() -> None:
    bad = _envelope().model_dump(mode="python", by_alias=True)
    bad["occurred_at"] = datetime(2026, 10, 5, 12)
    with pytest.raises(ValidationError, match="timezone-aware"):
        DecisionEnvelopeV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    with pytest.raises(ValidationError, match="redaction kinds must be unique"):
        RedactionSummaryV1(count=2, kinds=("secret_key", "secret_key"))
    with pytest.raises(ValidationError, match="exactly when count is non-zero"):
        RedactionSummaryV1(count=0, kinds=("secret_key",))

    with pytest.raises(ValidationError, match="unknown outcomes"):
        OutcomeEvidenceV1(
            decision_event_digest=SHA_A,
            outcome=VerifiedOutcome.UNKNOWN,
            terminal_event_ids=("terminal-1",),
            gate_digests=(SHA_B,),
            status_digests=(),
        )
    unknown = OutcomeEvidenceV1(
        decision_event_digest=SHA_A,
        outcome="unknown",  # type: ignore[arg-type]
        terminal_event_ids=(),
        gate_digests=(),
        status_digests=(),
    )
    assert unknown.outcome is VerifiedOutcome.UNKNOWN
    with pytest.raises(ValidationError, match="must be unique"):
        OutcomeEvidenceV1(
            decision_event_digest=SHA_A,
            outcome=VerifiedOutcome.SUCCESS,
            terminal_event_ids=("terminal-1", "terminal-1"),
            gate_digests=(SHA_B,),
            status_digests=(),
        )


def test_rule_leaf_and_graph_invariants_fail_closed() -> None:
    with pytest.raises(ValidationError, match="children must be distinct"):
        DecisionRuleNodeV1(
            node_id="node",
            feature_id="risk_band",
            operator="eq",
            value="low",
            match_id="leaf",
            miss_id="leaf",
        )
    with pytest.raises(ValidationError, match="must omit decision"):
        DecisionRuleLeafV1(
            leaf_id="leaf",
            decision="approve",
            support=0,
            confidence=0.0,
            outcome_counts=OutcomeCountsV1(
                success=0, failure=0, reverted=0, unknown=0, unsafe=0
            ),
            abstain=True,
        )
    with pytest.raises(ValidationError, match="must equal support"):
        DecisionRuleLeafV1(
            leaf_id="leaf",
            decision="approve",
            support=1,
            confidence=1.0,
            outcome_counts=OutcomeCountsV1(
                success=0, failure=0, reverted=0, unknown=0, unsafe=0
            ),
            abstain=False,
        )

    bundle = _rule_bundle()
    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["expires_at"] = NOW
    with pytest.raises(ValidationError, match="expiry must follow"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["nodes"] = (bad["nodes"][0], bad["nodes"][0])
    with pytest.raises(ValidationError, match="IDs must be unique"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["leaves"][0]["leaf_id"] = "node-1"
    with pytest.raises(ValidationError, match="must not overlap"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["root_id"] = "missing-root"
    with pytest.raises(ValidationError, match="root must reference"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["nodes"][0]["match_id"] = "node-1"
    with pytest.raises(ValidationError, match="must not contain cycles"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = bundle.model_dump(mode="python", by_alias=True)
    extra_leaf = DecisionRuleLeafV1(
        leaf_id="leaf-unreachable",
        decision=None,
        support=0,
        confidence=0.0,
        outcome_counts=OutcomeCountsV1(
            success=0, failure=0, reverted=0, unknown=0, unsafe=0
        ),
        abstain=True,
    )
    bad["leaves"] = (*bad["leaves"], extra_leaf)
    with pytest.raises(ValidationError, match="unreachable"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["leaves"][1]["decision"] = None
    bad["leaves"][1]["abstain"] = True
    with pytest.raises(ValidationError, match="non-abstaining"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["leaves"][1]["decision"] = "route_remote"
    with pytest.raises(ValidationError, match="action vocabulary"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )

    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["policy_compatibility"] = (SHA_E, SHA_E)
    with pytest.raises(ValidationError, match="must be unique"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )


def test_rule_tree_depth_is_bounded() -> None:
    bundle = _rule_bundle()
    bad = bundle.model_dump(mode="python", by_alias=True)
    bad["nodes"] = tuple(
        DecisionRuleNodeV1(
            node_id=f"node-{index}",
            feature_id="risk_band",
            operator="eq",
            value="low",
            match_id=f"node-{index + 1}" if index < 4 else "leaf-approve",
            miss_id="leaf-abstain",
        )
        for index in range(5)
    )
    bad["root_id"] = "node-0"
    with pytest.raises(ValidationError, match="depth exceeds 4"):
        DecisionRuleBundleV1.model_validate(
            bad, context={"skip_digest_check": True}
        )


def test_report_and_receipt_lifecycle_invariants_and_parsers() -> None:
    report = _report()
    bad_report = report.model_dump(mode="python", by_alias=True)
    bad_report["leaf_results"] = (
        bad_report["leaf_results"][0],
        bad_report["leaf_results"][0],
    )
    with pytest.raises(ValidationError, match="IDs must be unique"):
        EvaluationReportV1.model_validate(
            bad_report, context={"skip_digest_check": True}
        )

    bad_report = report.model_dump(mode="python", by_alias=True)
    bad_report["exact_match_count"] = 21
    with pytest.raises(ValidationError, match="exceed support"):
        EvaluationReportV1.model_validate(
            bad_report, context={"skip_digest_check": True}
        )

    receipt = _receipt()
    bad_receipt = receipt.model_dump(mode="python", by_alias=True)
    bad_receipt["expires_at"] = NOW
    with pytest.raises(ValidationError, match="expiry must follow"):
        ApprovalReceiptV1.model_validate(
            bad_receipt, context={"skip_digest_check": True}
        )

    bad_receipt = receipt.model_dump(mode="python", by_alias=True)
    bad_receipt["previous_receipt_digest"] = SHA_B
    with pytest.raises(ValidationError, match="must not name a previous"):
        ApprovalReceiptV1.model_validate(
            bad_receipt, context={"skip_digest_check": True}
        )

    bad_receipt = receipt.model_dump(mode="python", by_alias=True)
    bad_receipt["receipt_type"] = "promotion"
    bad_receipt["lifecycle_state"] = "canary"
    with pytest.raises(ValidationError, match="require the previous"):
        ApprovalReceiptV1.model_validate(
            bad_receipt, context={"skip_digest_check": True}
        )

    bad_receipt = receipt.model_dump(mode="python", by_alias=True)
    bad_receipt["lifecycle_state"] = "active"
    with pytest.raises(ValidationError, match="incompatible"):
        ApprovalReceiptV1.model_validate(
            bad_receipt, context={"skip_digest_check": True}
        )

    bad_receipt = receipt.model_dump(mode="python", by_alias=True)
    bad_receipt["rollout_plan"] = ("canary", "active")
    with pytest.raises(ValidationError, match="begin in shadow"):
        ApprovalReceiptV1.model_validate(
            bad_receipt, context={"skip_digest_check": True}
        )

    bad_receipt = receipt.model_dump(mode="python", by_alias=True)
    bad_receipt["rollout_plan"] = ("shadow", "active", "canary")
    with pytest.raises(ValidationError, match="unique and monotonic"):
        ApprovalReceiptV1.model_validate(
            bad_receipt, context={"skip_digest_check": True}
        )

    promotion_data = receipt.model_dump(
        mode="python", by_alias=True, exclude={"receipt_digest"}
    )
    promotion_data.update(
        receipt_type=ReceiptType.PROMOTION,
        lifecycle_state=LifecycleState.CANARY,
        previous_receipt_digest=receipt.receipt_digest,
        rollout_plan=(RolloutStage.SHADOW, RolloutStage.CANARY),
    )
    promotion = ApprovalReceiptV1.create(**promotion_data)
    assert promotion.lifecycle_state is LifecycleState.CANARY

    assert parse_rule_bundle(canonical_decision_json(_rule_bundle())) == _rule_bundle()
    assert parse_evaluation_report(canonical_decision_json(report)) == report
    assert parse_approval_receipt(canonical_decision_json(receipt)) == receipt


def test_string_typed_refusal_and_abstention_parsing_is_closed() -> None:
    refusal = NormalizationRefusalV1(
        reason="invalid_feature",  # type: ignore[arg-type]
        decision_kind="review",  # type: ignore[arg-type]
    )
    assert refusal.reason is NormalizationRefusalReason.INVALID_FEATURE
    assert refusal.decision_kind is DecisionKind.REVIEW

    abstention = DecisionAbstentionV1(
        reason="normalization_refused",  # type: ignore[arg-type]
        normalization_reason="invalid_feature",  # type: ignore[arg-type]
    )
    assert abstention.reason is FallbackReason.NORMALIZATION_REFUSED
    with pytest.raises(ValidationError, match="normalization_reason"):
        DecisionAbstentionV1(reason=FallbackReason.NORMALIZATION_REFUSED)
