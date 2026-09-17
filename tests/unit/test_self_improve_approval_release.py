"""Wired release path for human-gated self-improve todos.

``SelfImproveGate.auto_queue`` defaults to False, so admitted self-improve todos
are parked in APPROVAL_REQUIRED. These tests prove there is a WIRED release path
(``SelfImproveApprovalManager.approve_by_id`` / ``reject_by_id``) that persists
APPROVAL_REQUIRED -> APPROVED (legacy approve) and APPROVAL_REQUIRED ->
CANCELLED (reject) through a real ``TodoRepository`` — so a held self-improve
todo can never strand or leak into the generic scheduler.

This exercises the repository's OWN ``VALID_TRANSITIONS`` table (db/repository.py),
which must contain an APPROVAL_REQUIRED entry or ``TodoRepository.transition``
would reject the release with ``InvalidTransitionError``.

asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import TodoRepository
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.approval import (
    ApprovalError,
    SelfImproveApprovalManager,
)


def _make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


async def _seed(
    session: AsyncSession,
    *,
    todo_id: str,
    status: TodoStatus = TodoStatus.APPROVAL_REQUIRED,
    work_type: str = "self_improve",
) -> None:
    repo = TodoRepository(session)
    await repo.create(
        todo_data={
            "todo_id": todo_id,
            "title": "Self-improvement: add tests for foo.py",
            "status": status.value,
            "work_type": work_type,
            "priority": 10,
            "created_by": "self_improve_harness",
            "plan_artifact": json.dumps(
                {
                    "capability_required": "config_write",
                    "change_content": "enabled: true\n",
                    "kind": "config",
                    "reason": "release test",
                    "target_paths": ["config/test.yml"],
                }
            ),
        }
    )
    await session.commit()


class TestApproveByIdReleasesToApproved:
    async def test_approve_moves_approval_required_to_approved(
        self, async_session: AsyncSession
    ) -> None:
        await _seed(async_session, todo_id="TODO-SI-1")
        mgr = SelfImproveApprovalManager()
        repo = TodoRepository(async_session)

        released = await mgr.approve_by_id(repo, "TODO-SI-1")
        await async_session.commit()

        assert released.status == TodoStatus.APPROVED.value
        assert released.approved_artifact_digest is not None
        # Persisted, not just in-memory.
        fetched = await repo.get_by_id("TODO-SI-1")
        assert fetched is not None
        assert fetched.status == TodoStatus.APPROVED.value
        assert fetched.version == 3  # digest binding + guarded transition

    async def test_reject_moves_approval_required_to_cancelled(
        self, async_session: AsyncSession
    ) -> None:
        await _seed(async_session, todo_id="TODO-SI-2")
        mgr = SelfImproveApprovalManager()
        repo = TodoRepository(async_session)

        await mgr.reject_by_id(repo, "TODO-SI-2", reason="not worth it")
        await async_session.commit()

        fetched = await repo.get_by_id("TODO-SI-2")
        assert fetched is not None
        assert fetched.status == TodoStatus.CANCELLED.value
        assert fetched.manual_hold_reason == "not worth it"

    async def test_list_pending_returns_only_held_self_improve(
        self, async_session: AsyncSession
    ) -> None:
        await _seed(async_session, todo_id="TODO-SI-3")
        # A held todo of a DIFFERENT work_type must not appear.
        await _seed(async_session, todo_id="TODO-OTHER", work_type="code")
        # A self-improve todo that is NOT held must not appear.
        await _seed(async_session, todo_id="TODO-SI-Q", status=TodoStatus.QUEUED)

        mgr = SelfImproveApprovalManager()
        repo = TodoRepository(async_session)
        pending = await mgr.list_pending(repo)

        ids = {t.todo_id for t in pending}
        assert ids == {"TODO-SI-3"}


class TestReleaseGuards:
    async def test_approve_missing_raises(self, async_session: AsyncSession) -> None:
        mgr = SelfImproveApprovalManager()
        repo = TodoRepository(async_session)
        with pytest.raises(ApprovalError, match="not found"):
            await mgr.approve_by_id(repo, "TODO-NOPE")

    async def test_approve_non_self_improve_raises(
        self, async_session: AsyncSession
    ) -> None:
        await _seed(async_session, todo_id="TODO-CODE", work_type="code")
        mgr = SelfImproveApprovalManager()
        repo = TodoRepository(async_session)
        with pytest.raises(ApprovalError, match="not a self-improve todo"):
            await mgr.approve_by_id(repo, "TODO-CODE")

    async def test_approve_not_pending_raises(
        self, async_session: AsyncSession
    ) -> None:
        await _seed(async_session, todo_id="TODO-Q", status=TodoStatus.QUEUED)
        mgr = SelfImproveApprovalManager()
        repo = TodoRepository(async_session)
        with pytest.raises(ApprovalError, match="not awaiting approval"):
            await mgr.approve_by_id(repo, "TODO-Q")
