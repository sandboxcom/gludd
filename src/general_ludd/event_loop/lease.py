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
    now = datetime.now(UTC)
    stmt = select(BucketLeaseModel).where(BucketLeaseModel.expires_at < now)
    result = await session.execute(stmt)
    expired = list(result.scalars().all())
    for lease in expired:
        await session.delete(lease)
    await session.flush()
    return len(expired)
