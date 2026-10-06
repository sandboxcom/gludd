"""Tests for atomic ZDD generation swaps, canaries, holds, and rollback."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.rollout import (
    AtomicGenerationStore,
    GenerationPointer,
    RolloutController,
    RolloutError,
    stable_canary_bucket,
)
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    DecisionRuleNodeV1,
    LifecycleState,
    OutcomeCountsV1,
    ReceiptType,
    RolloutStage,
)

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
SHA_E = "sha256:" + "e" * 64
SHA_F = "sha256:" + "f" * 64
NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def _bundle(*, value: str = "code", policy: str = SHA_E) -> DecisionRuleBundleV1:
    return DecisionRuleBundleV1.create(
        schema="gludd.decision-rule-bundle/v1",
        project_id="project-1",
        decision_kind="review",
        feature_schema=SHA_D,
        policy_compatibility=(policy,),
        risk_scope="low",
        root_id="node",
        default_leaf_id="abstain",
        nodes=(
            DecisionRuleNodeV1(
                node_id="node",
                feature_id="work_type",
                operator="eq",
                value=value,
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
        evaluator_report_digest=SHA_F,
        created_at=NOW,
        expires_at=NOW + timedelta(days=90),
        maximum_use_count=100,
    )


def _receipt(
    bundle: DecisionRuleBundleV1,
    *,
    previous: str | None = None,
    receipt_type: str = "approval",
    state: str = "shadow",
    created_at: datetime = NOW,
    rollout_plan: tuple[str, ...] = (
        "shadow",
        "canary",
        "canary_10",
        "canary_50",
        "active",
    ),
) -> ApprovalReceiptV1:
    return ApprovalReceiptV1.create(
        schema="gludd.decision-approval-receipt/v1",
        receipt_type=receipt_type,
        lifecycle_state=state,
        previous_receipt_digest=previous,
        candidate_digest=bundle.candidate_digest,
        corpus_digest=bundle.corpus_digest,
        evaluator_report_digest=bundle.evaluator_report_digest,
        feature_schema=bundle.feature_schema,
        policy_digest=bundle.policy_compatibility[0],
        source_code_digest=SHA_B,
        dependency_lock_digest=bundle.dependency_lock_digest,
        training_recipe_digest=bundle.training_recipe_digest,
        project_id=bundle.project_id,
        decision_kind=bundle.decision_kind,
        approver_identity_hmac="hmac-sha256:" + "1" * 64,
        authorization_evidence_digest=SHA_A,
        created_at=created_at,
        expires_at=NOW + timedelta(days=30),
        risk_class=bundle.risk_scope,
        rollout_plan=rollout_plan,
        maximum_use_count=100,
    )


def _controller(tmp_path: Path) -> tuple[
    DecisionArtifactStore, RolloutController
]:
    artifacts = DecisionArtifactStore(str(tmp_path), key=b"rollout-artifact-key")
    controller = RolloutController(
        artifacts,
        AtomicGenerationStore(),
        rollout_key=b"project-rollout-key",
    )
    return artifacts, controller


def _persist(
    artifacts: DecisionArtifactStore,
    bundle: DecisionRuleBundleV1,
    *receipts: ApprovalReceiptV1,
) -> None:
    artifacts.create_rule_bundle(bundle)
    for receipt in receipts:
        artifacts.append_receipt(receipt)


def test_hmac_canary_bucket_is_stable_and_scope_separated() -> None:
    first = stable_canary_bucket(
        b"rollout-key", "project-1", "correlation-1", SHA_A
    )

    assert 0 <= first < 10_000
    assert first == stable_canary_bucket(
        b"rollout-key", "project-1", "correlation-1", SHA_A
    )
    assert first != stable_canary_bucket(
        b"rollout-key", "project-2", "correlation-1", SHA_A
    )
    assert first != stable_canary_bucket(
        b"other-key", "project-1", "correlation-1", SHA_A
    )


def test_install_and_promotion_are_atomic_compare_and_swap_operations(
    tmp_path: Path,
) -> None:
    artifacts, controller = _controller(tmp_path)
    bundle = _bundle()
    approval = _receipt(bundle)
    promotion = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="canary",
        created_at=NOW + timedelta(hours=1),
    )
    _persist(artifacts, bundle, approval, promotion)

    shadow = controller.install(bundle, approval, expected_candidate_digest=None)
    captured = controller.current("project-1", bundle.decision_kind)
    canary = controller.promote(
        promotion, expected_candidate_digest=bundle.candidate_digest
    )

    assert shadow is not None
    assert shadow.stage.value == "shadow"
    assert captured == shadow
    assert captured is not canary
    assert canary.stage.value == "canary"
    assert not controller.install(
        bundle, approval, expected_candidate_digest=None
    )


def test_stage_cohorts_holds_and_revocation_fail_closed(tmp_path: Path) -> None:
    artifacts, controller = _controller(tmp_path)
    bundle = _bundle()
    approval = _receipt(bundle, rollout_plan=("shadow", "active"))
    promotion = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="active",
        created_at=NOW + timedelta(hours=1),
        rollout_plan=("shadow", "active"),
    )
    revocation = _receipt(
        bundle,
        previous=promotion.receipt_digest,
        receipt_type="revocation",
        state="revoked",
        created_at=NOW + timedelta(hours=2),
        rollout_plan=("shadow", "active"),
    )
    _persist(artifacts, bundle, approval, promotion, revocation)
    controller.install(bundle, approval, expected_candidate_digest=None)

    assert not controller.selected_for_execution(
        controller.current("project-1", bundle.decision_kind), "correlation"
    )
    controller.promote(promotion, expected_candidate_digest=bundle.candidate_digest)
    pointer = controller.current("project-1", bundle.decision_kind)
    assert controller.selected_for_execution(pointer, "correlation")

    assert controller.mark_drift_hold(
        "project-1", bundle.decision_kind, "shadow_disagreement"
    )
    assert controller.is_drift_held(bundle.candidate_digest)
    assert not controller.selected_for_execution(pointer, "correlation")

    assert controller.revoke(
        revocation, expected_candidate_digest=bundle.candidate_digest
    )
    assert controller.current("project-1", bundle.decision_kind) is None
    assert controller.is_revoked(bundle.candidate_digest)
    assert controller.inactive_reason("project-1", bundle.decision_kind) == "revoked"


def test_rollback_restores_latest_compatible_generation_or_disables(
    tmp_path: Path,
) -> None:
    artifacts, controller = _controller(tmp_path)
    old = _bundle(value="code")
    new = _bundle(value="review")
    old_approval = _receipt(old)
    new_approval = _receipt(new, created_at=NOW + timedelta(hours=1))
    rollback = _receipt(
        new,
        previous=new_approval.receipt_digest,
        receipt_type="rollback",
        state="rolled_back",
        created_at=NOW + timedelta(hours=2),
    )
    _persist(artifacts, old, old_approval)
    _persist(artifacts, new, new_approval, rollback)
    controller.install(old, old_approval, expected_candidate_digest=None)
    controller.install(
        new,
        new_approval,
        expected_candidate_digest=old.candidate_digest,
    )

    restored = controller.rollback(
        rollback,
        expected_candidate_digest=new.candidate_digest,
        now=NOW + timedelta(hours=3),
        policy_digest=SHA_E,
    )

    assert restored is not None
    assert restored.candidate_digest == old.candidate_digest

    disable = _receipt(
        old,
        previous=old_approval.receipt_digest,
        receipt_type="rollback",
        state="rolled_back",
        created_at=NOW + timedelta(hours=4),
    )
    artifacts.append_receipt(disable)
    assert controller.rollback(
        disable,
        expected_candidate_digest=old.candidate_digest,
        now=NOW + timedelta(days=31),
        policy_digest=SHA_E,
    ) is None
    assert controller.current("project-1", old.decision_kind) is None


def test_invalid_keys_empty_scopes_and_stale_atomic_operations_fail_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        stable_canary_bucket(b"", "project-1", "correlation", SHA_A)
    artifacts = DecisionArtifactStore(str(tmp_path), key=b"rollout-errors-key")
    with pytest.raises(RolloutError, match="HMAC key"):
        RolloutController(artifacts, AtomicGenerationStore(), rollout_key=b"")

    pointers = AtomicGenerationStore()
    pointer = GenerationPointer(
        project_id="project-1",
        decision_kind=_bundle().decision_kind,
        candidate_digest=SHA_A,
        receipt_digest=SHA_B,
        stage=RolloutStage.SHADOW,
        epoch=0,
    )
    assert pointers.current("project-1", pointer.decision_kind) is None
    assert not pointers.revoke(
        "project-1",
        pointer.decision_kind,
        SHA_A,
        remove_pointer=True,
    )
    with pytest.raises(RolloutError, match="expectation"):
        pointers.rollback(
            "project-1", pointer.decision_kind, SHA_A, lambda candidate: True
        )
    controller = RolloutController(
        artifacts, pointers, rollout_key=b"rollout-errors-key"
    )
    assert not controller.selected_for_execution(None, "correlation")
    assert not controller.mark_drift_hold(
        "project-1", pointer.decision_kind, "failure_rate"
    )
    assert pointers.reserve_use(SHA_A, "one", 1)
    assert pointers.reserve_use(SHA_A, "one", 1)
    assert not pointers.reserve_use(SHA_A, "two", 1)


def test_invalid_lifecycle_operations_are_rejected_before_pointer_mutation(
    tmp_path: Path,
) -> None:
    artifacts, controller = _controller(tmp_path)
    bundle = _bundle()
    approval = _receipt(bundle)
    canary = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="canary",
        created_at=NOW + timedelta(hours=1),
    )
    _persist(artifacts, bundle, approval, canary)
    controller.install(bundle, approval, expected_candidate_digest=None)

    with pytest.raises(RolloutError, match="promotion receipt"):
        controller.promote(approval, expected_candidate_digest=bundle.candidate_digest)
    with pytest.raises(RolloutError, match="expectation"):
        controller.promote(canary, expected_candidate_digest=SHA_A)
    with pytest.raises(RolloutError, match="revoked receipt"):
        controller.revoke(approval, expected_candidate_digest=bundle.candidate_digest)
    bad_revocation = canary.model_copy(
        update={
            "receipt_type": ReceiptType.REVOCATION,
            "lifecycle_state": LifecycleState.REVOKED,
            "previous_receipt_digest": SHA_A,
        }
    )
    with pytest.raises(RolloutError, match="extend"):
        controller.revoke(
            bad_revocation,
            expected_candidate_digest=bundle.candidate_digest,
        )
    with pytest.raises(RolloutError, match="rolled-back receipt"):
        controller.rollback(
            approval,
            expected_candidate_digest=bundle.candidate_digest,
            now=NOW,
            policy_digest=SHA_E,
        )
    bad_rollback = canary.model_copy(
        update={
            "receipt_type": ReceiptType.ROLLBACK,
            "lifecycle_state": LifecycleState.ROLLED_BACK,
            "previous_receipt_digest": SHA_A,
        }
    )
    with pytest.raises(RolloutError, match="extend"):
        controller.rollback(
            bad_rollback,
            expected_candidate_digest=bundle.candidate_digest,
            now=NOW,
            policy_digest=SHA_E,
        )


def test_promotion_plan_receipt_and_cas_failures_are_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, controller = _controller(tmp_path / "skip")
    bundle = _bundle()
    approval = _receipt(bundle)
    active = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="active",
        created_at=NOW + timedelta(hours=1),
    )
    _persist(artifacts, bundle, approval, active)
    controller.install(bundle, approval, expected_candidate_digest=None)
    with pytest.raises(RolloutError, match="exactly one"):
        controller.promote(active, expected_candidate_digest=bundle.candidate_digest)

    artifacts, controller = _controller(tmp_path / "missing-stage")
    approval = _receipt(bundle, rollout_plan=("shadow", "active"))
    canary = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="canary",
        created_at=NOW + timedelta(hours=1),
        rollout_plan=("shadow", "active"),
    )
    _persist(artifacts, bundle, approval, canary)
    controller.install(bundle, approval, expected_candidate_digest=None)
    with pytest.raises(RolloutError, match="outside"):
        controller.promote(canary, expected_candidate_digest=bundle.candidate_digest)

    artifacts, controller = _controller(tmp_path / "missing-receipt")
    approval = _receipt(bundle)
    unstored = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="canary",
        created_at=NOW + timedelta(hours=1),
    )
    _persist(artifacts, bundle, approval)
    controller.install(bundle, approval, expected_candidate_digest=None)
    with pytest.raises(RolloutError, match="verification"):
        controller.promote(unstored, expected_candidate_digest=bundle.candidate_digest)

    artifacts, controller = _controller(tmp_path / "receipt-object")
    approval = _receipt(bundle)
    canary = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="canary",
        created_at=NOW + timedelta(hours=1),
    )
    _persist(artifacts, bundle, approval, canary)
    controller.install(bundle, approval, expected_candidate_digest=None)
    altered = canary.model_copy(update={"authorization_evidence_digest": SHA_B})
    with pytest.raises(RolloutError, match="differs"):
        controller.promote(altered, expected_candidate_digest=bundle.candidate_digest)

    def lose_cas(
        pointer: GenerationPointer, *, expected_candidate_digest: str | None
    ) -> None:
        return None

    monkeypatch.setattr(controller.pointers, "compare_and_swap", lose_cas)
    with pytest.raises(RolloutError, match="atomic generation race"):
        controller.promote(canary, expected_candidate_digest=bundle.candidate_digest)


def test_verified_generation_rejects_object_scope_and_binding_mismatches(
    tmp_path: Path,
) -> None:
    artifacts, controller = _controller(tmp_path / "scope")
    bundle = _bundle()
    approval = _receipt(bundle)
    _persist(artifacts, bundle, approval)
    pointer = controller.install(bundle, approval, expected_candidate_digest=None)
    assert pointer is not None

    with pytest.raises(RolloutError, match="scope"):
        controller.verified_generation(replace(pointer, project_id="project-2"))
    with pytest.raises(RolloutError, match="candidate object"):
        controller.install(
            bundle.model_copy(update={"risk_scope": "medium"}),
            approval,
            expected_candidate_digest=bundle.candidate_digest,
        )

    artifacts, controller = _controller(tmp_path / "binding")
    artifacts.create_rule_bundle(bundle)
    data = approval.model_dump(
        mode="python", by_alias=True, exclude={"receipt_digest"}
    )
    data["corpus_digest"] = SHA_B
    mismatched = ApprovalReceiptV1.create(**data)
    artifacts.append_receipt(mismatched)
    with pytest.raises(RolloutError, match="does not bind"):
        controller.install(bundle, mismatched, expected_candidate_digest=None)
