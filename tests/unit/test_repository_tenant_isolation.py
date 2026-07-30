"""H.12 — cross-tenant leak in claim_unreviewed + requeue_needs_more_work.

These tests pin the same tenant-isolation invariant that ``claim_runnable``
already honours: when a claim method is invoked with ``project_id=None``
(unscooped), it MUST only touch rows whose ``project_id IS NULL``. A scoped
``project_id`` MUST only touch rows whose ``project_id`` equals that scope.

The bug being fixed:
* ``TaskReturnRepository.claim_unreviewed`` had no ``else`` branch, so
  ``project_id=None`` returned EVERY created row across all tenants.
* ``TodoRepository.requeue_needs_more_work`` had no ``project_id`` parameter
  at all, so it flipped NEEDS_MORE_WORK -> QUEUED across all tenants.

SQLite is used for isolation; no row locks are required for these
scoping-only assertions (we are not exercising contention, just scope).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from general_ludd.db.models import Base, ProjectModel, TaskReturnModel, TodoModel
from general_ludd.db.repository import TaskReturnRepository, TodoRepository
from general_ludd.schemas.todo import TodoStatus


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    db_path = tmp_path / "tenant_iso.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _ensure_project(factory, project_id: str) -> None:
    async with factory() as s:
        s.add(ProjectModel(project_id=project_id, name=project_id))
        await s.commit()


async def _add_task_return(
    factory,
    return_id: str,
    project_id: str | None,
    status: str = "created",
) -> None:
    if project_id is not None:
        await _ensure_project(factory, project_id)
    async with factory() as s:
        s.add(
            TaskReturnModel(
                return_id=return_id,
                project_id=project_id,
                job_id=f"job-{return_id}",
                playbook="noop.yml",
                queue="core",
                work_type="unknown",
                resource_profile="low_resource",
                status=status,
                exit_code=0,
            )
        )
        await s.commit()


async def _add_needs_more_work_todo(
    factory,
    todo_id: str,
    project_id: str | None,
    updated_at: datetime,
    run_count: int = 1,
) -> None:
    if project_id is not None:
        await _ensure_project(factory, project_id)
    async with factory() as s:
        s.add(
            TodoModel(
                todo_id=todo_id,
                project_id=project_id,
                title=f"todo {todo_id}",
                status=TodoStatus.NEEDS_MORE_WORK.value,
                queue="core",
                version=1,
                run_count=run_count,
                updated_at=updated_at,
            )
        )
        await s.commit()


# ---------------------------------------------------------------------------
# claim_unreviewed: project_id=None MUST only return rows with NULL project_id
# ---------------------------------------------------------------------------
class TestClaimUnreviewedTenantIsolation:
    async def test_claim_unreviewed_unscoped_returns_only_null_project(self, session_factory):
        """An unscooped call (project_id=None) must NOT cross into any tenant's
        rows. Only the NULL-project rows are claimable by the unscooped path."""
        await _add_task_return(session_factory, "R-TENANT-A", project_id="proj-a")
        await _add_task_return(session_factory, "R-NULL", project_id=None)

        async with session_factory() as s:
            repo = TaskReturnRepository(s)
            claimed = await repo.claim_unreviewed(project_id=None, limit=10)

        claimed_ids = {r.return_id for r in claimed}
        assert "R-NULL" in claimed_ids
        assert "R-TENANT-A" not in claimed_ids, "cross-tenant leak: unscooped claim_unreviewed claimed a tenant row"

    async def test_claim_unreviewed_scoped_excludes_other_tenants(self, session_factory):
        await _add_task_return(session_factory, "R-A", project_id="proj-a")
        await _add_task_return(session_factory, "R-B", project_id="proj-b")
        await _add_task_return(session_factory, "R-NULL", project_id=None)

        async with session_factory() as s:
            repo = TaskReturnRepository(s)
            claimed = await repo.claim_unreviewed(project_id="proj-a", limit=10)

        claimed_ids = {r.return_id for r in claimed}
        assert claimed_ids == {"R-A"}


# ---------------------------------------------------------------------------
# requeue_needs_more_work: scoped call MUST NOT flip other tenants' todos
# ---------------------------------------------------------------------------
class TestRequeueNeedsMoreWorkTenantIsolation:
    async def test_requeue_needs_more_work_scoped_excludes_other_tenants(self, session_factory):
        """A scoped requeue MUST only flip rows in that scope. The pre-fix bug:
        no project_id param existed, so EVERY tenant's rows were requeued."""
        cutoff = datetime.now(UTC) - timedelta(hours=48)
        await _add_needs_more_work_todo(session_factory, "T-A", project_id="proj-a", updated_at=cutoff)
        await _add_needs_more_work_todo(session_factory, "T-B", project_id="proj-b", updated_at=cutoff)
        await _add_needs_more_work_todo(session_factory, "T-NULL", project_id=None, updated_at=cutoff)

        async with session_factory() as s:
            repo = TodoRepository(s)
            requeued = await repo.requeue_needs_more_work(
                project_id="proj-a",
                cooldown_hours=24,
                max_run_count=3,
                limit=10,
            )
            await s.commit()

        assert requeued == 1
        async with session_factory() as s:
            rows = {t.todo_id: t for t in (await s.execute(_all_todos())).scalars()}
        assert rows["T-A"].status == TodoStatus.QUEUED.value
        assert rows["T-B"].status == TodoStatus.NEEDS_MORE_WORK.value
        assert rows["T-NULL"].status == TodoStatus.NEEDS_MORE_WORK.value

    async def test_requeue_needs_more_work_unscoped_only_null_project(self, session_factory):
        cutoff = datetime.now(UTC) - timedelta(hours=48)
        await _add_needs_more_work_todo(session_factory, "T-A", project_id="proj-a", updated_at=cutoff)
        await _add_needs_more_work_todo(session_factory, "T-NULL", project_id=None, updated_at=cutoff)

        async with session_factory() as s:
            repo = TodoRepository(s)
            requeued = await repo.requeue_needs_more_work(
                project_id=None,
                cooldown_hours=24,
                max_run_count=3,
                limit=10,
            )
            await s.commit()

        assert requeued == 1
        async with session_factory() as s:
            rows = {t.todo_id: t for t in (await s.execute(_all_todos())).scalars()}
        assert rows["T-NULL"].status == TodoStatus.QUEUED.value
        assert rows["T-A"].status == TodoStatus.NEEDS_MORE_WORK.value


def _all_todos():
    from sqlalchemy import select

    return select(TodoModel)
