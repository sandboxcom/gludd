"""Shared PostgreSQL generation-state acceptance regressions."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import general_ludd.decision_codification.configuration as configuration_module
from general_ludd.db.models import (
    Base,
    BucketLeaseModel,
    ProjectModel,
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.configuration import (
    DecisionCodificationConfig,
    DecisionCodificationConfigurationError,
    build_configured_components,
)
from general_ludd.decision_codification.rollout import (
    AtomicGenerationStore,
    GenerationPointer,
    RolloutController,
    RolloutError,
)
from general_ludd.decision_codification.runtime import DecisionRuntime
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionApplicationOutcomeV1,
    DecisionKind,
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    DecisionRuleNodeV1,
    OutcomeCountsV1,
    RolloutStage,
    VerifiedOutcome,
)
from general_ludd.decision_codification.service import (
    DecisionResolutionSource,
    DecisionResolver,
)
from general_ludd.decision_codification.shared_generation import (
    PostgresGenerationStore,
    SharedGenerationStoreError,
)

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
POLICY = "sha256:" + "e" * 64
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64


def _features() -> dict[str, object]:
    return {
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
        "retry_count": 0,
        "estimated_cost_microusd": 50_000,
        "latency_ms": 250,
        "required_evidence": True,
    }


def _bundle(*, project_id: str, marker: int) -> DecisionRuleBundleV1:
    return DecisionRuleBundleV1.create(
        schema="gludd.decision-rule-bundle/v1",
        project_id=project_id,
        decision_kind="review",
        feature_schema=SHA_D,
        policy_compatibility=(POLICY,),
        risk_scope="low",
        root_id="node-1",
        default_leaf_id="leaf-abstain",
        nodes=(
            DecisionRuleNodeV1(
                node_id="node-1",
                feature_id="work_type",
                operator="eq",
                value="code",
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
                    success=0,
                    failure=0,
                    reverted=0,
                    unknown=0,
                    unsafe=0,
                ),
                abstain=True,
            ),
            DecisionRuleLeafV1(
                leaf_id="leaf-approve",
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
        corpus_digest=f"sha256:{marker:064x}",
        training_recipe_digest=SHA_A,
        dependency_lock_digest=SHA_B,
        observed_context_digests=(SHA_C,),
        created_at=NOW,
        expires_at=NOW + timedelta(days=30),
        maximum_use_count=100,
    )


def _active_generation(
    artifacts: DecisionArtifactStore,
    *,
    project_id: str = "project-1",
    marker: int,
) -> GenerationPointer:
    bundle = _bundle(project_id=project_id, marker=marker)
    approval = ApprovalReceiptV1.create(
        schema="gludd.decision-approval-receipt/v1",
        receipt_type="approval",
        lifecycle_state="shadow",
        previous_receipt_digest=None,
        candidate_digest=bundle.candidate_digest,
        corpus_digest=bundle.corpus_digest,
        evaluator_report_digest=SHA_C,
        feature_schema=bundle.feature_schema,
        policy_digest=POLICY,
        source_code_digest=SHA_A,
        dependency_lock_digest=bundle.dependency_lock_digest,
        training_recipe_digest=bundle.training_recipe_digest,
        project_id=project_id,
        decision_kind="review",
        approver_identity_hmac="hmac-sha256:" + "1" * 64,
        authorization_evidence_digest=SHA_B,
        created_at=NOW,
        expires_at=NOW + timedelta(days=20),
        risk_class="low",
        rollout_plan=("shadow", "active"),
        maximum_use_count=100,
    )
    active = ApprovalReceiptV1.create(
        **{
            **approval.model_dump(
                mode="python",
                by_alias=True,
                exclude={"receipt_digest", "receipt_type", "lifecycle_state"},
            ),
            "receipt_type": "promotion",
            "lifecycle_state": "active",
            "previous_receipt_digest": approval.receipt_digest,
            "created_at": NOW + timedelta(minutes=1),
        }
    )
    artifacts.create_rule_bundle(bundle)
    artifacts.append_receipt(approval)
    artifacts.append_receipt(active)
    return GenerationPointer(
        project_id=project_id,
        decision_kind=DecisionKind.REVIEW,
        candidate_digest=bundle.candidate_digest,
        receipt_digest=active.receipt_digest,
        stage=RolloutStage.ACTIVE,
        epoch=0,
    )


def _outcome(
    pointer: GenerationPointer,
    *,
    application_id: str = SHA_A,
    outcome: VerifiedOutcome = VerifiedOutcome.SUCCESS,
    occurred_at: datetime = NOW,
) -> DecisionApplicationOutcomeV1:
    return DecisionApplicationOutcomeV1(
        schema="gludd.decision-application-outcome/v1",
        project_id=pointer.project_id,
        decision_kind=pointer.decision_kind,
        candidate_digest=pointer.candidate_digest,
        application_id=application_id,
        rollout_stage=pointer.stage,
        outcome=outcome,
        occurred_at=occurred_at,
        terminal_event_id=application_id,
        evidence_digest=SHA_B,
    )


@pytest.fixture
def shared_backend(
    tmp_path: Path,
) -> Iterator[tuple[Engine, DecisionArtifactStore]]:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'shared-state.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                ProjectModel(project_id="project-1", name="Project 1"),
                ProjectModel(project_id="project-2", name="Project 2"),
            ]
        )
    artifacts = DecisionArtifactStore(
        str(tmp_path / "shared-artifacts"),
        key=b"shared-generation-artifact-key",
    )
    yield engine, artifacts
    engine.dispose()


def _store(
    engine: Engine,
    artifacts: DecisionArtifactStore,
    **kwargs: Any,
) -> PostgresGenerationStore:
    return PostgresGenerationStore(
        engine,
        artifacts,
        allow_sqlite_for_tests=True,
        clock=lambda: NOW,
        **kwargs,
    )


def test_concurrent_hosts_publish_only_one_initial_generation(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    pointers = (
        _active_generation(artifacts, marker=1),
        _active_generation(artifacts, marker=2),
    )

    def publish(pointer: GenerationPointer) -> GenerationPointer | None:
        return _store(engine, artifacts).compare_and_swap(
            pointer,
            expected_candidate_digest=None,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(publish, pointers))

    winners = tuple(result for result in results if result is not None)
    assert len(winners) == 1
    assert _store(engine, artifacts).current(
        "project-1", DecisionKind.REVIEW
    ) == winners[0]


def test_expired_owner_is_recovered_but_live_foreign_owner_blocks(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    pointer = _active_generation(artifacts, marker=3)
    store = _store(engine, artifacts)
    lease_key = store.coordination_key("project-1")
    with Session(engine) as session, session.begin():
        session.add(
            BucketLeaseModel(
                bucket_key=lease_key,
                project_id="project-1",
                holder_id="other-host",
                expires_at=NOW + timedelta(seconds=30),
                heartbeat_at=NOW,
            )
        )

    with pytest.raises(SharedGenerationStoreError, match="unavailable"):
        store.compare_and_swap(pointer, expected_candidate_digest=None)

    with Session(engine) as session, session.begin():
        lease = session.scalar(
            select(BucketLeaseModel).where(BucketLeaseModel.bucket_key == lease_key)
        )
        assert lease is not None
        lease.expires_at = NOW - timedelta(seconds=1)

    assert store.compare_and_swap(pointer, expected_candidate_digest=None) is not None


def test_crash_rolls_back_claim_and_partial_generation(
    shared_backend: tuple[Engine, DecisionArtifactStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, artifacts = shared_backend
    pointer = _active_generation(artifacts, marker=4)
    first = _store(engine, artifacts)

    def crash(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("private-crash-detail")

    monkeypatch.setattr(first, "_write_head", crash)
    with pytest.raises(SharedGenerationStoreError, match="write failed") as error:
        first.compare_and_swap(pointer, expected_candidate_digest=None)
    assert "private-crash-detail" not in str(error.value)

    second = _store(engine, artifacts)
    installed = second.compare_and_swap(pointer, expected_candidate_digest=None)
    assert installed is not None
    with Session(engine) as session:
        assert session.scalar(select(BucketLeaseModel)) is None


def test_tampered_or_orphaned_shared_state_fails_closed(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    pointer = _active_generation(artifacts, marker=5)
    store = _store(engine, artifacts)
    installed = store.compare_and_swap(pointer, expected_candidate_digest=None)
    assert installed is not None

    with Session(engine) as session, session.begin():
        row = session.scalar(
            select(VariableValueModel).where(VariableValueModel.key == "head:review")
        )
        assert row is not None
        row.value = "{}"
    with pytest.raises(SharedGenerationStoreError, match="invalid"):
        store.current("project-1", DecisionKind.REVIEW)

    with Session(engine) as session, session.begin():
        session.query(VariableValueModel).delete()
        session.query(VariableNamespaceModel).delete()
    replacement = _active_generation(artifacts, marker=6)
    assert store.compare_and_swap(replacement, expected_candidate_digest=None)
    receipt_name = replacement.receipt_digest.removeprefix("sha256:")
    artifact_root = Path(object.__getattribute__(artifacts, "_base"))
    (artifact_root / f"decision-receipt-{receipt_name}.json").unlink()
    with pytest.raises(SharedGenerationStoreError, match="artifact"):
        store.current("project-1", DecisionKind.REVIEW)


def test_exact_scope_and_full_generation_identity_reject_stale_aba(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    store = _store(engine, artifacts)
    first = _active_generation(artifacts, marker=7)
    second = _active_generation(artifacts, marker=8)
    third = _active_generation(artifacts, marker=9)
    installed_first = store.compare_and_swap(first, expected_candidate_digest=None)
    assert installed_first is not None
    installed_second = store.compare_and_swap(
        second,
        expected_candidate_digest=installed_first.candidate_digest,
        expected_generation=installed_first,
    )
    assert installed_second is not None
    restored = store.rollback(
        "project-1",
        DecisionKind.REVIEW,
        installed_second.candidate_digest,
        lambda candidate: candidate.candidate_digest
        == installed_first.candidate_digest,
        expected_generation=installed_second,
    )
    assert restored is not None
    assert restored.candidate_digest == installed_first.candidate_digest
    assert restored.epoch > installed_first.epoch

    assert (
        store.compare_and_swap(
            third,
            expected_candidate_digest=installed_first.candidate_digest,
            expected_generation=installed_first,
        )
        is None
    )
    wrong_scope = first.__class__(
        project_id="project-2",
        decision_kind=first.decision_kind,
        candidate_digest=first.candidate_digest,
        receipt_digest=first.receipt_digest,
        stage=first.stage,
        epoch=0,
    )
    with pytest.raises(SharedGenerationStoreError, match="scope"):
        store.compare_and_swap(wrong_scope, expected_candidate_digest=None)
    assert store.current("project-2", DecisionKind.REVIEW) is None


def test_use_limits_and_feedback_are_shared_across_hosts(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    pointer = _active_generation(artifacts, marker=10)
    first = _store(engine, artifacts)
    second = _store(engine, artifacts)
    installed = first.compare_and_swap(pointer, expected_candidate_digest=None)
    assert installed is not None
    assert first.reserve_use(installed.candidate_digest, SHA_A, 1)
    assert second.reserve_use(installed.candidate_digest, SHA_A, 1)
    assert not second.reserve_use(installed.candidate_digest, SHA_B, 1)
    assert second.use_count(installed.candidate_digest) == 1


def test_shared_revocation_hold_tombstone_and_emergency_recovery(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    first = _store(engine, artifacts)
    second = _store(engine, artifacts)
    pointer = _active_generation(artifacts, marker=12)

    assert first.current("project-1", DecisionKind.REVIEW) is None
    assert first.inactive_reason("project-1", DecisionKind.REVIEW) is None
    installed = first.compare_and_swap(pointer, expected_candidate_digest=None)
    assert installed is not None
    assert not second.is_drift_held(installed.candidate_digest)
    assert not second.is_revoked(installed.candidate_digest)

    first.mark_drift_hold(installed.candidate_digest, "failure_rate")
    first.mark_drift_hold(installed.candidate_digest, "safety_violation")
    assert second.is_drift_held(installed.candidate_digest)
    assert not second.revoke(
        "project-1",
        DecisionKind.REVIEW,
        SHA_A,
        remove_pointer=True,
        expected_generation=installed,
    )
    assert second.revoke(
        "project-1",
        DecisionKind.REVIEW,
        installed.candidate_digest,
        remove_pointer=False,
        expected_generation=installed,
    )
    assert second.is_revoked(installed.candidate_digest)
    assert second.current("project-1", DecisionKind.REVIEW) == installed
    assert first.revoke(
        "project-1",
        DecisionKind.REVIEW,
        installed.candidate_digest,
        remove_pointer=True,
        expected_generation=installed,
    )
    assert second.current("project-1", DecisionKind.REVIEW) is None
    assert second.inactive_reason("project-1", DecisionKind.REVIEW) == "revoked"

    replacement = _active_generation(artifacts, marker=13)
    installed_replacement = first.compare_and_swap(
        replacement,
        expected_candidate_digest=None,
    )
    assert installed_replacement is not None
    receipt_name = installed_replacement.receipt_digest.removeprefix("sha256:")
    artifact_root = Path(object.__getattribute__(artifacts, "_base"))
    (artifact_root / f"decision-receipt-{receipt_name}.json").unlink()
    first.force_revoke(installed_replacement.candidate_digest)
    assert first.is_revoked(installed_replacement.candidate_digest)


def test_shared_outcomes_are_idempotent_bounded_and_fail_closed(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    first = _store(engine, artifacts)
    second = _store(engine, artifacts)
    pointer = _active_generation(artifacts, marker=14)
    installed = first.compare_and_swap(pointer, expected_candidate_digest=None)
    assert installed is not None
    assert not first.has_application(installed.candidate_digest, SHA_A)
    assert first.reserve_use(installed.candidate_digest, SHA_A, 2)
    assert second.has_application(installed.candidate_digest, SHA_A)

    success = _outcome(installed)
    assert first.record_outcome(success)
    assert not second.record_outcome(success)
    assert second.recent_outcomes(installed.candidate_digest, now=NOW) == (
        VerifiedOutcome.SUCCESS,
    )
    assert second.recent_outcomes(
        installed.candidate_digest,
        now=NOW + timedelta(days=8),
    ) == ()
    with pytest.raises(RolloutError, match="conflicting outcome"):
        second.record_outcome(
            _outcome(installed, outcome=VerifiedOutcome.FAILURE)
        )
    with pytest.raises(RolloutError, match="issued application"):
        second.record_outcome(_outcome(installed, application_id=SHA_B))

    with Session(engine) as session, session.begin():
        row = session.scalar(
            select(VariableValueModel).where(
                VariableValueModel.key == "outcome-index"
            )
        )
        assert row is not None
        row.value = "{}"
    with pytest.raises(SharedGenerationStoreError, match="invalid"):
        second.recent_outcomes(installed.candidate_digest, now=NOW)


def test_rollback_disables_when_every_history_generation_is_held(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    store = _store(engine, artifacts)
    first = _active_generation(artifacts, marker=15)
    second = _active_generation(artifacts, marker=16)
    installed_first = store.compare_and_swap(first, expected_candidate_digest=None)
    assert installed_first is not None
    installed_second = store.compare_and_swap(
        second,
        expected_candidate_digest=installed_first.candidate_digest,
        expected_generation=installed_first,
    )
    assert installed_second is not None
    store.mark_drift_hold(installed_first.candidate_digest, "failure_rate")

    assert (
        store.rollback(
            "project-1",
            DecisionKind.REVIEW,
            installed_second.candidate_digest,
            lambda _candidate: True,
            expected_generation=installed_second,
        )
        is None
    )
    assert store.current("project-1", DecisionKind.REVIEW) is None
    assert store.inactive_reason("project-1", DecisionKind.REVIEW) is None
    with pytest.raises(RolloutError, match="compare-and-swap"):
        store.rollback(
            "project-1",
            DecisionKind.REVIEW,
            installed_second.candidate_digest,
            lambda _candidate: True,
            expected_generation=installed_second,
        )


@pytest.mark.parametrize("ttl", [0, 61, True])
def test_shared_configuration_and_inputs_are_bounded(
    shared_backend: tuple[Engine, DecisionArtifactStore],
    ttl: object,
) -> None:
    engine, artifacts = shared_backend
    with pytest.raises(ValueError, match="TTL"):
        PostgresGenerationStore(
            engine,
            artifacts,
            lease_ttl_seconds=cast(Any, ttl),
            allow_sqlite_for_tests=True,
        )
    with pytest.raises(ValueError, match="PostgreSQL"):
        PostgresGenerationStore(engine, artifacts)


@pytest.mark.parametrize("timeout", [0, 61, True, "ten"])
def test_shared_lock_timeout_is_bounded(
    shared_backend: tuple[Engine, DecisionArtifactStore],
    timeout: object,
) -> None:
    engine, artifacts = shared_backend
    with pytest.raises(ValueError, match="lock timeout"):
        PostgresGenerationStore(
            engine,
            artifacts,
            lock_timeout_seconds=cast(Any, timeout),
            allow_sqlite_for_tests=True,
        )


def test_shared_validation_refuses_invalid_scope_counts_and_time(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    store = _store(engine, artifacts)
    store.verify_ready("project-1")
    with pytest.raises(SharedGenerationStoreError, match="scope is unavailable"):
        store.verify_ready("missing-project")
    with pytest.raises(SharedGenerationStoreError, match="project scope"):
        store.current("bad project", DecisionKind.REVIEW)
    with pytest.raises(SharedGenerationStoreError, match="digest"):
        store.is_revoked("not-a-digest")
    with pytest.raises(SharedGenerationStoreError, match="drift reason"):
        pointer = _active_generation(artifacts, marker=17)
        installed = store.compare_and_swap(pointer, expected_candidate_digest=None)
        assert installed is not None
        store.mark_drift_hold(installed.candidate_digest, "private reason")
    assert installed is not None
    with pytest.raises(SharedGenerationStoreError, match="use count"):
        store.reserve_use(installed.candidate_digest, SHA_A, 0)
    with pytest.raises(SharedGenerationStoreError, match="timestamp"):
        store.recent_outcomes(
            installed.candidate_digest,
            now=datetime(2026, 10, 8),
        )


def test_full_identity_and_unmaterialized_scope_fail_closed(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    store = _store(engine, artifacts)
    pointer = _active_generation(artifacts, marker=18)

    with pytest.raises(SharedGenerationStoreError, match="identity is required"):
        store.compare_and_swap(
            pointer,
            expected_candidate_digest=pointer.candidate_digest,
        )
    with pytest.raises(SharedGenerationStoreError, match="identity is invalid"):
        store.compare_and_swap(
            pointer,
            expected_candidate_digest=None,
            expected_generation=pointer,
        )
    with pytest.raises(SharedGenerationStoreError, match="identity is invalid"):
        store.compare_and_swap(
            pointer,
            expected_candidate_digest=SHA_A,
            expected_generation=pointer,
        )

    wrong_project = pointer.__class__(
        project_id="project-2",
        decision_kind=pointer.decision_kind,
        candidate_digest=pointer.candidate_digest,
        receipt_digest=pointer.receipt_digest,
        stage=pointer.stage,
        epoch=pointer.epoch,
    )
    with pytest.raises(SharedGenerationStoreError, match="identity is invalid"):
        store.compare_and_swap(
            pointer,
            expected_candidate_digest=pointer.candidate_digest,
            expected_generation=wrong_project,
        )
    other_kind = next(
        kind for kind in DecisionKind if kind is not pointer.decision_kind
    )
    wrong_kind = pointer.__class__(
        project_id=pointer.project_id,
        decision_kind=other_kind,
        candidate_digest=pointer.candidate_digest,
        receipt_digest=pointer.receipt_digest,
        stage=pointer.stage,
        epoch=pointer.epoch,
    )
    with pytest.raises(SharedGenerationStoreError, match="identity is invalid"):
        store.compare_and_swap(
            pointer,
            expected_candidate_digest=pointer.candidate_digest,
            expected_generation=wrong_kind,
        )

    with pytest.raises(SharedGenerationStoreError, match="identity is required"):
        store.revoke(
            pointer.project_id,
            pointer.decision_kind,
            pointer.candidate_digest,
            remove_pointer=True,
        )
    assert not store.revoke(
        pointer.project_id,
        pointer.decision_kind,
        pointer.candidate_digest,
        remove_pointer=True,
        expected_generation=pointer,
    )
    with pytest.raises(SharedGenerationStoreError, match="identity is required"):
        store.rollback(
            pointer.project_id,
            pointer.decision_kind,
            pointer.candidate_digest,
            lambda _candidate: True,
        )
    with pytest.raises(RolloutError, match="compare-and-swap"):
        store.rollback(
            pointer.project_id,
            pointer.decision_kind,
            pointer.candidate_digest,
            lambda _candidate: True,
            expected_generation=pointer,
        )

    assert not store.has_application(pointer.candidate_digest, SHA_A)
    assert store.use_count(pointer.candidate_digest) == 0
    assert store.recent_outcomes(pointer.candidate_digest, now=NOW) == ()
    assert not store.is_revoked(pointer.candidate_digest)
    with pytest.raises(SharedGenerationStoreError, match="scope is unavailable"):
        store.reserve_use(pointer.candidate_digest, SHA_A, 1)
    with pytest.raises(SharedGenerationStoreError, match="outcome is invalid"):
        store.record_outcome(cast(Any, object()))
    with pytest.raises(SharedGenerationStoreError, match="outcome scope is invalid"):
        store.record_outcome(
            _outcome(pointer).model_copy(update={"project_id": "project-2"})
        )
    with pytest.raises(SharedGenerationStoreError, match="scope is unavailable"):
        store.record_outcome(_outcome(pointer))


def test_corrupt_shared_head_abstains_to_exactly_one_fallback(
    shared_backend: tuple[Engine, DecisionArtifactStore],
) -> None:
    engine, artifacts = shared_backend
    store = _store(engine, artifacts)
    pointer = _active_generation(artifacts, marker=11)
    assert store.compare_and_swap(pointer, expected_candidate_digest=None)
    with Session(engine) as session, session.begin():
        row = session.scalar(
            select(VariableValueModel).where(VariableValueModel.key == "head:review")
        )
        assert row is not None
        row.value = "tampered"

    resolver = DecisionResolver(
        DecisionRuntime(
            artifacts,
            RolloutController(artifacts, store, rollout_key=b"rollout-key"),
        )
    )
    calls = 0

    def fallback(_abstention: object) -> str:
        nonlocal calls
        calls += 1
        return "approve"

    resolution = resolver.resolve(
        project_id="project-1",
        expected_project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        policy_digest=POLICY,
        features=_features(),
        correlation_id="correlation-1",
        now=NOW,
        side_effect_id="side-effect-1",
        fallback=fallback,
    )

    assert calls == 1
    assert resolution.source is DecisionResolutionSource.AGENT_FALLBACK


def test_secret_indirect_configuration_selects_shared_generation_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeSharedStore(AtomicGenerationStore):
        def __init__(
            self,
            engine: object,
            artifacts: DecisionArtifactStore,
            *,
            lock_timeout_seconds: float,
        ) -> None:
            super().__init__()
            captured.update(
                engine=engine,
                artifacts=artifacts,
                lock_timeout_seconds=lock_timeout_seconds,
            )

        def verify_ready(self, project_id: str) -> None:
            captured["verified_project_id"] = project_id

    database_engine = object()

    def fake_create_engine(url: str, **kwargs: object) -> object:
        captured.update(url=url, engine_kwargs=kwargs)
        return database_engine

    monkeypatch.setattr(configuration_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(
        configuration_module,
        "PostgresGenerationStore",
        FakeSharedStore,
    )
    config = DecisionCodificationConfig(
        enabled=True,
        project_id="project-1",
        policy_digest=POLICY,
        replay_root=tmp_path / "replays",
        artifact_root=tmp_path / "artifacts",
        state_path=tmp_path / "local-observability.sqlite3",
        generation_database_url_env="TEST_GENERATION_DATABASE_URL",
        replay_key_id="primary",
        replay_key_envs={"primary": "TEST_REPLAY_KEY"},
        artifact_key_env="TEST_ARTIFACT_KEY",
        rollout_key_env="TEST_ROLLOUT_KEY",
    )
    environment = {
        "TEST_REPLAY_KEY": "replay-key-material-123",
        "TEST_ARTIFACT_KEY": "artifact-key-material-123",
        "TEST_ROLLOUT_KEY": "rollout-key-material-123",
        "TEST_GENERATION_DATABASE_URL": (
            "postgresql+psycopg://gludd:secret@db.invalid/gludd"
        ),
    }

    components = build_configured_components(config, environ=environment)

    assert components is not None
    assert isinstance(components.pointers, FakeSharedStore)
    assert captured["url"] == environment["TEST_GENERATION_DATABASE_URL"]
    assert captured["engine"] is database_engine
    assert captured["verified_project_id"] == "project-1"
    dumped = str(config.model_dump(mode="json"))
    assert environment["TEST_GENERATION_DATABASE_URL"] not in dumped
    assert "gludd:secret" not in dumped
    with pytest.raises(
        DecisionCodificationConfigurationError,
        match="database URL is unavailable",
    ):
        build_configured_components(config, environ={**environment, "TEST_GENERATION_DATABASE_URL": ""})
