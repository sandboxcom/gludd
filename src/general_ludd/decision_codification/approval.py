"""Fail-closed adapter from human approval to immutable lifecycle receipts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from general_ludd.approval.gate import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
)
from general_ludd.decision_codification.artifact_store import (
    ArtifactAlreadyExists,
    ArtifactStoreError,
    DecisionArtifactStore,
)
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionKind,
    DecisionRuleBundleV1,
    EvaluationReportV1,
    LifecycleState,
    ReceiptType,
    RolloutStage,
)


class ApprovalGateAdapter(Protocol):
    """Narrow existing approval-gate interface used by this workflow."""

    def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """Create a fail-closed human approval request."""
        ...


ApproverAuthorizer = Callable[[str, str, DecisionKind], bool]


class ApprovalRefused(RuntimeError):
    """Raised when exact approval evidence is absent or unsafe."""


class DecisionApprovalService:
    """Create digest-bound receipts only after exact human authorization."""

    def __init__(
        self,
        artifact_store: DecisionArtifactStore,
        gate: ApprovalGateAdapter,
        *,
        authorizer: ApproverAuthorizer,
    ) -> None:
        """Bind authenticated storage, the human gate, and authorization policy."""
        self.artifact_store = artifact_store
        self._gate = gate
        self._authorizer = authorizer

    def request_review(
        self,
        bundle: DecisionRuleBundleV1,
        report: EvaluationReportV1,
        *,
        requester: str,
    ) -> ApprovalResponse:
        """Send only bounded scope, rule, metric, and digest data to HITL."""
        self._validate_report_binding(bundle, report)
        request = ApprovalRequest(
            resource_id=bundle.candidate_digest,
            action="approve decision rule candidate",
            requester=requester,
            reason="Review the exact digest-bound deterministic rule candidate.",
            metadata={
                "candidate_digest": bundle.candidate_digest,
                "corpus_digest": bundle.corpus_digest,
                "report_digest": report.report_digest,
                "project_id": bundle.project_id,
                "decision_kind": bundle.decision_kind.value,
                "risk_scope": bundle.risk_scope,
                "precision": report.precision,
                "support_count": report.support_count,
                "false_automation_count": report.false_automation_count,
                "safety_violation_count": report.safety_violation_count,
                "expires_at": bundle.expires_at.isoformat(),
            },
        )
        return self._gate.request_approval(request)

    def approve(
        self,
        bundle: DecisionRuleBundleV1,
        report: EvaluationReportV1,
        *,
        decision: ApprovalDecision,
        approver_identity: str,
        authorization_evidence_digest: str,
        source_code_digest: str,
        policy_digest: str,
        now: datetime,
        expires_at: datetime,
        rollout_plan: tuple[str | RolloutStage, ...],
        maximum_use_count: int,
    ) -> ApprovalReceiptV1:
        """Persist the one immutable initial approval for an exact candidate."""
        self._require_human_authorization(
            decision, approver_identity, bundle.project_id, bundle.decision_kind
        )
        self._validate_report_binding(bundle, report)
        self._validate_report_quality(report)
        if policy_digest not in bundle.policy_compatibility:
            raise ApprovalRefused("approved policy is not candidate-compatible")
        if expires_at > bundle.expires_at:
            raise ApprovalRefused("approval expiry exceeds candidate expiry")
        if maximum_use_count > bundle.maximum_use_count:
            raise ApprovalRefused("approval use count exceeds candidate bound")

        receipt = ApprovalReceiptV1.create(
            schema="gludd.decision-approval-receipt/v1",
            receipt_type=ReceiptType.APPROVAL,
            lifecycle_state=LifecycleState.SHADOW,
            previous_receipt_digest=None,
            candidate_digest=bundle.candidate_digest,
            corpus_digest=bundle.corpus_digest,
            evaluator_report_digest=report.report_digest,
            feature_schema=bundle.feature_schema,
            policy_digest=policy_digest,
            source_code_digest=source_code_digest,
            dependency_lock_digest=bundle.dependency_lock_digest,
            training_recipe_digest=bundle.training_recipe_digest,
            project_id=bundle.project_id,
            decision_kind=bundle.decision_kind,
            approver_identity_hmac=self.artifact_store.identity_hmac(
                approver_identity, bundle.project_id
            ),
            authorization_evidence_digest=authorization_evidence_digest,
            created_at=now,
            expires_at=expires_at,
            risk_class=bundle.risk_scope,
            rollout_plan=rollout_plan,
            maximum_use_count=maximum_use_count,
        )
        try:
            self._ensure_rule(bundle)
            self._ensure_report(report)
            self.artifact_store.append_receipt(receipt)
        except ArtifactAlreadyExists as exc:
            raise ApprovalRefused("approval receipt is immutable and already exists") from exc
        except ArtifactStoreError as exc:
            raise ApprovalRefused("approval persistence failed closed") from exc
        return receipt

    def append_lifecycle(
        self,
        previous: ApprovalReceiptV1,
        *,
        receipt_type: ReceiptType,
        lifecycle_state: LifecycleState,
        decision: ApprovalDecision,
        approver_identity: str,
        authorization_evidence_digest: str,
        now: datetime,
    ) -> ApprovalReceiptV1:
        """Append an authorized promotion, rollback, revocation, or expiry."""
        self._require_human_authorization(
            decision,
            approver_identity,
            previous.project_id,
            previous.decision_kind,
        )
        receipt = ApprovalReceiptV1.create(
            schema="gludd.decision-approval-receipt/v1",
            receipt_type=receipt_type,
            lifecycle_state=lifecycle_state,
            previous_receipt_digest=previous.receipt_digest,
            candidate_digest=previous.candidate_digest,
            corpus_digest=previous.corpus_digest,
            evaluator_report_digest=previous.evaluator_report_digest,
            feature_schema=previous.feature_schema,
            policy_digest=previous.policy_digest,
            source_code_digest=previous.source_code_digest,
            dependency_lock_digest=previous.dependency_lock_digest,
            training_recipe_digest=previous.training_recipe_digest,
            project_id=previous.project_id,
            decision_kind=previous.decision_kind,
            approver_identity_hmac=self.artifact_store.identity_hmac(
                approver_identity, previous.project_id
            ),
            authorization_evidence_digest=authorization_evidence_digest,
            created_at=now,
            expires_at=previous.expires_at,
            risk_class=previous.risk_class,
            rollout_plan=previous.rollout_plan,
            maximum_use_count=previous.maximum_use_count,
        )
        try:
            self.artifact_store.append_receipt(receipt)
        except ArtifactStoreError as exc:
            raise ApprovalRefused("lifecycle receipt persistence failed closed") from exc
        return receipt

    def _require_human_authorization(
        self,
        decision: ApprovalDecision,
        identity: str,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> None:
        if decision is not ApprovalDecision.APPROVED:
            raise ApprovalRefused("explicit human approval is required")
        try:
            authorized = self._authorizer(identity, project_id, decision_kind)
        except Exception as exc:
            raise ApprovalRefused("approver authorization failed closed") from exc
        if not authorized:
            raise ApprovalRefused("approver is not authorized for this scope")

    @staticmethod
    def _validate_report_binding(
        bundle: DecisionRuleBundleV1, report: EvaluationReportV1
    ) -> None:
        if (
            report.candidate_digest != bundle.candidate_digest
            or report.corpus_digest != bundle.corpus_digest
            or report.project_id != bundle.project_id
            or report.decision_kind is not bundle.decision_kind
        ):
            raise ApprovalRefused("evaluation report does not bind the exact candidate")

    @staticmethod
    def _validate_report_quality(report: EvaluationReportV1) -> None:
        if report.precision < 0.99:
            raise ApprovalRefused("holdout precision is below 0.99")
        if report.false_automation_count:
            raise ApprovalRefused("evaluation contains false automation")
        if report.conflict_count:
            raise ApprovalRefused("evaluation contains rule conflicts")
        if report.safety_violation_count:
            raise ApprovalRefused("evaluation contains safety violations")
        if report.deterministic_replay_runs < 2:
            raise ApprovalRefused("evaluation lacks deterministic replay evidence")

    def _ensure_rule(self, bundle: DecisionRuleBundleV1) -> None:
        try:
            self.artifact_store.create_rule_bundle(bundle)
        except ArtifactAlreadyExists:
            if self.artifact_store.read_rule_bundle(bundle.candidate_digest) != bundle:
                raise ApprovalRefused("candidate digest maps to different content") from None

    def _ensure_report(self, report: EvaluationReportV1) -> None:
        try:
            self.artifact_store.create_evaluation_report(report)
        except ArtifactAlreadyExists:
            if self.artifact_store.read_evaluation_report(report.report_digest) != report:
                raise ApprovalRefused("report digest maps to different content") from None


__all__ = [
    "ApprovalGateAdapter",
    "ApprovalRefused",
    "ApproverAuthorizer",
    "DecisionApprovalService",
]
