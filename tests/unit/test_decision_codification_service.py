"""End-to-end contracts for mining logs and bypassing repeat agent decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

import general_ludd.decision_codification.service as service_module
from general_ludd.approval.gate import ApprovalDecision
from general_ludd.decision_codification.approval import DecisionApprovalService
from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.durable import DurableGenerationStore
from general_ludd.decision_codification.observability import DecisionReuseObservability
from general_ludd.decision_codification.rollout import (
    AtomicGenerationStore,
    RolloutController,
)
from general_ludd.decision_codification.runtime import DecisionRuntime
from general_ludd.decision_codification.schema import (
    DecisionAbstentionV1,
    DecisionKind,
    FallbackReason,
    LifecycleState,
    ReceiptType,
)
from general_ludd.decision_codification.service import (
    AnalysisRejectionReason,
    DecisionAnalysisError,
    DecisionLogAnalyzer,
    DecisionResolutionError,
    DecisionResolutionSource,
    DecisionResolver,
)
from general_ludd.replay.schema import (
    BundleManifestV1,
    CompletenessV1,
    CorrelationV1,
    EventEnvelopeV1,
    ModelIdentityV1,
    RedactionV1,
    RetentionV1,
    RuntimeIdentityV1,
    SourceIdentityV1,
)
from general_ludd.replay.store import ReplayIntegrityError, RunBundleStore, VerifiedBundle

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
POLICY_DIGEST = "sha256:" + "e" * 64
RECIPE_DIGEST = "sha256:" + "a" * 64
LOCK_DIGEST = "sha256:" + "b" * 64
SOURCE_DIGEST = "sha256:" + "c" * 64


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


def _verified_bundle(index: int) -> VerifiedBundle:
    run_id = f"run-{index:03d}"
    occurred_at = NOW - timedelta(days=48 - index)
    event_digest = f"sha256:{index + 1:064x}"
    event = EventEnvelopeV1(
        schema="gludd.run-event/v1",
        sequence=0,
        event_id=f"decision-{index:03d}",
        occurred_at=occurred_at,
        recorded_at=occurred_at,
        type="review.decided",
        project_id="project-1",
        correlation=CorrelationV1(
            todo_id=f"todo-{index:03d}",
            task_id=f"task-{index:03d}",
            trace_id=None,
        ),
        payload={
            "policy_digest": POLICY_DIGEST,
            "features": _features(),
            "decision": "approve",
            "verified_outcome": "success",
            "outcome_evidence": {
                "decision_event_digest": event_digest,
                "outcome": "success",
                "terminal_event_ids": [f"terminal-{index:03d}"],
                "gate_digests": [SOURCE_DIGEST],
                "status_digests": [],
            },
        },
        redaction=RedactionV1(count=0, kinds=()),
        digest=event_digest,
    )
    manifest = BundleManifestV1(
        schema="gludd.run-bundle/v1",
        run_id=run_id,
        parent_run_id=None,
        operation="record",
        created_at=occurred_at,
        finalized_at=occurred_at + timedelta(seconds=1),
        status="completed",
        project_id="project-1",
        source=SourceIdentityV1(
            repository_url_sha256=SOURCE_DIGEST,
            commit_sha="1" * 40,
            tree_sha="2" * 40,
            branch="development",
            dirty=False,
        ),
        runtime=RuntimeIdentityV1(
            gludd_version="0.1.2",
            python_version="3.14.0",
            os="darwin",
            architecture="arm64",
            config_sha256=SOURCE_DIGEST,
            feature_flags={"decision_codification": True},
        ),
        model=ModelIdentityV1(
            provider="openai",
            profile="default",
            model="gpt-6",
            request_parameters={},
            provider_revision=None,
        ),
        event_count=1,
        events_sha256=f"sha256:{index + 1000:064x}",
        attachments=(),
        completeness=CompletenessV1(
            expected_stages=("run.started", "run.completed"),
            observed_stages=("run.started", "run.completed"),
            recorder_errors=(),
            missing_ranges=(),
        ),
        retention=RetentionV1(expires_at=None, pinned=False, hold_reason=None),
        integrity="signed",
        signing_key_id="decision-log-test-key",
    )
    return VerifiedBundle(manifest=manifest, events=(event,))


class _VerifiedStore:
    def __init__(self, bundles: tuple[VerifiedBundle, ...]) -> None:
        self._bundles = {bundle.manifest.run_id: bundle for bundle in bundles}
        self.reads: list[str] = []

    def read_verified(self, run_id: str) -> VerifiedBundle:
        self.reads.append(run_id)
        return self._bundles[run_id]


class _MixedStore(_VerifiedStore):
    def read_verified(self, run_id: str) -> VerifiedBundle:
        if run_id == "broken-run":
            self.reads.append(run_id)
            raise ReplayIntegrityError("bundle failed verification")
        return super().read_verified(run_id)


class _UnusedGate:
    def request_approval(self, request: object) -> object:
        raise AssertionError("direct approval should not create an extra request")


def _analysis_kwargs() -> dict[str, Any]:
    return {
        "project_id": "project-1",
        "current_policy_digest": POLICY_DIGEST,
        "training_recipe_digest": RECIPE_DIGEST,
        "dependency_lock_digest": LOCK_DIGEST,
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=90),
        "maximum_use_count": 1_000,
        "estimated_tokens_per_call": 2_000,
    }


def _signed_split_decision_bundle(
    root: Path,
    *,
    outcome_count: int = 1,
    outcome_decision_digest: str | None = None,
    outcome_task_id: str = "task-live-001",
) -> RunBundleStore:
    """Persist one real signed decision/outcome pair through the replay store."""
    store = RunBundleStore(
        root,
        verification_keys={"decision-log-test-key": b"decision-log-test-key-material"},
        active_key_id="decision-log-test-key",
    )
    occurred_at = NOW - timedelta(days=1)
    decision_time = occurred_at.isoformat().replace("+00:00", "Z")
    outcome_time = (occurred_at + timedelta(seconds=1)).isoformat().replace(
        "+00:00", "Z"
    )
    decision = store.append_event(
        "live-review-001",
        {
            "event_id": "decision-live-001",
            "occurred_at": decision_time,
            "recorded_at": decision_time,
            "type": "review.decided",
            "project_id": "project-1",
            "correlation": {
                "todo_id": "todo-live-001",
                "task_id": "task-live-001",
                "trace_id": None,
            },
            "payload": {
                "policy_digest": POLICY_DIGEST,
                "features": _features(),
                "decision": "approve",
            },
            "redaction": {"count": 0, "kinds": []},
        },
    )
    for index in range(outcome_count):
        store.append_event(
            "live-review-001",
            {
                "event_id": f"outcome-live-{index:03d}",
                "occurred_at": outcome_time,
                "recorded_at": outcome_time,
                "type": "decision.outcome",
                "project_id": "project-1",
                "correlation": {
                    "todo_id": "todo-live-001",
                    "task_id": outcome_task_id,
                    "trace_id": None,
                },
                "payload": {
                    "decision_event_digest": (
                        decision.digest
                        if outcome_decision_digest is None
                        else outcome_decision_digest
                    ),
                    "verified_outcome": "success",
                    "terminal_event_ids": [f"terminal-live-{index:03d}"],
                    "gate_digests": [SOURCE_DIGEST],
                    "status_digests": [],
                },
                "redaction": {"count": 0, "kinds": []},
            },
        )
    manifest = _verified_bundle(1).manifest.model_copy(
        update={
            "run_id": "live-review-001",
            "created_at": occurred_at,
            "finalized_at": occurred_at + timedelta(seconds=2),
        }
    )
    store.finalize("live-review-001", manifest)
    return store


def test_real_signed_split_decision_outcome_becomes_mining_evidence(
    tmp_path: Path,
) -> None:
    store = _signed_split_decision_bundle(tmp_path / "replays")

    analysis = DecisionLogAnalyzer(store).analyze(
        ("live-review-001",),
        **_analysis_kwargs(),
    )

    assert analysis.bundles_read == 1
    assert analysis.events_seen == 2
    assert analysis.events_eligible == 1
    assert analysis.candidates == ()
    assert analysis.rejection_counts == (
        (AnalysisRejectionReason.NO_SAFE_GROUP, 1),
    )


@pytest.mark.parametrize(
    ("case", "helper_kwargs", "expected_events", "expected_rejections"),
    [
        (
            "missing",
            {"outcome_count": 0},
            1,
            ((AnalysisRejectionReason.MISSING_OUTCOME, 1),),
        ),
        (
            "duplicate",
            {"outcome_count": 2},
            3,
            ((AnalysisRejectionReason.CONFLICTING_OUTCOME, 1),),
        ),
        (
            "orphan",
            {"outcome_decision_digest": SOURCE_DIGEST},
            2,
            (
                (AnalysisRejectionReason.MISSING_OUTCOME, 1),
                (AnalysisRejectionReason.ORPHAN_OUTCOME, 1),
            ),
        ),
        (
            "correlation-mismatch",
            {"outcome_task_id": "task-other"},
            2,
            ((AnalysisRejectionReason.CONFLICTING_OUTCOME, 1),),
        ),
    ],
)
def test_split_outcome_links_fail_closed_without_mining_ambiguous_evidence(
    tmp_path: Path,
    case: str,
    helper_kwargs: dict[str, Any],
    expected_events: int,
    expected_rejections: tuple[tuple[AnalysisRejectionReason, int], ...],
) -> None:
    store = _signed_split_decision_bundle(tmp_path / case, **helper_kwargs)

    analysis = DecisionLogAnalyzer(store).analyze(
        ("live-review-001",),
        **_analysis_kwargs(),
    )

    assert analysis.events_seen == expected_events
    assert analysis.events_eligible == 0
    assert analysis.candidates == ()
    assert analysis.rejection_counts == expected_rejections


def test_verified_logs_become_an_exact_zero_agent_runtime_path(tmp_path: Path) -> None:
    bundles = tuple(_verified_bundle(index) for index in range(48))
    store = _VerifiedStore(bundles)
    analyzer = DecisionLogAnalyzer(store)

    analysis = analyzer.analyze(
        tuple(bundle.manifest.run_id for bundle in bundles), **_analysis_kwargs()
    )

    assert store.reads == [bundle.manifest.run_id for bundle in bundles]
    assert analysis.bundles_read == 48
    assert analysis.events_seen == 48
    assert analysis.events_eligible == 48
    assert analysis.rejection_counts == ()
    assert len(analysis.candidates) == 1
    candidate = analysis.candidates[0]
    assert candidate.evidence_count == 48
    assert candidate.validation_report.false_automation_count == 0
    assert candidate.holdout_report.false_automation_count == 0
    assert candidate.holdout_report.estimated_agent_calls_avoided == 8
    assert candidate.holdout_report.estimated_tokens_avoided == 16_000

    artifacts = DecisionArtifactStore(
        str(tmp_path / "artifacts"), key=b"decision-artifacts"
    )
    approval = DecisionApprovalService(
        artifacts,
        _UnusedGate(),  # type: ignore[arg-type]
        authorizer=lambda identity, project, kind: (
            identity == "human-1"
            and project == "project-1"
            and kind is DecisionKind.REVIEW
        ),
    )
    receipt = approval.approve(
        candidate.bundle,
        candidate.holdout_report,
        decision=ApprovalDecision.APPROVED,
        approver_identity="human-1",
        authorization_evidence_digest=SOURCE_DIGEST,
        source_code_digest=SOURCE_DIGEST,
        policy_digest=POLICY_DIGEST,
        now=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(days=30),
        rollout_plan=("shadow", "active"),
        maximum_use_count=1_000,
    )
    rollout = RolloutController(
        artifacts,
        AtomicGenerationStore(),
        rollout_key=b"decision-rollout",
    )
    rollout.install(candidate.bundle, receipt, expected_candidate_digest=None)
    active = approval.append_lifecycle(
        receipt,
        receipt_type=ReceiptType.PROMOTION,
        lifecycle_state=LifecycleState.ACTIVE,
        decision=ApprovalDecision.APPROVED,
        approver_identity="human-1",
        authorization_evidence_digest=SOURCE_DIGEST,
        now=NOW + timedelta(minutes=2),
    )
    rollout.promote(active, expected_candidate_digest=candidate.bundle.candidate_digest)

    timer = iter(
        (0, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 20_000_000)
    )
    observability = DecisionReuseObservability(
        DurableGenerationStore(tmp_path / "observability.sqlite3"),
        rollout,
        artifacts,
        project_id="project-1",
        policy_digest=POLICY_DIGEST,
        clock=lambda: NOW + timedelta(minutes=10),
    )
    resolver = DecisionResolver(
        DecisionRuntime(artifacts, rollout),
        observability=observability,
        monotonic_ns=lambda: next(timer),
    )
    fallback_calls: list[str] = []

    def fallback(reason: object) -> str:
        fallback_calls.append(type(reason).__name__)
        return "reject"

    resolved = resolver.resolve(
        project_id="project-1",
        expected_project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        policy_digest=POLICY_DIGEST,
        features=_features(),
        correlation_id="live-task-1",
        now=NOW + timedelta(minutes=3),
        side_effect_id="review-result-1",
        fallback=fallback,
    )
    assert resolved.source is DecisionResolutionSource.CODIFIED
    assert resolved.decision == "approve"
    assert fallback_calls == []

    unseen = resolver.resolve(
        project_id="project-1",
        expected_project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        policy_digest=POLICY_DIGEST,
        features=_features(queue="interactive"),
        correlation_id="live-task-2",
        now=NOW + timedelta(minutes=4),
        side_effect_id="review-result-2",
        fallback=fallback,
    )
    assert unseen.source is DecisionResolutionSource.AGENT_FALLBACK
    assert unseen.decision == "reject"
    assert fallback_calls == ["DecisionAbstentionV1"]

    rollback = approval.append_lifecycle(
        active,
        receipt_type=ReceiptType.ROLLBACK,
        lifecycle_state=LifecycleState.ROLLED_BACK,
        decision=ApprovalDecision.APPROVED,
        approver_identity="human-1",
        authorization_evidence_digest=SOURCE_DIGEST,
        now=NOW + timedelta(minutes=5),
    )
    assert (
        rollout.rollback(
            rollback,
            expected_candidate_digest=candidate.bundle.candidate_digest,
            now=NOW + timedelta(minutes=5),
            policy_digest=POLICY_DIGEST,
        )
        is None
    )
    after_rollback = resolver.resolve(
        project_id="project-1",
        expected_project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        policy_digest=POLICY_DIGEST,
        features=_features(),
        correlation_id="live-task-3",
        now=NOW + timedelta(minutes=6),
        side_effect_id="review-result-3",
        fallback=fallback,
    )
    assert after_rollback.source is DecisionResolutionSource.AGENT_FALLBACK
    assert fallback_calls == ["DecisionAbstentionV1", "DecisionAbstentionV1"]
    status = observability.status_receipt(
        project_id="project-1",
        policy_digest=POLICY_DIGEST,
    )
    review = next(
        item
        for item in status.summaries
        if item.decision_kind is DecisionKind.REVIEW
    )
    assert review.exact_rule_hits == 1
    assert review.avoided_agent_calls == 1
    assert review.typed_abstentions == 2
    assert review.fallback_calls == 2
    assert review.latency_observations == 3
    assert review.latency_total_us == 14_000
    assert [(item.reason, item.count) for item in review.abstentions] == [
        (FallbackReason.NO_ACTIVE_RULE, 1),
        (FallbackReason.SCOPE_MISS, 1),
    ]
    assert review.current_candidate_digest is None


def test_analyzer_excludes_untrusted_or_unusable_evidence_content_free() -> None:
    unsigned_source = _verified_bundle(1)
    unsigned = VerifiedBundle(
        manifest=unsigned_source.manifest.model_copy(
            update={
                "run_id": "unsigned-run",
                "integrity": "unsigned",
                "signing_key_id": None,
            }
        ),
        events=unsigned_source.events,
    )
    foreign_source = _verified_bundle(2)
    foreign = VerifiedBundle(
        manifest=foreign_source.manifest.model_copy(
            update={"run_id": "foreign-run", "project_id": "project-2"}
        ),
        events=foreign_source.events,
    )
    missing_source = _verified_bundle(3)
    missing_event = missing_source.events[0].model_copy(
        update={"correlation": CorrelationV1()}
    )
    missing = VerifiedBundle(
        manifest=missing_source.manifest.model_copy(update={"run_id": "missing-run"}),
        events=(missing_event,),
    )
    unsupported_source = _verified_bundle(4)
    unsupported_event = unsupported_source.events[0].model_copy(
        update={"type": "tool.responded"}
    )
    unsupported = VerifiedBundle(
        manifest=unsupported_source.manifest.model_copy(
            update={"run_id": "unsupported-run"}
        ),
        events=(unsupported_event,),
    )
    store = _MixedStore((unsigned, foreign, missing, unsupported))

    result = DecisionLogAnalyzer(store).analyze(
        (
            "broken-run",
            "unsigned-run",
            "foreign-run",
            "missing-run",
            "unsupported-run",
        ),
        **_analysis_kwargs(),
    )

    assert result.bundles_read == 4
    assert result.events_seen == 2
    assert result.events_eligible == 0
    assert result.candidates == ()
    assert dict(result.rejection_counts) == {
        AnalysisRejectionReason.UNVERIFIED_BUNDLE: 1,
        AnalysisRejectionReason.UNSIGNED_BUNDLE: 1,
        AnalysisRejectionReason.PROJECT_MISMATCH: 1,
        AnalysisRejectionReason.MISSING_ROOT_TASK: 1,
        AnalysisRejectionReason.NORMALIZATION_REFUSED: 1,
    }


def test_analyzer_enforces_request_and_resource_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _verified_bundle(0)
    analyzer = DecisionLogAnalyzer(_VerifiedStore((bundle,)))
    kwargs = _analysis_kwargs()

    with pytest.raises(DecisionAnalysisError, match=r"1\.\.10000"):
        analyzer.analyze((), **kwargs)
    with pytest.raises(DecisionAnalysisError, match="unique"):
        analyzer.analyze(("run-000", "run-000"), **kwargs)
    with pytest.raises(DecisionAnalysisError, match="project"):
        analyzer.analyze(("run-000",), **(kwargs | {"project_id": ""}))
    with pytest.raises(DecisionAnalysisError, match="expiry"):
        analyzer.analyze(
            ("run-000",), **(kwargs | {"expires_at": NOW - timedelta(seconds=1)})
        )
    with pytest.raises(DecisionAnalysisError, match="positive"):
        analyzer.analyze(("run-000",), **(kwargs | {"maximum_use_count": 0}))
    with pytest.raises(DecisionAnalysisError, match="non-negative"):
        analyzer.analyze(
            ("run-000",), **(kwargs | {"estimated_tokens_per_call": -1})
        )

    sparse = analyzer.analyze(("run-000",), **kwargs)
    assert dict(sparse.rejection_counts) == {
        AnalysisRejectionReason.NO_SAFE_GROUP: 1
    }

    monkeypatch.setattr(service_module, "MAX_ANALYSIS_EVENTS", 0)
    with pytest.raises(DecisionAnalysisError, match="event hard limit"):
        analyzer.analyze(("run-000",), **kwargs)


def test_resolver_rejects_an_agent_decision_outside_the_closed_vocabulary() -> None:
    class _AbstainingRuntime:
        def lookup(self, **kwargs: object) -> DecisionAbstentionV1:
            return DecisionAbstentionV1(reason=FallbackReason.NO_ACTIVE_RULE)

    resolver = DecisionResolver(cast(DecisionRuntime, _AbstainingRuntime()))
    calls: list[FallbackReason] = []

    def invalid_fallback(abstention: DecisionAbstentionV1) -> str:
        calls.append(abstention.reason)
        return "invented_action"

    with pytest.raises(DecisionResolutionError, match="action vocabulary"):
        resolver.resolve(
            project_id="project-1",
            expected_project_id="project-1",
            decision_kind=DecisionKind.REVIEW,
            policy_digest=POLICY_DIGEST,
            features=_features(),
            correlation_id="live-task-invalid",
            now=NOW,
            side_effect_id="invalid-result",
            fallback=invalid_fallback,
        )
    assert calls == [FallbackReason.NO_ACTIVE_RULE]


def test_observability_failure_cannot_change_fallback_decision() -> None:
    class _AbstainingRuntime:
        def lookup(self, **kwargs: object) -> DecisionAbstentionV1:
            return DecisionAbstentionV1(reason=FallbackReason.NO_ACTIVE_RULE)

    class _RaisingObserver:
        def record_resolution(self, **kwargs: object) -> bool:
            raise RuntimeError("private observer failure")

    timer = iter((0, 1_000_000))
    resolver = DecisionResolver(
        cast(DecisionRuntime, _AbstainingRuntime()),
        observability=cast(DecisionReuseObservability, _RaisingObserver()),
        monotonic_ns=lambda: next(timer),
    )
    calls: list[FallbackReason] = []

    def fallback(abstention: DecisionAbstentionV1) -> str:
        calls.append(abstention.reason)
        return "reject"

    resolved = resolver.resolve(
        project_id="project-1",
        expected_project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        policy_digest=POLICY_DIGEST,
        features=_features(),
        correlation_id="live-task-observer-failure",
        now=NOW,
        side_effect_id="observer-failure-result",
        fallback=fallback,
    )

    assert resolved.source is DecisionResolutionSource.AGENT_FALLBACK
    assert resolved.decision == "reject"
    assert calls == [FallbackReason.NO_ACTIVE_RULE]
