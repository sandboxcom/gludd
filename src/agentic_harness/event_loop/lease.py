"""Lease reclaim for bucket leases."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentic_harness.db.models import BucketLeaseModel


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
