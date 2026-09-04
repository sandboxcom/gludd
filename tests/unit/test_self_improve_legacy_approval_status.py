"""Durable non-runnable approval state for legacy self-improvement artifacts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TodoModel, UTCDateTime
from general_ludd.db.repository import ConcurrencyError, TodoRepository
from general_ludd.event_loop.loop import EventLoop
from general_ludd.routers import self_improve as self_improve_router
from general_ludd.schemas.todo import Todo, TodoStatus, validate_transition
from general_ludd.self_improve import staging as staging_module
from general_ludd.self_improve.approval import (
    ApprovalError,
    SelfImproveApprovalManager,
)
from general_ludd.self_improve.managed_runner import TaskSpec
from general_ludd.self_improve.staging import (
    MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
    ManagedSelfImproveArtifactKind,
)


def _config_artifact(*, content: str = "enabled: true\n") -> str:
    return json.dumps(
        {
            "capability_required": "config_write",
            "change_content": content,
            "kind": "config",
            "reason": "approved config update",
            "target_paths": ["config/approved.yml"],
        }
    )


def _non_config_artifact() -> str:
    return json.dumps(
        {
            "description": "approved implementation",
            "kind": "code",
            "project_id": "project-1",
            "schema_version": 1,
            "title": "approved title",
            "worktree_path": "/workspace/repo/worktrees/approved",
        }
    )


def _legacy_row(artifact: str, *, todo_id: str = "TODO-LEGACY") -> SimpleNamespace:
    return SimpleNamespace(
        todo_id=todo_id,
        project_id=None,
        status=TodoStatus.APPROVAL_REQUIRED.value,
        work_type="self_improve",
        approval_policy="none",
        plan_artifact=artifact,
        approved_artifact_digest=None,
        version=1,
    )


def test_approved_is_non_runnable_and_has_only_explicit_consumption_edges() -> None:
    assert TodoStatus.APPROVED.value == "approved"
    assert validate_transition(TodoStatus.APPROVAL_REQUIRED, TodoStatus.APPROVED)
    assert validate_transition(TodoStatus.APPROVED, TodoStatus.ACTIVE)
    assert validate_transition(TodoStatus.APPROVED, TodoStatus.CANCELLED)
    assert not validate_transition(TodoStatus.APPROVED, TodoStatus.QUEUED)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("title", "   ", "field must not be empty"),
        ("queue", "", "field must not be empty"),
        ("confidence", -0.01, "confidence must be between"),
        ("confidence", 1.01, "confidence must be between"),
        ("priority", -1, "priority must be non-negative"),
        ("priority", 1001, "priority must not exceed"),
        ("version", 0, "version must be at least 1"),
        ("run_count", -1, "run_count must be non-negative"),
        ("max_runs", 0, "max_runs must be at least 1"),
        ("cron", "0 0 * *", "cron must be a 5-field expression"),
    ],
)
def test_todo_schema_rejects_every_invalid_lifecycle_boundary(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = {"title": "approval", field: value}
    with pytest.raises(ValidationError, match=message):
        Todo(**payload)


def test_todo_schema_rejects_non_text_required_fields() -> None:
    with pytest.raises(ValidationError):
        Todo(title=object())


def test_todo_schema_accepts_nullable_and_normalized_lifecycle_values() -> None:
    todo = Todo(
        title="  approval  ",
        queue="  manual  ",
        confidence=None,
        priority=1000,
        version=1,
        run_count=0,
        max_runs=None,
        cron="   ",
        approved_artifact_digest="a" * 64,
    )

    assert todo.title == "approval"
    assert todo.queue == "manual"
    assert todo.cron is None
    assert todo.approved_artifact_digest == "a" * 64


def test_todo_schema_accepts_valid_cron_and_complete_timestamp() -> None:
    completed_at = datetime.now(UTC)
    todo = Todo(
        title="approval",
        status=TodoStatus.COMPLETE,
        completed_at=completed_at,
        confidence=0.5,
        max_runs=1,
        cron="0 0 * * *",
    )

    assert todo.completed_at == completed_at
    assert todo.cron == "0 0 * * *"


def test_todo_schema_accepts_explicit_none_cron() -> None:
    todo = Todo(title="approval", cron=None)

    assert todo.cron is None


def test_utc_datetime_normalizes_naive_bind_and_aware_result() -> None:
    codec = UTCDateTime()

    bound = codec.process_bind_param(datetime(2026, 1, 1), None)
    restored = codec.process_result_value(datetime(2026, 1, 1, tzinfo=UTC), None)

    assert bound is not None
    assert bound.tzinfo is UTC
    assert restored is not None
    assert restored.tzinfo is UTC


def test_staging_bounded_text_rejects_oversized_utf8() -> None:
    with (
        patch.object(staging_module, "_MAX_TEXT_BYTES", 3),
        pytest.raises(ValueError, match="value exceeds the bounded text limit"),
    ):
        staging_module._bounded_text("four", "value")


def test_managed_request_serialization_rejects_oversized_artifact() -> None:
    request = staging_module.ManagedSelfImprovePlanRequest(
        project_id="project-1",
        source="runtime",
        gap_type="quality",
        source_file="src/general_ludd/example.py",
        title="bounded request",
        work_type="code",
        task_type="",
        blocker_kind="",
        incident_count=0,
        recent_todo_ids=(),
        task=TaskSpec(
            task_id="S1",
            objective="exercise the approved request boundary",
            canonical_make_commands=("make test-unit",),
        ),
    )

    with (
        patch.object(staging_module, "_MAX_REQUEST_BYTES", 1),
        pytest.raises(
            ValueError,
            match="managed plan request exceeds the bounded artifact limit",
        ),
    ):
        request.to_json()


def test_todo_schema_rejects_completion_timestamp_before_completion() -> None:
    with pytest.raises(ValidationError, match="completed_at can only be set"):
        Todo(
            title="approval",
            status=TodoStatus.APPROVED,
            completed_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_legacy_approval_records_artifact_digest_then_enters_approved() -> None:
    artifact = _config_artifact()
    row = _legacy_row(artifact)
    digested = SimpleNamespace(
        **{
            **vars(row),
            "approved_artifact_digest": hashlib.sha256(artifact.encode()).hexdigest(),
            "version": 2,
        }
    )
    approved = SimpleNamespace(
        **{
            **vars(digested),
            "status": TodoStatus.APPROVED.value,
            "version": 3,
        }
    )
    store = AsyncMock()
    store.get_by_id.return_value = row
    store.update.return_value = digested
    store.transition.return_value = approved

    result = await SelfImproveApprovalManager().approve_by_id(store, row.todo_id)

    assert result is approved
    store.update.assert_awaited_once_with(
        row.todo_id,
        {"approved_artifact_digest": digested.approved_artifact_digest},
        expected_version=1,
        project_id=None,
    )
    store.transition.assert_awaited_once_with(
        row.todo_id,
        TodoStatus.APPROVED,
        expected_version=2,
        project_id=None,
    )


@pytest.mark.asyncio
async def test_unknown_legacy_artifact_cannot_be_approved_or_queued() -> None:
    row = _legacy_row('{"kind":"unknown"}')
    store = AsyncMock()
    store.get_by_id.return_value = row

    with pytest.raises(ApprovalError, match="legacy self-improve artifact"):
        await SelfImproveApprovalManager().approve_by_id(store, row.todo_id)

    store.update.assert_not_awaited()
    store.transition.assert_not_awaited()


def test_in_memory_approval_translates_digest_failure() -> None:
    todo = Todo(
        title="legacy config",
        todo_id="TODO-DIGEST-FAIL",
        status=TodoStatus.APPROVAL_REQUIRED,
        approval_policy="none",
        plan_artifact=_config_artifact(),
    )

    with patch(
        "general_ludd.self_improve.approval.self_improve_artifact_digest",
        side_effect=ValueError("oversized"),
    ), pytest.raises(ApprovalError, match="approval artifact is invalid"):
        SelfImproveApprovalManager().approve(todo)

    assert todo.status is TodoStatus.APPROVAL_REQUIRED


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_in_memory_release_rejects_a_state_machine_denial(action: str) -> None:
    todo = Todo(
        title="legacy config",
        todo_id="TODO-STATE-DENIAL",
        status=TodoStatus.APPROVAL_REQUIRED,
        approval_policy="none",
        plan_artifact=_config_artifact(),
    )
    manager = SelfImproveApprovalManager()

    with patch(
        "general_ludd.self_improve.approval.validate_transition",
        return_value=False,
    ), pytest.raises(ApprovalError, match="not a valid transition"):
        getattr(manager, action)(todo)

    assert todo.status is TodoStatus.APPROVAL_REQUIRED


def test_managed_release_validator_rejects_non_managed_policy() -> None:
    row = _legacy_row(_config_artifact())

    with pytest.raises(ApprovalError, match="managed approval policy"):
        SelfImproveApprovalManager()._validate_managed_release(row)


@pytest.mark.parametrize(
    ("todo_id", "project_id"),
    [(None, "project-1"), ("TODO-MANAGED", None)],
)
def test_managed_release_rejects_malformed_row_identity(
    todo_id: object,
    project_id: object,
) -> None:
    row = SimpleNamespace(
        todo_id=todo_id,
        project_id=project_id,
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact="canonical-plan-placeholder",
    )

    with patch(
        "general_ludd.self_improve.approval.classify_self_improve_artifact",
        return_value=ManagedSelfImproveArtifactKind.MANAGED_APPROVED_PLAN,
    ), pytest.raises(ApprovalError, match="row identity is malformed"):
        SelfImproveApprovalManager()._validate_managed_release(row)


def test_managed_release_requires_canonical_repository_resolver() -> None:
    row = SimpleNamespace(
        todo_id="TODO-MANAGED",
        project_id="project-1",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact="canonical-plan-placeholder",
    )

    with patch(
        "general_ludd.self_improve.approval.classify_self_improve_artifact",
        return_value=ManagedSelfImproveArtifactKind.MANAGED_APPROVED_PLAN,
    ), pytest.raises(ApprovalError, match="repository resolver is unavailable"):
        SelfImproveApprovalManager()._validate_managed_release(row)


def test_managed_release_preserves_typed_binding_error(tmp_path: Path) -> None:
    row = SimpleNamespace(
        todo_id="TODO-MANAGED",
        project_id="project-1",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact="canonical-plan-placeholder",
    )
    binding_error = ApprovalError("typed binding rejection")
    manager = SelfImproveApprovalManager(
        managed_repo_resolver=lambda _project_id: tmp_path
    )

    with patch(
        "general_ludd.self_improve.approval.classify_self_improve_artifact",
        return_value=ManagedSelfImproveArtifactKind.MANAGED_APPROVED_PLAN,
    ), patch(
        "general_ludd.self_improve.approval.validate_bound_managed_plan",
        side_effect=binding_error,
    ), pytest.raises(ApprovalError) as exc_info:
        manager._validate_managed_release(row)

    assert exc_info.value is binding_error


def test_bound_artifact_translates_digest_failure_before_apply() -> None:
    row = _legacy_row(_config_artifact())
    row.status = TodoStatus.APPROVED.value
    row.approved_artifact_digest = hashlib.sha256(
        row.plan_artifact.encode("utf-8")
    ).hexdigest()

    with patch.object(
        self_improve_router,
        "self_improve_artifact_digest",
        side_effect=ValueError("unreadable artifact"),
    ), pytest.raises(HTTPException) as exc_info:
        self_improve_router._require_bound_legacy_artifact(
            row,
            expected_kind=ManagedSelfImproveArtifactKind.LEGACY_CONFIG,
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_persisted_approval_translates_digest_failure() -> None:
    row = _legacy_row(_config_artifact())
    store = AsyncMock()
    store.get_by_id.return_value = row

    with patch(
        "general_ludd.self_improve.approval.self_improve_artifact_digest",
        side_effect=ValueError("oversized"),
    ), pytest.raises(ApprovalError, match="approval artifact is invalid"):
        await SelfImproveApprovalManager().approve_by_id(store, row.todo_id)

    store.update.assert_not_awaited()
    store.transition.assert_not_awaited()


@pytest_asyncio.fixture
async def async_session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_queue_recovery_zero_limit_avoids_database_work() -> None:
    session = AsyncMock()
    repo = TodoRepository(cast(AsyncSession, session))

    assert await repo.recover_queued_legacy_self_improve(limit=-1) == []
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_queue_recovery_cas_loss_refreshes_without_event() -> None:
    candidate = SimpleNamespace(
        id=7,
        todo_id="TODO-RECOVERY-RACE",
        project_id="project-1",
        status=TodoStatus.QUEUED.value,
        work_type="self_improve",
        approval_policy="none",
        plan_artifact=_config_artifact(),
        version=2,
    )
    candidates = MagicMock()
    candidates.scalars.return_value.all.return_value = [candidate]
    lost_cas = MagicMock(rowcount=0)
    session = AsyncMock()
    session.execute.side_effect = [candidates, lost_cas]
    repo = TodoRepository(cast(AsyncSession, session), project_id="project-1")

    recovered = await repo.recover_queued_legacy_self_improve(limit=5000)

    assert recovered == []
    session.refresh.assert_awaited_once_with(candidate)
    session.add.assert_not_called()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_queue_recovery_quarantines_digest_failure() -> None:
    candidate = SimpleNamespace(
        id=8,
        todo_id="TODO-RECOVERY-DIGEST-FAIL",
        project_id="project-1",
        status=TodoStatus.QUEUED.value,
        work_type="self_improve",
        approval_policy="none",
        plan_artifact=_config_artifact(),
        approved_artifact_digest=None,
        manual_hold_reason=None,
        version=2,
    )
    candidates = MagicMock()
    candidates.scalars.return_value.all.return_value = [candidate]
    won_cas = MagicMock(rowcount=1)
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [candidates, won_cas]
    repo = TodoRepository(cast(AsyncSession, session), project_id="project-1")

    with patch(
        "general_ludd.schemas.self_improve_artifact.self_improve_artifact_digest",
        side_effect=ValueError("digest unavailable"),
    ):
        recovered = await repo.recover_queued_legacy_self_improve(limit=1)

    assert recovered == [candidate]
    assert candidate.status == TodoStatus.MANUAL_HOLD.value
    assert candidate.approved_artifact_digest is None
    assert "does not match" in candidate.manual_hold_reason
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_moves_only_exact_legacy_queued_rows_out_of_scheduler(
    async_session: AsyncSession,
) -> None:
    repo = TodoRepository(async_session)
    legacy = await repo.create(
        {
            "todo_id": "TODO-LEGACY",
            "title": "legacy config",
            "status": TodoStatus.QUEUED.value,
            "work_type": "self_improve",
            "approval_policy": "none",
            "plan_artifact": _config_artifact(),
        }
    )
    malformed = await repo.create(
        {
            "todo_id": "TODO-MALFORMED",
            "title": "malformed legacy",
            "status": TodoStatus.QUEUED.value,
            "work_type": "self_improve",
            "approval_policy": "none",
            "plan_artifact": "not-json",
        }
    )
    ordinary = await repo.create(
        {
            "todo_id": "TODO-CODE",
            "title": "ordinary code",
            "status": TodoStatus.QUEUED.value,
            "work_type": "code",
        }
    )
    await async_session.commit()

    recovered = await repo.recover_queued_legacy_self_improve(limit=10)
    claimed = await repo.claim_runnable(limit=10)

    assert [row.todo_id for row in recovered] == [legacy.todo_id, malformed.todo_id]
    assert legacy.status == TodoStatus.APPROVED.value
    assert malformed.status == TodoStatus.MANUAL_HOLD.value
    assert malformed.manual_hold_reason == (
        "Quarantined queued self-improvement artifact that does not match an "
        "approved executable schema"
    )
    assert [row.todo_id for row in claimed] == [ordinary.todo_id]


@pytest.mark.asyncio
async def test_recovery_is_bounded(async_session: AsyncSession) -> None:
    repo = TodoRepository(async_session)
    for index in range(3):
        await repo.create(
            {
                "todo_id": f"TODO-LEGACY-{index}",
                "title": f"legacy {index}",
                "status": TodoStatus.QUEUED.value,
                "work_type": "self_improve",
                "approval_policy": "none",
                "plan_artifact": _config_artifact(content=f"value: {index}\n"),
            }
        )
    await async_session.commit()

    recovered = await repo.recover_queued_legacy_self_improve(limit=2)

    assert len(recovered) == 2


def test_migration_045_adds_digest_and_recovers_legacy_rows() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/045_add_legacy_self_improve_approved_status.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert 'revision: str = "045"' in source
    assert 'down_revision: str | None = "044"' in source
    assert '"approved_artifact_digest"' in source
    assert "'queued'" in source
    assert "'approved'" in source
    assert '"managed_self_improve_plan"' in source


def _load_migration_045() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/045_add_legacy_self_improve_approved_status.py"
    )
    spec = importlib.util.spec_from_file_location("legacy_approval_migration_045", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [
        (None, False),
        ("[]", False),
        ('{"kind":"unknown"}', False),
        (_config_artifact(), True),
        (_non_config_artifact(), True),
    ],
)
def test_migration_045_legacy_classifier_is_exact_and_fail_closed(
    artifact: object,
    expected: bool,
) -> None:
    module = _load_migration_045()

    assert module._legacy_artifact_is_exact(artifact) is expected


def test_migration_045_recovers_exact_rows_and_quarantines_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    todos = sa.Table(
        "todos",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("work_type", sa.String(32), nullable=False),
        sa.Column("approval_policy", sa.String(32), nullable=False),
        sa.Column("plan_artifact", sa.Text),
        sa.Column("manual_hold_reason", sa.Text),
        sa.Column("version", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)
    managed_artifact = '{"managed":true}'
    with engine.begin() as connection:
        connection.execute(
            todos.insert(),
            [
                {
                    "id": 1,
                    "status": "queued",
                    "work_type": "self_improve",
                    "approval_policy": "none",
                    "plan_artifact": _config_artifact(),
                    "version": 1,
                },
                {
                    "id": 2,
                    "status": "queued",
                    "work_type": "self_improve",
                    "approval_policy": "none",
                    "plan_artifact": "not-json",
                    "version": 1,
                },
                {
                    "id": 3,
                    "status": "queued",
                    "work_type": "self_improve",
                    "approval_policy": "managed_self_improve_plan",
                    "plan_artifact": managed_artifact,
                    "version": 1,
                },
                {
                    "id": 4,
                    "status": "queued",
                    "work_type": "self_improve",
                    "approval_policy": "managed_self_improve_plan",
                    "plan_artifact": None,
                    "version": 1,
                },
            ],
        )
        module = _load_migration_045()
        monkeypatch.setattr(
            module,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        monkeypatch.setattr(module, "_MIGRATION_BATCH_SIZE", 2)

        module.upgrade()

        upgraded = {
            row.id: row
            for row in connection.execute(
                sa.text(
                    "SELECT id, status, approved_artifact_digest, manual_hold_reason "
                    "FROM todos ORDER BY id"
                )
            ).mappings()
        }
        assert upgraded[1].status == "approved"
        assert upgraded[1].approved_artifact_digest == hashlib.sha256(
            _config_artifact().encode("utf-8")
        ).hexdigest()
        assert upgraded[2].status == "manual_hold"
        assert "quarantined" in upgraded[2].manual_hold_reason
        assert upgraded[3].status == "queued"
        assert upgraded[3].approved_artifact_digest == hashlib.sha256(
            managed_artifact.encode("utf-8")
        ).hexdigest()
        assert upgraded[4].status == "queued"
        assert upgraded[4].approved_artifact_digest is None

        module.downgrade()

        downgraded = connection.execute(
            sa.text("SELECT id, status FROM todos ORDER BY id")
        ).mappings().all()
        assert [row.status for row in downgraded] == [
            "approval_required",
            "approval_required",
            "queued",
            "queued",
        ]
        assert "approved_artifact_digest" not in {
            column["name"] for column in sa.inspect(connection).get_columns("todos")
        }
    engine.dispose()


@pytest.mark.asyncio
async def test_event_loop_recovers_legacy_queue_before_claiming() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    repo = AsyncMock()
    repo.count_active.return_value = 0
    repo.recover_queued_legacy_self_improve.return_value = [
        SimpleNamespace(todo_id="TODO-LEGACY")
    ]
    repo.claim_runnable.return_value = []
    loop = EventLoop(
        worker_base_url="http://worker.invalid",
        config={"tick_interval": 1.0},
        session=session,
        http_client=AsyncMock(),
        todo_repo=repo,
        task_return_repo=AsyncMock(),
    )

    await loop._phase_claim_runnable_todos()

    repo.recover_queued_legacy_self_improve.assert_awaited_once_with(
        limit=10,
        project_id=None,
    )
    repo.claim_runnable.assert_awaited_once_with(limit=10, project_id=None)
    assert loop._tick_state["recovered_legacy_self_improve"] == 1


@pytest.mark.asyncio
async def test_event_loop_recovery_failure_remains_fail_closed() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    repo = AsyncMock()
    repo.count_active.return_value = 0
    repo.recover_queued_legacy_self_improve.side_effect = RuntimeError(
        "recovery unavailable"
    )
    repo.claim_runnable.return_value = []
    loop = EventLoop(
        worker_base_url="http://worker.invalid",
        config={"tick_interval": 1.0},
        session=session,
        http_client=AsyncMock(),
        todo_repo=repo,
        task_return_repo=AsyncMock(),
    )

    await loop._phase_claim_runnable_todos()

    assert loop._tick_state["recovered_legacy_self_improve"] == 0
    repo.claim_runnable.assert_awaited_once_with(limit=10, project_id=None)


@pytest.mark.asyncio
async def test_event_loop_recovers_legacy_rows_before_zero_capacity_return() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    repo = AsyncMock()
    repo.count_active.return_value = 1
    repo.recover_queued_legacy_self_improve.return_value = []
    floor_controller = MagicMock()
    floor_controller.get_max_active.return_value = 1
    loop = EventLoop(
        worker_base_url="http://worker.invalid",
        config={"tick_interval": 1.0},
        session=session,
        http_client=AsyncMock(),
        todo_repo=repo,
        task_return_repo=AsyncMock(),
        floor_controller=floor_controller,
    )

    await loop._phase_claim_runnable_todos()

    repo.recover_queued_legacy_self_improve.assert_awaited_once_with(
        limit=10,
        project_id=None,
    )
    repo.claim_runnable.assert_not_awaited()
    assert loop._tick_state["claimed_todos"] == []


@pytest.mark.asyncio
async def test_reaper_terminally_fails_interrupted_legacy_manual_apply() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    legacy = SimpleNamespace(
        todo_id="TODO-INTERRUPTED-LEGACY",
        status=TodoStatus.ACTIVE.value,
        queue="self-improve",
        updated_at=datetime(2000, 1, 1, tzinfo=UTC),
        version=5,
        work_type="self_improve",
        approval_policy="none",
    )
    candidates = MagicMock()
    candidates.scalars.return_value.all.return_value = [legacy]
    live_leases = MagicMock()
    live_leases.scalars.return_value.all.return_value = []
    session.execute.side_effect = [candidates, live_leases]
    repo = AsyncMock()
    loop = EventLoop(
        worker_base_url="http://worker.invalid",
        config={"tick_interval": 1.0},
        session=session,
        http_client=AsyncMock(),
        todo_repo=repo,
        task_return_repo=AsyncMock(),
    )
    loop._active_session = session

    await loop._reap_stuck_todos()

    repo.transition.assert_awaited_once_with(
        legacy.todo_id,
        TodoStatus.FAILED,
        legacy.version,
    )
    assert legacy.todo_id not in loop._tick_state.get("reaped_todo_ids", set())


async def _approve_config(
    session: AsyncSession,
    artifact: str,
    *,
    todo_id: str,
) -> object:
    repo = TodoRepository(session)
    created = await repo.create(
        {
            "todo_id": todo_id,
            "title": "legacy config approval",
            "status": TodoStatus.APPROVAL_REQUIRED.value,
            "work_type": "self_improve",
            "approval_policy": "none",
            "plan_artifact": artifact,
        }
    )
    await session.commit()
    approved = await SelfImproveApprovalManager().approve_by_id(repo, created.todo_id)
    await session.commit()
    return approved


@pytest.mark.asyncio
async def test_config_apply_rejects_post_approval_artifact_bait_and_switch(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    from general_ludd.routers.self_improve import _apply_approved_config_change

    approved = await _approve_config(
        async_session,
        _config_artifact(content="safe: true\n"),
        todo_id="TODO-TAMPER",
    )
    await async_session.execute(
        sa.update(TodoModel)
        .where(TodoModel.todo_id == approved.todo_id)
        .values(plan_artifact=_config_artifact(content="attacker: true\n"))
    )
    await async_session.commit()
    factory = sessionmaker(
        async_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    with pytest.raises(HTTPException, match="changed since human approval"):
        await _apply_approved_config_change(
            factory,
            approved.todo_id,
            workspace_root=tmp_path,
        )

    assert not (tmp_path / "config/approved.yml").exists()


@pytest.mark.asyncio
async def test_repository_freezes_approval_artifact_fields_after_release(
    async_session: AsyncSession,
) -> None:
    approved = await _approve_config(
        async_session,
        _config_artifact(),
        todo_id="TODO-FROZEN",
    )
    repo = TodoRepository(async_session)

    with pytest.raises(ValueError, match="immutable after human approval"):
        await repo.update(
            approved.todo_id,
            {
                "plan_artifact": _config_artifact(content="changed: true\n"),
                "approved_artifact_digest": "0" * 64,
            },
            expected_version=approved.version,
        )


@pytest.mark.asyncio
async def test_config_apply_consumes_approved_to_terminal_failure(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    from general_ludd.routers.self_improve import _apply_approved_config_change

    artifact = json.dumps(
        {
            "capability_required": "config_write",
            "change_content": "unsafe: true\n",
            "kind": "config",
            "reason": "must be denied",
            "target_paths": ["secrets/config.yml"],
        }
    )
    approved = await _approve_config(
        async_session,
        artifact,
        todo_id="TODO-DENIED",
    )
    factory = sessionmaker(
        async_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    result = await _apply_approved_config_change(
        factory,
        approved.todo_id,
        workspace_root=tmp_path,
    )

    assert result["status"] == "denied"
    async with factory() as verification_session:
        consumed = await TodoRepository(verification_session).get_by_id(approved.todo_id)
        assert consumed is not None
        assert consumed.status == TodoStatus.FAILED.value


@pytest.mark.asyncio
async def test_config_apply_exception_consumes_approval_to_failed(
    async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import general_ludd.routers.self_improve as self_improve_router

    approved = await _approve_config(
        async_session,
        _config_artifact(),
        todo_id="TODO-CONFIG-EXCEPTION",
    )
    factory = sessionmaker(
        async_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    class ExplodingApplier:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def apply(self, *_args: object) -> None:
            raise RuntimeError("config apply failed")

    monkeypatch.setattr(self_improve_router, "UpdateApplier", ExplodingApplier)

    with pytest.raises(RuntimeError, match="config apply failed"):
        await self_improve_router._apply_approved_config_change(
            factory,
            approved.todo_id,
            workspace_root=tmp_path,
        )

    async with factory() as verification_session:
        consumed = await TodoRepository(verification_session).get_by_id(approved.todo_id)
        assert consumed is not None
        assert consumed.status == TodoStatus.FAILED.value


@pytest.mark.asyncio
async def test_config_apply_consumes_approval_exactly_once(
    async_session: AsyncSession,
    tmp_path: Path,
) -> None:
    from general_ludd.routers.self_improve import _apply_approved_config_change

    approved = await _approve_config(
        async_session,
        _config_artifact(content="safe: true\n"),
        todo_id="TODO-ONE-SHOT",
    )
    factory = sessionmaker(
        async_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    first = await _apply_approved_config_change(
        factory,
        approved.todo_id,
        workspace_root=tmp_path,
    )
    with pytest.raises(HTTPException, match="is not released") as replay:
        await _apply_approved_config_change(
            factory,
            approved.todo_id,
            workspace_root=tmp_path,
        )

    assert first["status"] == "applied"
    assert replay.value.status_code == 409
    assert (tmp_path / "config/approved.yml").read_text(encoding="utf-8") == (
        "safe: true\n"
    )


@pytest.mark.asyncio
async def test_config_apply_reports_concurrent_claim_as_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import general_ludd.routers.self_improve as self_improve_router

    artifact = _config_artifact()
    todo = SimpleNamespace(
        todo_id="TODO-RACE",
        work_type="self_improve",
        status=TodoStatus.APPROVED.value,
        version=3,
        approval_policy="none",
        plan_artifact=artifact,
        approved_artifact_digest=hashlib.sha256(artifact.encode("utf-8")).hexdigest(),
    )

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object | None,
        ) -> None:
            return None

        async def commit(self) -> None:
            return None

    class Repository:
        def __init__(self, _session: object) -> None:
            pass

        async def get_by_id(self, _todo_id: str) -> SimpleNamespace:
            return todo

        async def transition(self, *_args: object, **_kwargs: object) -> None:
            raise ConcurrencyError("lost approval claim")

    monkeypatch.setattr(self_improve_router, "TodoRepository", Repository)

    with pytest.raises(HTTPException, match="already being applied") as conflict:
        await self_improve_router._apply_approved_config_change(
            cast(async_sessionmaker[AsyncSession], lambda: Session()),
            todo.todo_id,
            workspace_root=tmp_path,
        )

    assert conflict.value.status_code == 409
    assert not (tmp_path / "config/approved.yml").exists()


@pytest.mark.asyncio
async def test_non_config_apply_reports_concurrent_claim_before_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import general_ludd.routers.self_improve as self_improve_router

    project_root = tmp_path / "project"
    worktree = project_root / "repo/worktrees/approved"
    worktree.mkdir(parents=True)
    artifact = json.dumps(
        {
            "description": "approved description",
            "kind": "code",
            "project_id": "project-1",
            "schema_version": 1,
            "title": "approved title",
            "worktree_path": str(worktree.resolve()),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    todo = SimpleNamespace(
        todo_id="TODO-NONCONFIG-RACE",
        work_type="self_improve",
        status=TodoStatus.APPROVED.value,
        version=2,
        project_id="project-1",
        approval_policy="none",
        plan_artifact=artifact,
        approved_artifact_digest=hashlib.sha256(artifact.encode("utf-8")).hexdigest(),
    )

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object | None,
        ) -> None:
            return None

        async def commit(self) -> None:
            return None

    class Repository:
        def __init__(self, _session: object) -> None:
            pass

        async def get_by_id(self, _todo_id: str) -> SimpleNamespace:
            return todo

        async def transition(self, *_args: object, **_kwargs: object) -> None:
            raise ConcurrencyError("lost approval claim")

    app = FastAPI()
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            workspace_path=str(project_root)
        )
    )
    monkeypatch.setattr(self_improve_router, "TodoRepository", Repository)

    with pytest.raises(HTTPException, match="already being applied") as conflict:
        await self_improve_router._apply_approved_non_config_change(
            app,
            cast(async_sessionmaker[AsyncSession], lambda: Session()),
            todo.todo_id,
        )

    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_non_config_apply_failure_consumes_approval_to_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import general_ludd.reload.self_improve as reload_self_improve
    import general_ludd.routers.self_improve as self_improve_router

    project_root = tmp_path / "project"
    worktree = project_root / "repo/worktrees/approved"
    worktree.mkdir(parents=True)
    artifact = json.dumps(
        {
            "description": "approved description",
            "kind": "code",
            "project_id": "project-1",
            "schema_version": 1,
            "title": "approved title",
            "worktree_path": str(worktree.resolve()),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    todo = SimpleNamespace(
        todo_id="TODO-NONCONFIG-FAIL",
        work_type="self_improve",
        status=TodoStatus.APPROVED.value,
        version=2,
        project_id="project-1",
        approval_policy="none",
        plan_artifact=artifact,
        approved_artifact_digest=hashlib.sha256(artifact.encode("utf-8")).hexdigest(),
    )

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object | None,
        ) -> None:
            return None

        async def commit(self) -> None:
            return None

    class Repository:
        def __init__(self, _session: object) -> None:
            pass

        @classmethod
        def scoped(cls, _session: object, project_id: str) -> Repository:
            assert project_id == "project-1"
            return cls(_session)

        async def get_by_id(self, _todo_id: str) -> SimpleNamespace:
            return todo

        async def transition(
            self,
            _todo_id: str,
            new_status: TodoStatus,
            *,
            expected_version: int,
            project_id: str | None = None,
        ) -> SimpleNamespace:
            assert project_id in {None, "project-1"}
            assert expected_version == todo.version
            todo.status = new_status.value
            todo.version += 1
            return todo

    class Workflow:
        def validate_improvement(self, _worktree_path: str) -> None:
            raise RuntimeError("workflow failed")

    app = FastAPI()
    app.state._project_manager = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            workspace_path=str(project_root)
        )
    )
    monkeypatch.setattr(self_improve_router, "TodoRepository", Repository)
    monkeypatch.setattr(reload_self_improve, "SelfImprovementWorkflow", Workflow)

    with pytest.raises(RuntimeError, match="workflow failed"):
        await self_improve_router._apply_approved_non_config_change(
            app,
            cast(async_sessionmaker[AsyncSession], lambda: Session()),
            todo.todo_id,
        )

    assert todo.status == TodoStatus.FAILED.value
