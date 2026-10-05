"""Recover expired execution leases after exact-owner termination."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import BucketLeaseModel, TodoModel
from general_ludd.schemas.project_identity import ProjectWorkIdentity
from general_ludd.schemas.todo import TodoStatus

LeaseScope = tuple[str | None, str, bool]


def _lease_scope(lease: BucketLeaseModel) -> LeaseScope | None:
    bucket_key = lease.bucket_key
    if not isinstance(bucket_key, str):
        return None
    scoped = ProjectWorkIdentity.from_lease_bucket_key(bucket_key)
    if scoped is not None:
        return scoped.project_id, scoped.todo_id, True
    parts = bucket_key.split(":")
    if len(parts) == 5 and parts[0] == "unowned" and parts[1] == "queue" and parts[3] == "todo":
        return None, parts[4], True
    if len(parts) == 2 and all(parts):
        return getattr(lease, "project_id", None), parts[1], False
    return None


async def _load_todos(
    session: AsyncSession,
    lease_scopes: dict[str, LeaseScope],
) -> dict[str, list[TodoModel]]:
    todo_ids = {todo_id for _, todo_id, _ in lease_scopes.values()}
    if not todo_ids:
        return {}
    rows = (
        await session.execute(select(TodoModel).where(TodoModel.todo_id.in_(todo_ids)))
    ).scalars().all()
    todos_by_id: dict[str, list[TodoModel]] = {}
    for row in rows:
        todos_by_id.setdefault(row.todo_id, []).append(row)
    return todos_by_id


def _request_cancellation(lease: BucketLeaseModel, now: datetime) -> None:
    if lease.cancel_requested_at is None:
        lease.cancel_requested_at = now
        lease.updated_at = now


def _matching_todo(
    candidates: list[TodoModel],
    project_id: str | None,
    exact_scope: bool,
) -> TodoModel | None:
    if exact_scope or project_id is not None:
        return next((row for row in candidates if row.project_id == project_id), None)
    return candidates[0] if len(candidates) == 1 else None


async def _reclaim_one(
    session: AsyncSession,
    lease: BucketLeaseModel,
    scope: LeaseScope | None,
    todos_by_id: dict[str, list[TodoModel]],
    now: datetime,
) -> bool:
    if scope is None:
        if lease.termination_confirmed_at is not None:
            await session.delete(lease)
            return True
        _request_cancellation(lease, now)
        return False
    project_id, todo_id, exact_scope = scope
    if exact_scope and lease.project_id != project_id:
        _request_cancellation(lease, now)
        return False
    todo = _matching_todo(todos_by_id.get(todo_id, []), project_id, exact_scope)
    if todo is None or todo.status != TodoStatus.ACTIVE.value:
        await session.delete(lease)
        return True
    if (
        lease.termination_confirmed_at is None
        or lease.todo_version is None
        or todo.version != lease.todo_version
    ):
        _request_cancellation(lease, now)
        return False
    stmt = (
        update(TodoModel)
        .where(
            TodoModel.todo_id == todo_id,
            TodoModel.status == TodoStatus.ACTIVE.value,
            TodoModel.version == lease.todo_version,
        )
        .values(
            status=TodoStatus.QUEUED.value,
            version=TodoModel.version + 1,
            updated_at=now,
        )
    )
    if exact_scope:
        condition = TodoModel.project_id.is_(None) if project_id is None else TodoModel.project_id == project_id
        stmt = stmt.where(condition)
    transitioned = await session.execute(stmt)
    if (cast("CursorResult[Any]", transitioned).rowcount or 0) != 1:
        return False
    await session.delete(lease)
    return True


async def reclaim_expired_leases(
    session: AsyncSession,
    max_age_seconds: int = 300,
    *,
    now: datetime | None = None,
) -> int:
    """Cancel expired owners and requeue only termination-confirmed attempts."""
    del max_age_seconds
    now = now or datetime.now(UTC)
    result = await session.execute(
        select(BucketLeaseModel).where(BucketLeaseModel.expires_at < now)
    )
    expired = list(result.scalars().all())
    if not expired:
        return 0
    lease_scopes = {
        lease.bucket_key: scope
        for lease in expired
        if isinstance(lease.bucket_key, str)
        and (scope := _lease_scope(lease)) is not None
    }
    todos_by_id = await _load_todos(session, lease_scopes)
    reclaimed = 0
    for lease in expired:
        key = lease.bucket_key
        scope = lease_scopes.get(key) if isinstance(key, str) else None
        reclaimed += int(await _reclaim_one(session, lease, scope, todos_by_id, now))
    await session.flush()
    return reclaimed


__all__ = ("reclaim_expired_leases",)
