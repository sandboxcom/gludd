"""Exact-owner cancellation and termination proof for execution leases."""

from __future__ import annotations

import threading
import weakref
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import event, func, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from general_ludd.db.models import BucketLeaseModel
from general_ludd.event_loop.lease_validation import validate_lease_input

_CancellationKey = tuple[str, str, int]
_PendingCancellations = dict[object, set[_CancellationKey]]
_PENDING_LOCAL_CANCELLATIONS = "gludd_pending_local_lease_cancellations"
_local_cancel_signals: dict[
    _CancellationKey,
    weakref.WeakSet[threading.Event],
] = {}
_local_cancel_signals_lock = threading.Lock()


def register_local_cancellation_signal(
    *,
    bucket_key: str,
    holder_id: str,
    todo_version: int,
    signal: threading.Event,
) -> None:
    """Bind an in-process runner signal to one durable lease identity."""
    identity = (bucket_key, holder_id, todo_version)
    with _local_cancel_signals_lock:
        signals = _local_cancel_signals.setdefault(identity, weakref.WeakSet())
        signals.add(signal)


def unregister_local_cancellation_signal(
    *,
    bucket_key: str,
    holder_id: str,
    todo_version: int,
    signal: threading.Event,
) -> None:
    """Remove a completed runner from the in-process cancellation index."""
    identity = (bucket_key, holder_id, todo_version)
    with _local_cancel_signals_lock:
        signals = _local_cancel_signals.get(identity)
        if signals is None:
            return
        signals.discard(signal)
        if not signals:
            _local_cancel_signals.pop(identity, None)


def _notify_local_cancellation(identity: _CancellationKey) -> None:
    with _local_cancel_signals_lock:
        signals = tuple(_local_cancel_signals.get(identity, ()))
    for signal in signals:
        signal.set()


@event.listens_for(Session, "after_commit")
def _notify_committed_local_cancellations(session: Session) -> None:
    """Wake local runners only after their durable cancellation commits."""
    if session.in_nested_transaction():
        return
    pending = cast(
        "_PendingCancellations",
        session.info.pop(_PENDING_LOCAL_CANCELLATIONS, {}),
    )
    for identity in {item for identities in pending.values() for item in identities}:
        _notify_local_cancellation(identity)


@event.listens_for(Session, "after_rollback")
def _discard_rolled_back_local_cancellations(session: Session) -> None:
    """Never expose a cancellation whose database transaction rolled back."""
    nested = session.get_nested_transaction()
    if nested is None:
        session.info.pop(_PENDING_LOCAL_CANCELLATIONS, None)
        return
    pending = cast(
        "_PendingCancellations",
        session.info.get(_PENDING_LOCAL_CANCELLATIONS, {}),
    )
    pending.pop(nested, None)
    if not pending:
        session.info.pop(_PENDING_LOCAL_CANCELLATIONS, None)


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
    requested = (cast("CursorResult[Any]", result).rowcount or 0) == 1
    if requested:
        sync_session = session.sync_session
        transaction = (
            sync_session.get_nested_transaction()
            or sync_session.get_transaction()
        )
        if transaction is None:
            raise RuntimeError("lease cancellation requires an active transaction")
        pending = cast(
            "_PendingCancellations",
            session.info.setdefault(_PENDING_LOCAL_CANCELLATIONS, {}),
        )
        pending.setdefault(transaction, set()).add(
            (bucket_key, holder_id, todo_version)
        )
    return requested


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


__all__ = (
    "confirm_lease_termination",
    "register_local_cancellation_signal",
    "request_lease_cancellation",
    "unregister_local_cancellation_signal",
)
