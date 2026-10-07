"""Tests for deterministic exact runtime lookup and typed abstention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.rollout import (
    AtomicGenerationStore,
    RolloutController,
    RolloutError,
)
from general_ludd.decision_codification.runtime import (
    CodifiedDecision,
    DecisionRuntime,
    RuleBundleAdapter,
    SafetyCheck,
)
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionAbstentionV1,
    DecisionContextV1,
    DecisionKind,
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    DecisionRuleNodeV1,
    FallbackReason,
    NormalizationRefusalReason,
    NormalizationRefusalV1,
    OutcomeCountsV1,
    RolloutStage,
    VerifiedOutcome,
)

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
SHA_E = "sha256:" + "e" * 64
SHA_F = "sha256:" + "f" * 64
NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def _bundle(
    *,
    expires_at: datetime | None = None,
    feature_id: str = "work_type",
    feature_value: str | bool = "code",
) -> DecisionRuleBundleV1:
    return DecisionRuleBundleV1.create(
        schema="gludd.decision-rule-bundle/v1",
        project_id="project-1",
        decision_kind="review",
        feature_schema=SHA_D,
        policy_compatibility=(SHA_E,),
        risk_scope="low",
        root_id="node",
        default_leaf_id="abstain",
        nodes=(
            DecisionRuleNodeV1(
                node_id="node",
                feature_id=feature_id,
                operator="eq",
                value=feature_value,
                match_id="approve",
                miss_id="abstain",
            ),
        ),
        leaves=(
            DecisionRuleLeafV1(
                leaf_id="abstain",
                decision=None,
                support=0,
                confidence=0.0,
                outcome_counts=OutcomeCountsV1(
                    success=0, failure=0, reverted=0, unknown=0, unsafe=0
                ),
                abstain=True,
            ),
            DecisionRuleLeafV1(
                leaf_id="approve",
                decision="approve",
                support=20,
                confidence=1.0,
                outcome_counts=OutcomeCountsV1(
                    success=20, failure=0, reverted=0, unknown=0, unsafe=0
                ),
                abstain=False,
            ),
        ),
        corpus_digest=SHA_A,
        training_recipe_digest=SHA_B,
        dependency_lock_digest=SHA_C,
        observed_context_digests=(_context().context_signature,),
        created_at=NOW - timedelta(days=1),
        expires_at=expires_at or NOW + timedelta(days=90),
        maximum_use_count=2,
    )


def _receipt(
    bundle: DecisionRuleBundleV1,
    *,
    previous: str | None = None,
    state: str = "shadow",
    receipt_type: str = "approval",
    expires_at: datetime | None = None,
) -> ApprovalReceiptV1:
    return ApprovalReceiptV1.create(
        schema="gludd.decision-approval-receipt/v1",
        receipt_type=receipt_type,
        lifecycle_state=state,
        previous_receipt_digest=previous,
        candidate_digest=bundle.candidate_digest,
        corpus_digest=bundle.corpus_digest,
        evaluator_report_digest=SHA_F,
        feature_schema=bundle.feature_schema,
        policy_digest=SHA_E,
        source_code_digest=SHA_B,
        dependency_lock_digest=bundle.dependency_lock_digest,
        training_recipe_digest=bundle.training_recipe_digest,
        project_id=bundle.project_id,
        decision_kind=bundle.decision_kind,
        approver_identity_hmac="hmac-sha256:" + "1" * 64,
        authorization_evidence_digest=SHA_A,
        created_at=NOW - timedelta(hours=1),
        expires_at=expires_at or NOW + timedelta(days=30),
        risk_class="low",
        rollout_plan=("shadow", "active"),
        maximum_use_count=2,
    )


def _context(*, work_type: str = "code") -> DecisionContextV1:
    return DecisionContextV1.create(
        schema="gludd.decision-context/v1",
        project_id="project-1",
        decision_kind="review",
        feature_schema=SHA_D,
        policy_digest=SHA_E,
        exact_guards={
            "action_vocabulary": "review.v1",
            "operation_class": "review_change",
            "risk_band": "low",
        },
        features={"risk_band": "low", "work_type": work_type},
    )


def _runtime(
    tmp_path: Path,
    *,
    active: bool = True,
    bundle: DecisionRuleBundleV1 | None = None,
    safety_check: SafetyCheck | None = None,
    rule_adapter: RuleBundleAdapter | None = None,
) -> tuple[DecisionRuntime, RolloutController, DecisionRuleBundleV1]:
    selected = bundle or _bundle()
    artifacts = DecisionArtifactStore(str(tmp_path), key=b"runtime-artifact-key")
    approval = _receipt(selected)
    active_receipt = _receipt(
        selected,
        previous=approval.receipt_digest,
        state="active",
        receipt_type="promotion",
    )
    artifacts.create_rule_bundle(selected)
    artifacts.append_receipt(approval)
    artifacts.append_receipt(active_receipt)
    controller = RolloutController(
        artifacts,
        AtomicGenerationStore(),
        rollout_key=b"runtime-rollout-key",
    )
    controller.install(selected, approval, expected_candidate_digest=None)
    if active:
        controller.promote(
            active_receipt,
            expected_candidate_digest=selected.candidate_digest,
        )
    return (
        DecisionRuntime(
            artifacts,
            controller,
            safety_check=safety_check,
            rule_adapter=rule_adapter,
        ),
        controller,
        selected,
    )


def _lookup(
    runtime: DecisionRuntime,
    value: DecisionContextV1 | NormalizationRefusalV1,
    *,
    project_id: str = "project-1",
    policy_digest: str = SHA_E,
    correlation_id: str = "correlation-1",
) -> CodifiedDecision | DecisionAbstentionV1:
    return runtime.lookup(
        project_id=project_id,
        decision_kind=DecisionKind.REVIEW,
        normalized=value,
        correlation_id=correlation_id,
        policy_digest=policy_digest,
        now=NOW,
        side_effect_id="effect-1",
    )


def test_exact_lookup_is_rules_engine_backed_and_idempotent(tmp_path: Path) -> None:
    runtime, controller, bundle = _runtime(tmp_path)
    context = _context()

    first = _lookup(runtime, context)
    second = _lookup(runtime, context)

    assert isinstance(first, CodifiedDecision)
    assert first == second
    assert first.decision == "approve"
    assert first.leaf_id == "approve"
    assert first.context_id == context.context_id
    assert first.candidate_digest == bundle.candidate_digest
    assert controller.use_count(bundle.candidate_digest) == 1


def test_reserved_known_guard_is_injected_only_after_scope_validation(
    tmp_path: Path,
) -> None:
    bundle = _bundle(feature_id="codification_known", feature_value=True)
    runtime, _, _ = _runtime(tmp_path, bundle=bundle)

    result = _lookup(runtime, _context())

    assert isinstance(result, CodifiedDecision)
    assert result.decision == "approve"


@pytest.mark.parametrize(
    ("value", "project_id", "policy_digest", "reason"),
    [
        (
            NormalizationRefusalV1(
                reason=NormalizationRefusalReason.INVALID_FEATURE,
                decision_kind=DecisionKind.REVIEW,
            ),
            "project-1",
            SHA_E,
            FallbackReason.NORMALIZATION_REFUSED,
        ),
        (_context(), "project-2", SHA_E, FallbackReason.NO_ACTIVE_RULE),
        (_context(), "project-1", SHA_F, FallbackReason.POLICY_CHANGED),
        (_context(work_type="docs"), "project-1", SHA_E, FallbackReason.SCOPE_MISS),
    ],
)
def test_lookup_returns_closed_typed_abstentions(
    tmp_path: Path,
    value: DecisionContextV1 | NormalizationRefusalV1,
    project_id: str,
    policy_digest: str,
    reason: FallbackReason,
) -> None:
    runtime, _, _ = _runtime(tmp_path)

    result = _lookup(
        runtime,
        value,
        project_id=project_id,
        policy_digest=policy_digest,
    )

    assert isinstance(result, DecisionAbstentionV1)
    assert result.reason is reason


def test_shadow_expiry_revocation_and_drift_hold_never_execute(tmp_path: Path) -> None:
    shadow, _, _ = _runtime(tmp_path / "shadow", active=False)
    result = _lookup(shadow, _context())
    assert isinstance(result, DecisionAbstentionV1)
    assert result.reason is FallbackReason.CANARY_EXCLUDED

    expired_bundle = _bundle(expires_at=NOW - timedelta(seconds=1))
    expired, _, _ = _runtime(tmp_path / "expired", bundle=expired_bundle)
    result = _lookup(expired, _context())
    assert isinstance(result, DecisionAbstentionV1)
    assert result.reason is FallbackReason.EXPIRED

    held, controller, bundle = _runtime(tmp_path / "held")
    controller.mark_drift_hold("project-1", DecisionKind.REVIEW, "failure_rate")
    result = _lookup(held, _context())
    assert isinstance(result, DecisionAbstentionV1)
    assert result.reason is FallbackReason.DRIFT_HOLD

    revoked, controller, bundle = _runtime(tmp_path / "revoked")
    controller.force_revoke_for_integrity(bundle.candidate_digest)
    result = _lookup(revoked, _context())
    assert isinstance(result, DecisionAbstentionV1)
    assert result.reason is FallbackReason.REVOKED

    revoked, controller, bundle = _runtime(tmp_path / "receipt-revoked")
    pointer = controller.current("project-1", DecisionKind.REVIEW)
    assert pointer is not None
    revocation = _receipt(
        bundle,
        previous=pointer.receipt_digest,
        state="revoked",
        receipt_type="revocation",
    )
    controller.artifacts.append_receipt(revocation)
    assert controller.revoke(
        revocation, expected_candidate_digest=bundle.candidate_digest
    )
    result = _lookup(revoked, _context())
    assert isinstance(result, DecisionAbstentionV1)
    assert result.reason is FallbackReason.REVOKED


def test_ambiguity_policy_refusal_and_internal_error_fail_closed(
    tmp_path: Path,
) -> None:
    class _Ambiguous:
        def matching_leaf_ids(
            self, bundle: DecisionRuleBundleV1, context: dict[str, object]
        ) -> tuple[str, ...]:
            return ("approve", "abstain")

    ambiguous, controller, bundle = _runtime(
        tmp_path / "ambiguous", rule_adapter=_Ambiguous()
    )
    result = _lookup(ambiguous, _context())
    assert isinstance(result, DecisionAbstentionV1)
    assert result.reason is FallbackReason.MULTIPLE_LEAVES
    assert controller.is_drift_held(bundle.candidate_digest)

    refused, _, _ = _runtime(
        tmp_path / "refused", safety_check=lambda decision, context: False
    )
    result = _lookup(refused, _context())
    assert isinstance(result, DecisionAbstentionV1)
    assert result.reason is FallbackReason.POLICY_CHANGED

    def _raise(decision: str, context: DecisionContextV1) -> bool:
        raise RuntimeError("must not escape")

    broken, _, _ = _runtime(tmp_path / "broken", safety_check=_raise)
    result = _lookup(broken, _context())
    assert isinstance(result, DecisionAbstentionV1)
    assert result.reason is FallbackReason.RUNTIME_ERROR


def test_use_limit_expires_generation_without_double_counting_retries(
    tmp_path: Path,
) -> None:
    runtime, controller, bundle = _runtime(tmp_path)

    assert isinstance(_lookup(runtime, _context()), CodifiedDecision)
    assert isinstance(
        _lookup(runtime, _context(), correlation_id="correlation-2"),
        CodifiedDecision,
    )
    third = _lookup(runtime, _context(), correlation_id="correlation-3")

    assert isinstance(third, DecisionAbstentionV1)
    assert third.reason is FallbackReason.EXPIRED
    assert controller.use_count(bundle.candidate_digest) == 2


def test_application_outcome_is_scope_checked_recorded_and_idempotent(
    tmp_path: Path,
) -> None:
    runtime, _, bundle = _runtime(tmp_path)
    decision = _lookup(runtime, _context())
    assert isinstance(decision, CodifiedDecision)

    feedback = runtime.record_application_outcome(
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        candidate_digest=bundle.candidate_digest,
        application_id=decision.application_id,
        rollout_stage=RolloutStage.ACTIVE,
        outcome=VerifiedOutcome.SUCCESS,
        occurred_at=NOW,
        terminal_event_id="terminal-1",
        evidence_digest=SHA_A,
    )
    duplicate = runtime.record_application_outcome(
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        candidate_digest=bundle.candidate_digest,
        application_id=decision.application_id,
        rollout_stage=RolloutStage.ACTIVE,
        outcome=VerifiedOutcome.SUCCESS,
        occurred_at=NOW,
        terminal_event_id="terminal-1",
        evidence_digest=SHA_A,
    )

    assert feedback.recorded is True
    assert feedback.drift_reason is None
    assert duplicate.recorded is False
    with pytest.raises(RolloutError, match="scope does not match"):
        runtime.record_application_outcome(
            project_id="project-2",
            decision_kind=DecisionKind.REVIEW,
            candidate_digest=bundle.candidate_digest,
            application_id=decision.application_id,
            rollout_stage=RolloutStage.ACTIVE,
            outcome=VerifiedOutcome.SUCCESS,
            occurred_at=NOW,
            terminal_event_id="terminal-1",
            evidence_digest=SHA_A,
        )
