"""Bucket lease acquisition and reclaim (H15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import BucketLeaseModel


async def acquire_lease(
    session: AsyncSession,
    bucket_key: str,
    holder_id: str,
    ttl_seconds: int = 300,
    project_id: str | None = None,
) -> BucketLeaseModel:
    return (
        await acquire_leases_batch(
            session, [bucket_key], holder_id, ttl_seconds, project_id
        )
    )[0]


async def acquire_leases_batch(
    session: AsyncSession,
    bucket_keys: list[str],
    holder_id: str,
    ttl_seconds: int = 300,
    project_id: str | None = None,
) -> list[BucketLeaseModel]:
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    stmt = select(BucketLeaseModel).where(
        BucketLeaseModel.bucket_key.in_(bucket_keys),
        BucketLeaseModel.holder_id == holder_id,
    )
    existing_rows = list((await session.execute(stmt)).scalars().all())
    existing_map: dict[str, BucketLeaseModel] = {r.bucket_key: r for r in existing_rows}
    results: list[BucketLeaseModel] = []
    for key in bucket_keys:
        if key in existing_map:
            existing = existing_map[key]
            existing.expires_at = expires_at
            if project_id is not None:
                existing.project_id = project_id
            results.append(existing)
        else:
            lease = BucketLeaseModel(
                bucket_key=key,
                holder_id=holder_id,
                expires_at=expires_at,
                project_id=project_id,
            )
            session.add(lease)
            results.append(lease)
    await session.flush()
    return results


async def reclaim_expired_leases(
    session: AsyncSession,
    max_age_seconds: int = 300,
) -> int:
    """Delete expired bucket leases AND requeue the orphaned work they guarded.

    A lease whose ``expires_at`` is in the past means the holder (a tick/worker)
    is presumed crashed. Deleting the bookkeeping row alone loses the work: the
    associated todo stays ACTIVE forever and ``claim_runnable`` (which only sees
    QUEUED) never re-dispatches it. So for each expired lease we also reset its
    still-ACTIVE todo back to QUEUED with a guarded conditional UPDATE so the work
    is actually reclaimable. The bucket_key is ``f"{queue}:{todo_id}"`` (see
    EventLoop._phase_claim_runnable_todos).
    """
    from sqlalchemy import update

    from general_ludd.db.models import TodoModel
    from general_ludd.schemas.todo import TodoStatus

    now = datetime.now(UTC)
    stmt = select(BucketLeaseModel).where(BucketLeaseModel.expires_at < now)
    result = await session.execute(stmt)
    expired = list(result.scalars().all())
    if not expired:
        return 0
    bucket_keys = [
        lease.bucket_key for lease in expired
        if isinstance(lease.bucket_key, str) and ":" in lease.bucket_key
    ]
    live_map: dict[str, list[BucketLeaseModel]] = {}
    if bucket_keys:
        live_stmt = (
            select(BucketLeaseModel)
            .where(
                BucketLeaseModel.bucket_key.in_(bucket_keys),
                BucketLeaseModel.expires_at >= now,
            )
        )
        for live in (await session.execute(live_stmt)).scalars().all():
            live_map.setdefault(live.bucket_key, []).append(live)
    for lease in expired:
        bucket_key = lease.bucket_key
        todo_id = bucket_key.partition(":")[2] if isinstance(bucket_key, str) else ""
        if todo_id:
            live_leases = live_map.get(bucket_key, [])
            has_live = any(live.id != lease.id for live in live_leases)
            if not has_live:
                await session.execute(
                    update(TodoModel)
                    .where(
                        TodoModel.todo_id == todo_id,
                        TodoModel.status == TodoStatus.ACTIVE.value,
                    )
                    .values(status=TodoStatus.QUEUED.value, updated_at=now)
                )
        await session.delete(lease)
    await session.flush()
    return len(expired)


async def release_lease(
    session: AsyncSession,
    bucket_key: str,
    holder_id: str | None = None,
) -> int:
    """Delete the bucket lease for a released todo. Returns rows deleted.

    Called from the PID-cap trim path (and any other place a claimed todo is
    released back to QUEUED without ever being dispatched): without this, the
    lease row is orphaned, accumulates, eventually expires, and trips
    ``reclaim_expired_leases`` to requeue a todo that was just requeued by the
    trim — a double-dispatch vector.
    """
    from sqlalchemy import delete

    stmt = delete(BucketLeaseModel).where(BucketLeaseModel.bucket_key == bucket_key)
    if holder_id is not None:
        stmt = stmt.where(BucketLeaseModel.holder_id == holder_id)
    result = await session.execute(stmt)
    await session.flush()
    return int(cast("CursorResult[Any]", result).rowcount or 0)
