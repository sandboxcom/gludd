"""Bucket lease acquisition and reclaim (H15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import BucketLeaseModel


async def acquire_lease(
    session: AsyncSession,
    bucket_key: str,
    holder_id: str,
    ttl_seconds: int = 300,
    project_id: str | None = None,
) -> BucketLeaseModel:
    """Acquire (or renew) a lease on a bucket for a holder.

    Idempotent per (bucket_key, holder_id): an existing row is renewed rather
    than duplicated (the unique constraint forbids duplicates anyway).
    """
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    stmt = select(BucketLeaseModel).where(
        BucketLeaseModel.bucket_key == bucket_key,
        BucketLeaseModel.holder_id == holder_id,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        existing.expires_at = expires_at
        if project_id is not None:
            existing.project_id = project_id
        await session.flush()
        return existing
    lease = BucketLeaseModel(
        bucket_key=bucket_key,
        holder_id=holder_id,
        expires_at=expires_at,
        project_id=project_id,
    )
    session.add(lease)
    await session.flush()
    return lease


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
    for lease in expired:
        # bucket_key == f"{queue}:{todo_id}"; the todo_id is the part after the
        # first ':' (queue names contain no ':'). Guard non-str/malformed keys
        # (e.g. a mocked lease, or a legacy bucket_key without a ':') so a bad
        # key just skips the requeue instead of raising.
        bucket_key = lease.bucket_key
        todo_id = bucket_key.partition(":")[2] if isinstance(bucket_key, str) else ""
        if todo_id:
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
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
