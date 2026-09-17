"""Exact-owner cancellation and termination proof for execution leases."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import BucketLeaseModel
from general_ludd.event_loop.lease_validation import validate_lease_input


async def request_lease_cancellation(
    session: AsyncSession,
    *,
    bucket_key: str,
    holder_id: str,
    todo_version: int,
) -> bool:
    """Expire and request cancellation for only one exact execution attempt."""
    validate_lease_input([bucket_key], holder_id, 1, {bucket_key: todo_version})
    now = datetime.now(UTC)
    result = await session.execute(
        update(BucketLeaseModel)
        .where(
            BucketLeaseModel.bucket_key == bucket_key,
            BucketLeaseModel.holder_id == holder_id,
            BucketLeaseModel.todo_version == todo_version,
            BucketLeaseModel.termination_confirmed_at.is_(None),
        )
        .values(
            cancel_requested_at=func.coalesce(
                BucketLeaseModel.cancel_requested_at,
                now,
            ),
            expires_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    return (cast("CursorResult[Any]", result).rowcount or 0) == 1


async def confirm_lease_termination(
    session: AsyncSession,
    *,
    bucket_key: str,
    holder_id: str,
    todo_version: int,
) -> bool:
    """Record exact-owner proof that cancellation fully reaped its process."""
    now = datetime.now(UTC)
    result = await session.execute(
        update(BucketLeaseModel)
        .where(
            BucketLeaseModel.bucket_key == bucket_key,
            BucketLeaseModel.holder_id == holder_id,
            BucketLeaseModel.todo_version == todo_version,
            BucketLeaseModel.cancel_requested_at.is_not(None),
            BucketLeaseModel.termination_confirmed_at.is_(None),
        )
        .values(termination_confirmed_at=now, updated_at=now)
    )
    await session.flush()
    return (cast("CursorResult[Any]", result).rowcount or 0) == 1


__all__ = ("confirm_lease_termination", "request_lease_cancellation")
