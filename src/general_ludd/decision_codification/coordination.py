"""Database-backed single-writer coordination for decision capture."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from secrets import token_hex
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

CAPTURE_LEASE_TTL_SECONDS = 60
_CAPTURE_LEASE_KEY = re.compile(r"decision-capture:[0-9a-f]{64}\Z")
_T = TypeVar("_T")


class DecisionCaptureCoordinationError(RuntimeError):
    """Refuse capture when its durable single-writer claim is uncertain."""


async def _acquire_decision_lease(
    session: AsyncSession,
    bucket_key: str,
    holder_id: str,
    ttl_seconds: int,
    project_id: str | None,
) -> object:
    """Reuse the execution lease without creating an import cycle."""
    from general_ludd.event_loop.lease import acquire_lease

    return await acquire_lease(
        session,
        bucket_key,
        holder_id,
        ttl_seconds=ttl_seconds,
        project_id=project_id,
    )


async def _release_decision_lease(
    session: AsyncSession,
    bucket_key: str,
    holder_id: str,
) -> int:
    """Release only the exact holder through the existing lease primitive."""
    from general_ludd.event_loop.lease import release_lease

    return await release_lease(session, bucket_key, holder_id)


async def run_with_decision_capture_lease(
    session: AsyncSession,
    *,
    lease_key: str,
    operation: Callable[[], Awaitable[_T]],
) -> _T:
    """Run one capture while holding the existing unique database lease row."""
    if not isinstance(lease_key, str) or _CAPTURE_LEASE_KEY.fullmatch(lease_key) is None:
        raise DecisionCaptureCoordinationError(
            "decision capture coordination key is invalid"
        )
    holder_id = f"decision-capture-{token_hex(16)}"
    try:
        await _acquire_decision_lease(
            session,
            lease_key,
            holder_id,
            ttl_seconds=CAPTURE_LEASE_TTL_SECONDS,
            project_id=None,
        )
    except Exception as exc:
        raise DecisionCaptureCoordinationError(
            "decision capture coordination is unavailable"
        ) from exc

    try:
        try:
            result = await operation()
        except Exception as exc:
            raise DecisionCaptureCoordinationError(
                "decision capture coordination failed closed"
            ) from exc
    finally:
        try:
            released = await _release_decision_lease(session, lease_key, holder_id)
        except Exception as exc:
            raise DecisionCaptureCoordinationError(
                "decision capture coordination failed closed"
            ) from exc
        if released != 1:
            raise DecisionCaptureCoordinationError(
                "decision capture coordination failed closed"
            )
    return result


__all__ = [
    "CAPTURE_LEASE_TTL_SECONDS",
    "DecisionCaptureCoordinationError",
    "run_with_decision_capture_lease",
]
