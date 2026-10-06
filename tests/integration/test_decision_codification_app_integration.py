"""Integration coverage for opt-in decision codification application wiring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from general_ludd.daemon import create_daemon_app
from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.normalize import normalize_decision_context
from general_ludd.decision_codification.rollout import (
    AtomicGenerationStore,
    RolloutController,
)
from general_ludd.decision_codification.runtime import DecisionRuntime
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionAbstentionV1,
    DecisionContextV1,
    DecisionKind,
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    DecisionRuleNodeV1,
    FallbackReason,
    OutcomeCountsV1,
)
from general_ludd.decision_codification.service import (
    AnalysisRejectionReason,
    DecisionCodificationAdapter,
    DecisionCodificationIntegrationError,
    DecisionResolutionSource,
)
from general_ludd.event_loop.loop import EventLoop
from general_ludd.replay.store import ReplayIntegrityError, VerifiedBundle

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
POLICY_DIGEST = "sha256:" + "e" * 64
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_F = "sha256:" + "f" * 64


class _RejectingVerifiedReader:
    def __init__(self) -> None:
        self.reads: list[str] = []

    def read_verified(self, run_id: str) -> VerifiedBundle:
        self.reads.append(run_id)
        raise ReplayIntegrityError("untrusted bundle")


def _features(*, queue: str = "batch") -> dict[str, object]:
    return {
        "work_type": "code",
        "queue": queue,
        "risk_band": "low",
        "resource_profile": "cpu",
        "provider_class": "local",
        "operation_class": "review",
        "status": "succeeded",
        "approval_required": False,
        "fallback_allowed": True,
        "reversible": True,
        "retry_count": 0,
        "estimated_cost_microusd": 50_000,
        "latency_ms": 250,
        "required_evidence": True,
    }


def _active_runtime(tmp_path: Path) -> DecisionRuntime:
    context = normalize_decision_context(
        project_id="project-1",
        expected_project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        policy_digest=POLICY_DIGEST,
        features=_features(),
    )
    assert isinstance(context, DecisionContextV1)
    bundle = DecisionRuleBundleV1.create(
        schema="gludd.decision-rule-bundle/v1",
        project_id="project-1",
        decision_kind="review",
        feature_schema=context.feature_schema,
        policy_compatibility=(POLICY_DIGEST,),
        risk_scope="low",
        observed_context_digests=(context.context_signature,),
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
                    success=0,
                    failure=0,
                    reverted=0,
                    unknown=0,
                    unsafe=0,
                ),
                abstain=True,
            ),
            DecisionRuleLeafV1(
                leaf_id="approve",
                decision="approve",
                support=20,
                confidence=1.0,
                outcome_counts=OutcomeCountsV1(
                    success=20,
                    failure=0,
                    reverted=0,
                    unknown=0,
                    unsafe=0,
                ),
                abstain=False,
            ),
        ),
        corpus_digest=SHA_A,
        training_recipe_digest=SHA_B,
        dependency_lock_digest=SHA_C,
        created_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=90),
        maximum_use_count=10,
    )
    approval = ApprovalReceiptV1.create(
        schema="gludd.decision-approval-receipt/v1",
        receipt_type="approval",
        lifecycle_state="shadow",
        previous_receipt_digest=None,
        candidate_digest=bundle.candidate_digest,
        corpus_digest=bundle.corpus_digest,
        evaluator_report_digest=SHA_F,
        feature_schema=bundle.feature_schema,
        policy_digest=POLICY_DIGEST,
        source_code_digest=SHA_B,
        dependency_lock_digest=bundle.dependency_lock_digest,
        training_recipe_digest=bundle.training_recipe_digest,
        project_id=bundle.project_id,
        decision_kind=bundle.decision_kind,
        approver_identity_hmac="hmac-sha256:" + "1" * 64,
        authorization_evidence_digest=SHA_A,
        created_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(days=30),
        risk_class="low",
        rollout_plan=("shadow", "active"),
        maximum_use_count=10,
    )
    active = ApprovalReceiptV1.create(
        **(
            approval.model_dump(mode="python", by_alias=True)
            | {
                "receipt_digest": "sha256:" + "0" * 64,
                "receipt_type": "promotion",
                "lifecycle_state": "active",
                "previous_receipt_digest": approval.receipt_digest,
                "created_at": NOW,
            }
        )
    )
    artifacts = DecisionArtifactStore(str(tmp_path / "artifacts"), key=b"artifact-key")
    artifacts.create_rule_bundle(bundle)
    artifacts.append_receipt(approval)
    artifacts.append_receipt(active)
    rollout = RolloutController(
        artifacts,
        AtomicGenerationStore(),
        rollout_key=b"rollout-key",
    )
    rollout.install(bundle, approval, expected_candidate_digest=None)
    rollout.promote(active, expected_candidate_digest=bundle.candidate_digest)
    return DecisionRuntime(artifacts, rollout)


def test_adapter_is_opt_in_and_wired_through_daemon_and_event_loop(
    tmp_path: Path,
) -> None:
    default_app = create_daemon_app(tick_interval=300.0)
    assert default_app.state.decision_codification is None

    adapter = DecisionCodificationAdapter(
        bundle_reader=_RejectingVerifiedReader(),
        runtime=_active_runtime(tmp_path),
        project_id="project-1",
        policy_digest=POLICY_DIGEST,
    )
    app = create_daemon_app(
        tick_interval=300.0,
        decision_codification=adapter,
    )
    event_loop = EventLoop(decision_codification=app.state.decision_codification)

    assert app.state.decision_codification is adapter
    assert event_loop.decision_codification is adapter


def test_exact_active_rule_skips_fallback_and_every_abstention_calls_once(
    tmp_path: Path,
) -> None:
    adapter = DecisionCodificationAdapter(
        bundle_reader=_RejectingVerifiedReader(),
        runtime=_active_runtime(tmp_path),
        project_id="project-1",
        policy_digest=POLICY_DIGEST,
    )
    fallback_reasons: list[FallbackReason] = []

    def fallback(abstention: DecisionAbstentionV1) -> str:
        fallback_reasons.append(abstention.reason)
        return "reject"

    exact = adapter.resolve(
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        features=_features(),
        correlation_id="correlation-exact",
        now=NOW + timedelta(minutes=1),
        side_effect_id="effect-exact",
        fallback=fallback,
    )
    assert exact.source is DecisionResolutionSource.CODIFIED
    assert exact.decision == "approve"
    assert fallback_reasons == []

    abstentions = (
        ("project-1", _features(queue="interactive"), FallbackReason.SCOPE_MISS),
        ("project-2", _features(), FallbackReason.NORMALIZATION_REFUSED),
        (
            "project-1",
            _features() | {"unknown_feature": "must-not-pass"},
            FallbackReason.NORMALIZATION_REFUSED,
        ),
    )
    for index, (project_id, features, expected_reason) in enumerate(abstentions):
        calls_before = len(fallback_reasons)
        abstained = adapter.resolve(
            project_id=project_id,
            decision_kind=DecisionKind.REVIEW,
            features=features,
            correlation_id=f"correlation-miss-{index}",
            now=NOW + timedelta(minutes=index + 2),
            side_effect_id=f"effect-miss-{index}",
            fallback=fallback,
        )
        assert abstained.source is DecisionResolutionSource.AGENT_FALLBACK
        assert abstained.decision == "reject"
        assert abstained.abstention is not None
        assert abstained.abstention.reason is expected_reason
        assert len(fallback_reasons) == calls_before + 1


def test_bad_binding_and_unverified_evidence_fail_closed(tmp_path: Path) -> None:
    runtime = _active_runtime(tmp_path)
    reader = _RejectingVerifiedReader()
    with pytest.raises(DecisionCodificationIntegrationError, match="project"):
        DecisionCodificationAdapter(
            bundle_reader=reader,
            runtime=runtime,
            project_id="",
            policy_digest=POLICY_DIGEST,
        )
    with pytest.raises(DecisionCodificationIntegrationError, match="policy"):
        DecisionCodificationAdapter(
            bundle_reader=reader,
            runtime=runtime,
            project_id="project-1",
            policy_digest="not-a-digest",
        )

    adapter = DecisionCodificationAdapter(
        bundle_reader=reader,
        runtime=runtime,
        project_id="project-1",
        policy_digest=POLICY_DIGEST,
    )
    analysis = adapter.analyze(
        ("unverified-run",),
        training_recipe_digest=SHA_B,
        dependency_lock_digest=SHA_C,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
        maximum_use_count=10,
    )

    assert reader.reads == ["unverified-run"]
    assert analysis.candidates == ()
    assert dict(analysis.rejection_counts) == {
        AnalysisRejectionReason.UNVERIFIED_BUNDLE: 1
    }
