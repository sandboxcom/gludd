"""Hermetic producer-to-codified-reuse proof through the live review seam."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Table
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.approval.gate import ApprovalDecision
from general_ludd.config.decision_codification import DecisionCodificationConfig
from general_ludd.db.models import BucketLeaseModel, ProjectModel
from general_ludd.decision_codification.approval import DecisionApprovalService
from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.capture import DecisionOutcomeRecorder
from general_ludd.decision_codification.configuration import build_configured_adapter
from general_ludd.decision_codification.rollout import (
    AtomicGenerationStore,
    RolloutController,
)
from general_ludd.decision_codification.runtime import DecisionRuntime
from general_ludd.decision_codification.schema import (
    DecisionKind,
    LifecycleState,
    ReceiptType,
)
from general_ludd.decision_codification.service import DecisionCodificationAdapter
from general_ludd.event_loop.loop import EventLoop
from general_ludd.replay.schema import (
    ModelIdentityV1,
    RuntimeIdentityV1,
    SourceIdentityV1,
    canonical_replay_json,
)
from general_ludd.replay.store import RunBundleStore
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn

POLICY_DIGEST = "sha256:" + "a" * 64
SOURCE_DIGEST = "sha256:" + "b" * 64
RECIPE_DIGEST = "sha256:" + "c" * 64
LOCK_DIGEST = "sha256:" + "d" * 64
KEY_ID = "producer-reuse-key"
REPLAY_KEY = b"producer-reuse-replay-key"
CORRELATION_KEY = b"producer-reuse-correlation-key"
ARTIFACT_KEY = b"producer-reuse-artifact-key"
ROLLOUT_KEY = b"producer-reuse-rollout-key"
EVIDENCE_START = datetime(2026, 9, 20, 12, tzinfo=UTC)
ACTIVE_AT = datetime(2026, 10, 1, 12, tzinfo=UTC)


class _Clock:
    """Controllable UTC clock for evidence diversity and expiry checks."""

    current = EVIDENCE_START

    @classmethod
    def now(cls, tz: object = None) -> datetime:
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current


class _Reviewer:
    """Count live agent interactions while returning record-bound decisions."""

    def __init__(self) -> None:
        self.calls = 0
        self.decision = "complete"

    def review_return(
        self,
        task_return: TaskReturn,
        **_kwargs: object,
    ) -> TaskDecision:
        self.calls += 1
        return TaskDecision(
            return_id=task_return.return_id,
            matched_todo_id=task_return.todo_id,
            decision=self.decision,
            confidence=0.99,
        )


class _UnusedGate:
    def request_approval(self, _request: object) -> object:
        raise AssertionError("direct approval must not make a second request")


def _record(index: int, **changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "return_id": f"RET-PRODUCER-{index:03d}",
        "todo_id": f"TODO-PRODUCER-{index:03d}",
        "job_id": f"JOB-PRODUCER-{index:03d}",
        "project_id": "project-1",
        "playbook": "noop.yml",
        "queue": "batch",
        "work_type": "code",
        "risk_band": "low",
        "resource_profile": "cpu",
        "provider_class": "local",
        "exit_code": 0,
        "result_summary": f"private result {index}",
        "approval_required": False,
        "reversible": True,
        "retry_count": 0,
        "estimated_cost_microusd": 50_000,
        "latency_ms": 250,
        "required_evidence": True,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _recorder(store: RunBundleStore) -> DecisionOutcomeRecorder:
    return DecisionOutcomeRecorder(
        store,
        project_id="project-1",
        policy_digest=POLICY_DIGEST,
        correlation_key=CORRELATION_KEY,
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
            profile="review",
            model="gpt-6",
            request_parameters={},
            provider_revision=None,
        ),
        retention_days=30,
        max_total_bytes=4 * 1024 * 1024,
        scan_limit=64,
    )


def _run_id(recorder: DecisionOutcomeRecorder, record: SimpleNamespace) -> str:
    key = recorder.coordination_key(
        capture_id=f"return-review:{record.return_id}",
        root_task_id=record.todo_id,
        decision_kind=DecisionKind.REVIEW,
    )
    return f"decision-review-{key.removeprefix('decision-capture:')}"


def _create_coordination_tables(connection: Connection) -> None:
    """Create only the mature tables exercised by the hermetic lease seam."""
    cast(Table, ProjectModel.__table__).create(connection)
    cast(Table, BucketLeaseModel.__table__).create(connection)


@pytest.mark.asyncio
async def test_live_producer_evidence_becomes_fail_closed_zero_llm_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Capture, mine, approve, reuse, and exercise every closed fallback path."""
    monkeypatch.setattr(
        "general_ludd.event_loop.review_orchestration.datetime",
        _Clock,
    )
    applied: list[TaskDecision] = []

    async def _apply(
        decision: TaskDecision,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        applied.append(decision)

    monkeypatch.setattr(
        "general_ludd.review.decision_applier.apply_decision",
        _apply,
    )

    store = RunBundleStore(
        tmp_path / "replays",
        verification_keys={KEY_ID: REPLAY_KEY},
        active_key_id=KEY_ID,
    )
    recorder = _recorder(store)
    artifacts = DecisionArtifactStore(
        str(tmp_path / "artifacts"),
        key=ARTIFACT_KEY,
    )
    rollout = RolloutController(
        artifacts,
        AtomicGenerationStore(),
        rollout_key=ROLLOUT_KEY,
    )
    adapter = DecisionCodificationAdapter(
        bundle_reader=store,
        runtime=DecisionRuntime(artifacts, rollout),
        project_id="project-1",
        policy_digest=POLICY_DIGEST,
        decision_recorder=recorder,
    )
    reviewer = _Reviewer()
    audit_repo = AsyncMock()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'leases.sqlite3'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_create_coordination_tables)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            loop = EventLoop(
                reviewer=reviewer,
                session=session,
                todo_repo=AsyncMock(),
                audit_repo=audit_repo,
                decision_codification=adapter,
            )
            records = [_record(index) for index in range(32)]
            run_ids: list[str] = []
            for index, record in enumerate(records):
                _Clock.current = EVIDENCE_START + timedelta(
                    days=index // 8,
                    minutes=index,
                )
                await loop._review_in_process(record)
                await session.commit()
                run_ids.append(_run_id(recorder, record))

            assert reviewer.calls == 32
            assert all(decision.decision == "complete" for decision in applied)
            bundles = tuple(store.read_verified(run_id) for run_id in run_ids)
            assert all(bundle.manifest.integrity == "signed" for bundle in bundles)
            assert all(len(bundle.events) == 2 for bundle in bundles)
            serialized = canonical_replay_json(
                [
                    event.model_dump(mode="json", by_alias=True)
                    for event in bundles[0].events
                ]
            )
            assert records[0].return_id not in serialized
            assert records[0].todo_id not in serialized

            reviewer.decision = "failed"
            _Clock.current = EVIDENCE_START + timedelta(days=4)
            await loop._review_in_process(records[0])
            await session.commit()
            unchanged = store.read_verified(run_ids[0])
            assert unchanged.events[0].payload["decision"] == "approve"
            assert len(unchanged.events) == 2
            assert applied[-1].decision == "failed"
            reviewer.decision = "complete"

            analysis = adapter.analyze(
                tuple(run_ids),
                training_recipe_digest=RECIPE_DIGEST,
                dependency_lock_digest=LOCK_DIGEST,
                created_at=ACTIVE_AT,
                expires_at=ACTIVE_AT + timedelta(days=30),
                maximum_use_count=1_000,
                estimated_tokens_per_call=2_000,
            )
            assert analysis.events_eligible == 32
            assert len(analysis.candidates) == 1
            candidate = analysis.candidates[0]

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
                now=ACTIVE_AT,
                expires_at=ACTIVE_AT + timedelta(days=2),
                rollout_plan=("shadow", "active"),
                maximum_use_count=1_000,
            )
            rollout.install(candidate.bundle, receipt, expected_candidate_digest=None)
            active = approval.append_lifecycle(
                receipt,
                receipt_type=ReceiptType.PROMOTION,
                lifecycle_state=LifecycleState.ACTIVE,
                decision=ApprovalDecision.APPROVED,
                approver_identity="human-1",
                authorization_evidence_digest=SOURCE_DIGEST,
                now=ACTIVE_AT + timedelta(minutes=1),
            )
            rollout.promote(
                active,
                expected_candidate_digest=candidate.bundle.candidate_digest,
            )

            calls_before_reuse = reviewer.calls
            _Clock.current = ACTIVE_AT + timedelta(days=1)
            await loop._review_in_process(_record(100))
            await session.commit()
            assert reviewer.calls == calls_before_reuse
            assert applied[-1].decision == "complete"
            reuse_audit = json.loads(audit_repo.create.await_args.kwargs["details"])
            assert reuse_audit["decision_source"] == "codified"

            await loop._review_in_process(_record(101, queue="interactive"))
            await session.commit()
            assert reviewer.calls == calls_before_reuse + 1
            mismatch_audit = json.loads(audit_repo.create.await_args.kwargs["details"])
            assert mismatch_audit["decision_source"] == "agent_fallback"
            assert mismatch_audit["fallback_reason"] == "scope_miss"

            _Clock.current = ACTIVE_AT + timedelta(days=3)
            await loop._review_in_process(_record(102))
            await session.commit()
            assert reviewer.calls == calls_before_reuse + 2
            expired_audit = json.loads(audit_repo.create.await_args.kwargs["details"])
            assert expired_audit["fallback_reason"] == "expired"

            disabled = build_configured_adapter(
                DecisionCodificationConfig(),
                environ={},
            )
            assert disabled is None
            disabled_loop = EventLoop(
                reviewer=reviewer,
                session=session,
                todo_repo=AsyncMock(),
                audit_repo=audit_repo,
                decision_codification=disabled,
            )
            await disabled_loop._review_in_process(_record(103))
            await session.commit()
            assert reviewer.calls == calls_before_reuse + 3
            disabled_audit = json.loads(audit_repo.create.await_args.kwargs["details"])
            assert "decision_source" not in disabled_audit
    finally:
        await engine.dispose()
