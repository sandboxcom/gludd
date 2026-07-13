"""Tests for D.21 — Remediation idempotency guard in RemediationDispatcher.

Covers:
  - Idempotent replay: same key returns skipped action without re-acting.
  - Duplicate idempotency-key → no second todo/retry/human-todo created.
  - Null idempotency_key → acts normally.
  - Different keys → each acts independently.
  - Null remediation_repo → guard is bypassed (no crash).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, RemediationActionModel, TodoModel
from general_ludd.db.repository import (
    HumanTodoRepository,
    RemediationActionRepository,
    TodoRepository,
)
from general_ludd.remediation.blocker_detector import BlockedTask, RemediationConfig
from general_ludd.remediation.dispatcher import RemediationActionKind, RemediationDispatcher


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


@dataclass
class _FakeDetector:
    config: RemediationConfig


def _make_blocked(**overrides) -> BlockedTask:
    defaults = dict(
        todo_id="TODO-ID-1",
        blocker_kind="resource_contention",
        remediation="dispatch_agent",
        task_type="code",
    )
    defaults.update(overrides)
    return BlockedTask(
        todo_id=defaults["todo_id"],
        project_id=None,
        blocked_at=datetime.now(UTC) - timedelta(hours=10),
        blocked_duration_seconds=36000,
        blocker_kind=defaults["blocker_kind"],
        blocker_summary="test idempotency",
        suggested_remediation=defaults["remediation"],
        task_type=defaults["task_type"],
    )


def _make_dispatcher(session: AsyncSession) -> RemediationDispatcher:
    return RemediationDispatcher(
        detector=_FakeDetector(config=RemediationConfig()),
        todo_repo=TodoRepository(session),
        human_todo_repo=HumanTodoRepository(session),
        remediation_repo=RemediationActionRepository(session),
    )


class TestIdempotencyKeyReplay:
    @pytest.mark.asyncio
    async def test_same_key_returns_idempotent_replay_without_acting(
        self, async_session: AsyncSession
    ):
        todo_repo = TodoRepository(async_session)
        original = await todo_repo.create(
            {
                "todo_id": "TODO-ORG-REPLAY",
                "title": "original task",
                "description": "pre-blocked",
                "queue": "core",
                "work_type": "infra",
                "status": "blocked",
            }
        )
        await async_session.flush()
        dispatcher = _make_dispatcher(async_session)
        blocked = _make_blocked(
            todo_id=original.todo_id, remediation="dispatch_agent"
        )

        action1 = await dispatcher.remediate(
            blocked, idempotency_key="key-abc-123"
        )
        assert action1.kind == RemediationActionKind.DISPATCH_AGENT
        assert action1.ok

        action2 = await dispatcher.remediate(
            blocked, idempotency_key="key-abc-123"
        )
        assert action2.kind == RemediationActionKind.NO_ACTION
        assert action2.ok
        assert action2.detail.get("idempotent_replay") is True

    @pytest.mark.asyncio
    async def test_duplicate_idempotency_key_does_not_create_second_todo(
        self, async_session: AsyncSession
    ):
        todo_repo = TodoRepository(async_session)
        original = await todo_repo.create(
            {
                "todo_id": "TODO-ORG-NODUP",
                "title": "original task",
                "description": "pre-blocked",
                "queue": "core",
                "work_type": "infra",
                "status": "blocked",
            }
        )
        await async_session.flush()
        dispatcher = _make_dispatcher(async_session)
        blocked = _make_blocked(
            todo_id=original.todo_id, remediation="dispatch_agent"
        )

        await dispatcher.remediate(blocked, idempotency_key="key-nodup-1")
        await dispatcher.remediate(blocked, idempotency_key="key-nodup-1")

        children = (
            (
                await async_session.execute(
                    select(TodoModel).where(
                        TodoModel.parent_todo_id == original.todo_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(children) == 1

    @pytest.mark.asyncio
    async def test_same_key_with_schedule_retry_is_idempotent(
        self, async_session: AsyncSession
    ):
        todo_repo = TodoRepository(async_session)
        await todo_repo.create(
            {
                "todo_id": "TODO-SCHED-1",
                "title": "schedule retry original",
                "status": "blocked",
            }
        )
        await async_session.flush()
        dispatcher = _make_dispatcher(async_session)
        blocked = _make_blocked(
            todo_id="TODO-SCHED-1", remediation="schedule_retry"
        )

        action1 = await dispatcher.remediate(
            blocked, idempotency_key="key-retry-1"
        )
        assert action1.kind == RemediationActionKind.SCHEDULE_RETRY
        assert action1.ok

        action2 = await dispatcher.remediate(
            blocked, idempotency_key="key-retry-1"
        )
        assert action2.kind == RemediationActionKind.NO_ACTION
        assert action2.detail.get("idempotent_replay") is True

    @pytest.mark.asyncio
    async def test_same_key_with_file_human_todo_is_idempotent(
        self, async_session: AsyncSession
    ):
        todo_repo = TodoRepository(async_session)
        await todo_repo.create(
            {
                "todo_id": "TODO-HT-1",
                "title": "human-todo original",
                "status": "blocked",
            }
        )
        await async_session.flush()
        dispatcher = _make_dispatcher(async_session)
        blocked = _make_blocked(
            todo_id="TODO-HT-1",
            blocker_kind="human_input",
            remediation="file_human_todo",
        )

        action1 = await dispatcher.remediate(
            blocked, idempotency_key="key-ht-1"
        )
        assert action1.kind == RemediationActionKind.FILE_HUMAN_TODO
        assert action1.ok

        action2 = await dispatcher.remediate(
            blocked, idempotency_key="key-ht-1"
        )
        assert action2.kind == RemediationActionKind.NO_ACTION
        assert action2.detail.get("idempotent_replay") is True


class TestIdempotencyKeyDistinct:
    @pytest.mark.asyncio
    async def test_different_keys_act_independently(
        self, async_session: AsyncSession
    ):
        todo_repo = TodoRepository(async_session)
        original = await todo_repo.create(
            {
                "todo_id": "TODO-DIFF-1",
                "title": "task A",
                "description": "pre-blocked",
                "queue": "core",
                "work_type": "infra",
                "status": "blocked",
            }
        )
        await async_session.flush()
        dispatcher = _make_dispatcher(async_session)
        blocked = _make_blocked(
            todo_id=original.todo_id, remediation="dispatch_agent"
        )

        action1 = await dispatcher.remediate(
            blocked, idempotency_key="key-A"
        )
        assert action1.kind == RemediationActionKind.DISPATCH_AGENT

        action2 = await dispatcher.remediate(
            blocked, idempotency_key="key-B"
        )
        assert action2.kind == RemediationActionKind.DISPATCH_AGENT

        children = (
            (
                await async_session.execute(
                    select(TodoModel).where(
                        TodoModel.parent_todo_id == original.todo_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(children) == 2

    @pytest.mark.asyncio
    async def test_null_key_always_acts_normally(
        self, async_session: AsyncSession
    ):
        todo_repo = TodoRepository(async_session)
        original = await todo_repo.create(
            {
                "todo_id": "TODO-NULL-1",
                "title": "task null key",
                "description": "pre-blocked",
                "queue": "core",
                "work_type": "infra",
                "status": "blocked",
            }
        )
        await async_session.flush()
        dispatcher = _make_dispatcher(async_session)
        blocked = _make_blocked(
            todo_id=original.todo_id, remediation="dispatch_agent"
        )

        action1 = await dispatcher.remediate(blocked)
        assert action1.kind == RemediationActionKind.DISPATCH_AGENT

        action2 = await dispatcher.remediate(blocked)
        assert action2.kind == RemediationActionKind.DISPATCH_AGENT


class TestIdempotencyGuardEdgeCases:
    @pytest.mark.asyncio
    async def test_null_remediation_repo_with_key_does_not_crash(
        self, async_session: AsyncSession
    ):
        todo_repo = TodoRepository(async_session)
        await todo_repo.create(
            {
                "todo_id": "TODO-ID-1",
                "title": "norepo original",
                "status": "blocked",
            }
        )
        await async_session.flush()
        dispatcher = RemediationDispatcher(
            detector=_FakeDetector(config=RemediationConfig()),
            todo_repo=todo_repo,
            human_todo_repo=HumanTodoRepository(async_session),
            remediation_repo=None,
        )
        blocked = _make_blocked(remediation="dispatch_agent")
        action = await dispatcher.remediate(
            blocked, idempotency_key="key-norepo"
        )
        assert action.kind == RemediationActionKind.DISPATCH_AGENT

    @pytest.mark.asyncio
    async def test_idempotency_key_stored_on_audit_row(
        self, async_session: AsyncSession
    ):
        todo_repo = TodoRepository(async_session)
        original = await todo_repo.create(
            {
                "todo_id": "TODO-STORE-1",
                "title": "store key",
                "description": "pre-blocked",
                "queue": "core",
                "work_type": "infra",
                "status": "blocked",
            }
        )
        await async_session.flush()
        dispatcher = _make_dispatcher(async_session)
        blocked = _make_blocked(
            todo_id=original.todo_id, remediation="dispatch_agent"
        )

        await dispatcher.remediate(blocked, idempotency_key="key-stored")

        rows = (
            (await async_session.execute(select(RemediationActionModel)))
            .scalars()
            .all()
        )
        stored_keys = [r.idempotency_key for r in rows if r.idempotency_key is not None]
        assert "key-stored" in stored_keys

    @pytest.mark.asyncio
    async def test_idempotent_replay_does_not_raise_no_action_remediation(
        self, async_session: AsyncSession
    ):
        blocked = _make_blocked(remediation="no_action")
        dispatcher = _make_dispatcher(async_session)

        action1 = await dispatcher.remediate(
            blocked, idempotency_key="key-noop"
        )
        assert action1.kind == RemediationActionKind.NO_ACTION
        assert "Idempotent replay" not in action1.summary

        action2 = await dispatcher.remediate(
            blocked, idempotency_key="key-noop"
        )
        assert action2.kind == RemediationActionKind.NO_ACTION
        assert "Idempotent replay" in action2.summary
        assert action2.detail.get("idempotent_replay") is True
