"""Retention policy for ``task_decisions`` — periodic cleanup of old rows.

E11/E12: ``task_decisions`` is an insert-only table (rows are never updated or
deleted after creation). Without a retention policy the table grows unboundedly,
and the ``ORDER BY created_at DESC LIMIT 50`` query on each tick scans an
ever-larger index.

This module provides a single async function that deletes rows older than a
configurable cutoff, suitable for invocation on the event-loop tick or from a
scheduled cleanup job.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import TaskDecisionModel

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90


async def cleanup_old_task_decisions(
    session: AsyncSession,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
    dry_run: bool = False,
) -> int:
    """Delete ``task_decisions`` rows older than ``retention_days`` days.

    Args:
        session: An active async database session.
        retention_days: Rows older than this many days are deleted.
        now: Injection point for tests; defaults to ``datetime.now(UTC)``.
        dry_run: When ``True``, count matching rows without deleting them.

    Returns:
        Number of rows deleted (or matched in dry-run mode).
    """
    if retention_days <= 0:
        raise ValueError(f"retention_days must be > 0, got {retention_days}")

    cutoff = (
        (now or datetime.now(UTC)) - timedelta(days=retention_days)
    ).replace(hour=0, minute=0, second=0, microsecond=0)

    stmt = delete(TaskDecisionModel).where(
        TaskDecisionModel.created_at < cutoff
    )

    if dry_run:
        count_stmt = select(func.count()).select_from(
            select(TaskDecisionModel.id)
            .where(TaskDecisionModel.created_at < cutoff)
            .subquery()
        )
        result = await session.execute(count_stmt)
        count: int = result.scalar_one()
        return count

    result = await session.execute(stmt)
    deleted: int = int(getattr(result, "rowcount", 0) or 0)

    if deleted > 0:
        logger.info(
            "task_decisions retention cleanup deleted %d rows older than %s (retention_days=%d)",
            deleted,
            cutoff.isoformat(),
            retention_days,
        )

    return deleted
