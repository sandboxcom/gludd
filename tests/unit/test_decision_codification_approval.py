"""Tests for digest-bound, fail-closed human decision approval."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from general_ludd.approval.gate import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
)
from general_ludd.decision_codification.approval import (
    ApprovalRefused,
    DecisionApprovalService,
)
from general_ludd.decision_codification.artifact_store import (
    ArtifactIntegrityError,
    DecisionArtifactStore,
)
from general_ludd.decision_codification.schema import (
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    DecisionRuleNodeV1,
    EvaluationReportV1,
    LeafEvaluationV1,
    LifecycleState,
    OutcomeCountsV1,
    ReceiptType,
)

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
SHA_E = "sha256:" + "e" * 64
NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


class _Gate:
    def __init__(self) -> None:
        self.requests: list[ApprovalRequest] = []

    def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        self.requests.append(request)
        return ApprovalResponse(request=request)


def _bundle() -> DecisionRuleBundleV1:
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
                feature_id="work_type",
                operator="eq",
                value="code",
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
        observed_context_digests=(SHA_E,),
        created_at=NOW,
        expires_at=NOW + timedelta(days=90),
        maximum_use_count=100,
    )


def _report(bundle: DecisionRuleBundleV1, **changes: Any) -> EvaluationReportV1:
    data: dict[str, object] = {
        "schema": "gludd.decision-evaluation-report/v1",
        "candidate_digest": bundle.candidate_digest,
        "corpus_digest": bundle.corpus_digest,
        "project_id": bundle.project_id,
        "decision_kind": bundle.decision_kind,
        "created_at": NOW,
        "support_count": 20,
        "root_task_count": 8,
        "utc_day_count": 3,
        "source_agent_count": 2,
        "exact_match_count": 20,
        "abstention_count": 0,
        "precision": 1.0,
        "leaf_results": (
            LeafEvaluationV1(leaf_id="approve", support=20, confidence=1.0),
        ),
        "false_automation_count": 0,
        "conflict_count": 0,
        "unknown_feature_count": 0,
        "policy_mismatch_count": 0,
        "verified_failure_count": 0,
        "rollback_count": 0,
        "safety_violation_count": 0,
        "estimated_agent_calls_avoided": 20,
        "estimated_tokens_avoided": 2000,
        "historical_policy_digest": SHA_E,
        "current_policy_digest": SHA_E,
        "deterministic_replay_runs": 2,
    }
    data.update(changes)
    return EvaluationReportV1.create(**data)


def _service(tmp_path: Path, gate: _Gate) -> DecisionApprovalService:
    store = DecisionArtifactStore(str(tmp_path), key=b"approval-key-for-tests")
    return DecisionApprovalService(
        store,
        gate,
        authorizer=lambda identity, project, kind: (
            identity == "human-1" and project == "project-1" and kind.value == "review"
        ),
    )


def test_review_request_contains_only_safe_digest_bound_context(tmp_path: Path) -> None:
    gate = _Gate()
    service = _service(tmp_path, gate)
    bundle = _bundle()
    report = _report(bundle)

    response = service.request_review(bundle, report, requester="miner")

    assert response.decision is ApprovalDecision.PENDING
    request = gate.requests[0]
    assert request.resource_id == bundle.candidate_digest
    assert request.metadata["candidate_digest"] == bundle.candidate_digest
    assert request.metadata["precision"] == 1.0
    assert "prompt" not in repr(request.metadata).lower()


def test_approval_is_authorized_exact_and_immutable(tmp_path: Path) -> None:
    gate = _Gate()
    service = _service(tmp_path, gate)
    bundle = _bundle()
    report = _report(bundle)

    receipt = service.approve(
        bundle,
        report,
        decision=ApprovalDecision.APPROVED,
        approver_identity="human-1",
        authorization_evidence_digest=SHA_C,
        source_code_digest=SHA_B,
        policy_digest=SHA_E,
        now=NOW,
        expires_at=NOW + timedelta(days=30),
        rollout_plan=("shadow", "canary", "active"),
        maximum_use_count=50,
    )

    assert receipt.receipt_type is ReceiptType.APPROVAL
    assert receipt.candidate_digest == bundle.candidate_digest
    assert "human-1" not in receipt.approver_identity_hmac
    assert service.artifact_store.verify_receipt_chain(receipt.receipt_digest) == (
        receipt,
    )
    with pytest.raises(ApprovalRefused, match="immutable"):
        service.approve(
            bundle,
            report,
            decision=ApprovalDecision.APPROVED,
            approver_identity="human-1",
            authorization_evidence_digest=SHA_C,
            source_code_digest=SHA_B,
            policy_digest=SHA_E,
            now=NOW,
            expires_at=NOW + timedelta(days=30),
            rollout_plan=("shadow", "canary", "active"),
            maximum_use_count=50,
        )


@pytest.mark.parametrize(
    ("decision", "identity", "report_changes", "message"),
    [
        (ApprovalDecision.PENDING, "human-1", {}, "human approval"),
        (ApprovalDecision.APPROVED, "intruder", {}, "authorized"),
        (ApprovalDecision.APPROVED, "human-1", {"precision": 0.98}, "precision"),
        (
            ApprovalDecision.APPROVED,
            "human-1",
            {"false_automation_count": 1},
            "false automation",
        ),
    ],
)
def test_approval_fails_closed_before_persistence(
    tmp_path: Path,
    decision: ApprovalDecision,
    identity: str,
    report_changes: dict[str, object],
    message: str,
) -> None:
    service = _service(tmp_path, _Gate())
    bundle = _bundle()
    report = _report(bundle, **report_changes)

    with pytest.raises(ApprovalRefused, match=message):
        service.approve(
            bundle,
            report,
            decision=decision,
            approver_identity=identity,
            authorization_evidence_digest=SHA_C,
            source_code_digest=SHA_B,
            policy_digest=SHA_E,
            now=NOW,
            expires_at=NOW + timedelta(days=30),
            rollout_plan=("shadow", "canary", "active"),
            maximum_use_count=50,
        )


def test_lifecycle_receipts_copy_exact_bindings_and_chain_to_approval(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, _Gate())
    bundle = _bundle()
    approval = service.approve(
        bundle,
        _report(bundle),
        decision=ApprovalDecision.APPROVED,
        approver_identity="human-1",
        authorization_evidence_digest=SHA_C,
        source_code_digest=SHA_B,
        policy_digest=SHA_E,
        now=NOW,
        expires_at=NOW + timedelta(days=30),
        rollout_plan=("shadow", "canary", "active"),
        maximum_use_count=50,
    )

    promotion = service.append_lifecycle(
        approval,
        receipt_type=ReceiptType.PROMOTION,
        lifecycle_state=LifecycleState.CANARY,
        decision=ApprovalDecision.APPROVED,
        approver_identity="human-1",
        authorization_evidence_digest=SHA_C,
        now=NOW + timedelta(hours=1),
    )

    assert promotion.previous_receipt_digest == approval.receipt_digest
    assert promotion.candidate_digest == approval.candidate_digest
    assert service.artifact_store.verify_receipt_chain(
        promotion.receipt_digest
    ) == (approval, promotion)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("policy", "policy"),
        ("expiry", "expiry"),
        ("uses", "use count"),
        ("binding", "exact candidate"),
        ("conflict", "conflicts"),
        ("safety", "safety"),
        ("replay", "deterministic replay"),
    ],
)
def test_approval_rejects_every_digest_and_evaluation_bound(
    tmp_path: Path, case: str, message: str
) -> None:
    service = _service(tmp_path, _Gate())
    bundle = _bundle()
    report = _report(bundle)
    policy = SHA_E
    expiry = NOW + timedelta(days=30)
    uses = 50
    if case == "policy":
        policy = SHA_D
    elif case == "expiry":
        expiry = NOW + timedelta(days=91)
    elif case == "uses":
        uses = 101
    elif case == "binding":
        report = report.model_copy(update={"project_id": "project-2"})
    elif case == "conflict":
        report = report.model_copy(update={"conflict_count": 1})
    elif case == "safety":
        report = report.model_copy(update={"safety_violation_count": 1})
    elif case == "replay":
        report = report.model_copy(update={"deterministic_replay_runs": 1})

    with pytest.raises(ApprovalRefused, match=message):
        service.approve(
            bundle,
            report,
            decision=ApprovalDecision.APPROVED,
            approver_identity="human-1",
            authorization_evidence_digest=SHA_C,
            source_code_digest=SHA_B,
            policy_digest=policy,
            now=NOW,
            expires_at=expiry,
            rollout_plan=("shadow", "canary", "active"),
            maximum_use_count=uses,
        )


def test_authorization_and_storage_exceptions_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = _Gate()
    store = DecisionArtifactStore(str(tmp_path), key=b"approval-errors-key")

    def broken_authorizer(identity: str, project: str, kind: object) -> bool:
        raise RuntimeError("authorization backend unavailable")

    service = DecisionApprovalService(store, gate, authorizer=broken_authorizer)
    bundle = _bundle()
    report = _report(bundle)
    with pytest.raises(ApprovalRefused, match="authorization failed"):
        service.approve(
            bundle,
            report,
            decision=ApprovalDecision.APPROVED,
            approver_identity="human-1",
            authorization_evidence_digest=SHA_C,
            source_code_digest=SHA_B,
            policy_digest=SHA_E,
            now=NOW,
            expires_at=NOW + timedelta(days=30),
            rollout_plan=("shadow", "active"),
            maximum_use_count=50,
        )

    service = _service(tmp_path / "storage", gate)

    def fail_append(receipt: object) -> None:
        raise ArtifactIntegrityError("storage unavailable")

    monkeypatch.setattr(service.artifact_store, "append_receipt", fail_append)
    with pytest.raises(ApprovalRefused, match="persistence failed"):
        service.approve(
            bundle,
            report,
            decision=ApprovalDecision.APPROVED,
            approver_identity="human-1",
            authorization_evidence_digest=SHA_C,
            source_code_digest=SHA_B,
            policy_digest=SHA_E,
            now=NOW,
            expires_at=NOW + timedelta(days=30),
            rollout_plan=("shadow", "active"),
            maximum_use_count=50,
        )
