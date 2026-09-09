"""Fenced execution-lease acquisition, heartbeat, and safe recovery."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import BucketLeaseModel, TodoModel
from general_ludd.event_loop.lease_cancellation import (
    confirm_lease_termination,
    request_lease_cancellation,
)
from general_ludd.event_loop.lease_validation import validate_lease_input
from general_ludd.schemas.todo import TodoStatus


class LeaseBusyError(RuntimeError):
    """Raised when another exact execution attempt owns a bucket."""


class LeaseRenewalStatus(StrEnum):
    """Outcome of one exact-holder execution heartbeat."""

    RENEWED = "renewed"
    CANCEL_REQUESTED = "cancel_requested"
    STALE = "stale"


async def acquire_lease(
    session: AsyncSession,
    bucket_key: str,
    holder_id: str,
    ttl_seconds: int = 300,
    project_id: str | None = None,
    todo_version: int | None = None,
) -> BucketLeaseModel:
    """Acquire one bucket for an exact todo claim attempt."""
    versions = None if todo_version is None else {bucket_key: todo_version}
    return (
        await acquire_leases_batch(
            session,
            [bucket_key],
            holder_id,
            ttl_seconds,
            project_id,
            todo_versions=versions,
        )
    )[0]


async def acquire_leases_batch(
    session: AsyncSession,
    bucket_keys: list[str],
    holder_id: str,
    ttl_seconds: int = 300,
    project_id: str | None = None,
    *,
    todo_versions: Mapping[str, int] | None = None,
) -> list[BucketLeaseModel]:
    """Acquire a batch atomically without replacing another live attempt."""
    validate_lease_input(bucket_keys, holder_id, ttl_seconds, todo_versions)
    if not bucket_keys:
        return []
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    stmt = select(BucketLeaseModel).where(
        BucketLeaseModel.bucket_key.in_(bucket_keys),
    )
    existing_rows = list((await session.execute(stmt)).scalars().all())
    existing_map: dict[str, BucketLeaseModel] = {r.bucket_key: r for r in existing_rows}
    # Validate the entire batch before staging any insert or refresh. A later
    # conflicting bucket must not leave earlier new rows pending in the caller's
    # session if it catches ``LeaseBusyError`` without rolling back immediately.
    for key, existing in existing_map.items():
        version = None if todo_versions is None else todo_versions.get(key)
        same_attempt = existing.holder_id == holder_id and (
            version is None or existing.todo_version == version
        )
        if (
            not same_attempt
            or existing.expires_at <= now
            or existing.cancel_requested_at is not None
            or existing.termination_confirmed_at is not None
        ):
            raise LeaseBusyError(f"bucket {key!r} is already owned")

    results: list[BucketLeaseModel] = []
    for key in bucket_keys:
        if key in existing_map:
            existing = existing_map[key]
            existing.expires_at = expires_at
            existing.heartbeat_at = now
            existing.updated_at = now
            if project_id is not None:
                existing.project_id = project_id
            results.append(existing)
        else:
            lease = BucketLeaseModel(
                bucket_key=key,
                holder_id=holder_id,
                todo_version=(
                    None if todo_versions is None else todo_versions.get(key)
                ),
                expires_at=expires_at,
                heartbeat_at=now,
                project_id=project_id,
                updated_at=now,
            )
            session.add(lease)
            results.append(lease)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise LeaseBusyError("one or more execution buckets lost the claim race") from exc
    return results


async def renew_lease(
    session: AsyncSession,
    *,
    bucket_key: str,
    holder_id: str,
    todo_version: int,
    ttl_seconds: int = 300,
) -> LeaseRenewalStatus:
    """Heartbeat only while the exact, unexpired attempt remains current."""
    validate_lease_input(
        [bucket_key],
        holder_id,
        ttl_seconds,
        {bucket_key: todo_version},
    )
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    result = await session.execute(
        update(BucketLeaseModel)
        .where(
            BucketLeaseModel.bucket_key == bucket_key,
            BucketLeaseModel.holder_id == holder_id,
            BucketLeaseModel.todo_version == todo_version,
            BucketLeaseModel.expires_at > now,
            BucketLeaseModel.cancel_requested_at.is_(None),
            BucketLeaseModel.termination_confirmed_at.is_(None),
        )
        .values(
            expires_at=expires_at,
            heartbeat_at=now,
            updated_at=now,
        )
    )
    if (cast("CursorResult[Any]", result).rowcount or 0) == 1:
        await session.flush()
        return LeaseRenewalStatus.RENEWED
    row = (
        await session.execute(
            select(BucketLeaseModel).where(
                BucketLeaseModel.bucket_key == bucket_key,
                BucketLeaseModel.holder_id == holder_id,
                BucketLeaseModel.todo_version == todo_version,
            )
        )
    ).scalar_one_or_none()
    if row is not None and row.cancel_requested_at is not None:
        return LeaseRenewalStatus.CANCEL_REQUESTED
    return LeaseRenewalStatus.STALE


async def reclaim_expired_leases(
    session: AsyncSession,
    max_age_seconds: int = 300,
) -> int:
    """Request cancellation, then requeue only termination-confirmed attempts.

    Heartbeat expiry is evidence that the owner may be unhealthy; it is not proof
    that model, Ansible, or infrastructure effects stopped. The first sweep keeps
    the lease and records ``cancel_requested_at``. Only a later sweep that sees
    exact-owner ``termination_confirmed_at`` may advance ACTIVE back to QUEUED.
    """
    del max_age_seconds

    now = datetime.now(UTC)
    stmt = select(BucketLeaseModel).where(BucketLeaseModel.expires_at < now)
    result = await session.execute(stmt)
    expired = list(result.scalars().all())
    if not expired:
        return 0
    todo_ids = {
        lease.bucket_key.partition(":")[2]
        for lease in expired
        if isinstance(lease.bucket_key, str) and ":" in lease.bucket_key
    }
    todo_map: dict[str, TodoModel] = {}
    if todo_ids:
        todo_rows = (
            await session.execute(select(TodoModel).where(TodoModel.todo_id.in_(todo_ids)))
        ).scalars().all()
        todo_map = {todo.todo_id: todo for todo in todo_rows}
    reclaimed = 0
    for lease in expired:
        bucket_key = lease.bucket_key
        todo_id = bucket_key.partition(":")[2] if isinstance(bucket_key, str) else ""
        todo = todo_map.get(todo_id)
        if todo is None or todo.status != TodoStatus.ACTIVE.value:
            await session.delete(lease)
            reclaimed += 1
            continue
        if (
            lease.termination_confirmed_at is None
            or lease.todo_version is None
            or todo.version != lease.todo_version
        ):
            if lease.cancel_requested_at is None:
                lease.cancel_requested_at = now
                lease.updated_at = now
            continue
        transitioned = await session.execute(
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
        if (cast("CursorResult[Any]", transitioned).rowcount or 0) != 1:
            continue
        await session.delete(lease)
        reclaimed += 1
    await session.flush()
    return reclaimed


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
    stmt = delete(BucketLeaseModel).where(BucketLeaseModel.bucket_key == bucket_key)
    if holder_id is not None:
        stmt = stmt.where(BucketLeaseModel.holder_id == holder_id)
    result = await session.execute(stmt)
    await session.flush()
    return int(cast("CursorResult[Any]", result).rowcount or 0)


__all__ = (
    "LeaseBusyError",
    "LeaseRenewalStatus",
    "acquire_lease",
    "acquire_leases_batch",
    "confirm_lease_termination",
    "reclaim_expired_leases",
    "release_lease",
    "renew_lease",
    "request_lease_cancellation",
)
