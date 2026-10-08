"""Durable single-writer coordination for signed decision capture."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.decision_codification.coordination import (
    CAPTURE_LEASE_TTL_SECONDS,
    DecisionCaptureCoordinationError,
    run_with_decision_capture_lease,
)
from general_ludd.event_loop.lease import LeaseBusyError

LEASE_KEY = "decision-capture:" + "a" * 64


def _session() -> AsyncSession:
    return cast(AsyncSession, object())


@pytest.mark.asyncio
async def test_capture_lease_serializes_one_attempt_and_releases_exact_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []

    async def acquire(
        session: AsyncSession,
        bucket_key: str,
        holder_id: str,
        ttl_seconds: int,
        project_id: str | None,
    ) -> object:
        events.append(
            ("acquire", session, bucket_key, holder_id, ttl_seconds, project_id)
        )
        return object()

    async def release(
        session: AsyncSession,
        bucket_key: str,
        holder_id: str | None,
    ) -> int:
        events.append(("release", session, bucket_key, holder_id))
        return 1

    async def operation() -> str:
        events.append(("capture",))
        return "receipt"

    monkeypatch.setattr(
        "general_ludd.event_loop.lease.acquire_lease",
        acquire,
    )
    monkeypatch.setattr(
        "general_ludd.event_loop.lease.release_lease",
        release,
    )
    monkeypatch.setattr(
        "general_ludd.decision_codification.coordination.token_hex",
        lambda _size: "b" * 32,
    )
    session = _session()

    result = await run_with_decision_capture_lease(
        session,
        lease_key=LEASE_KEY,
        operation=operation,
    )

    holder_id = "decision-capture-" + "b" * 32
    assert result == "receipt"
    assert events == [
        (
            "acquire",
            session,
            LEASE_KEY,
            holder_id,
            CAPTURE_LEASE_TTL_SECONDS,
            None,
        ),
        ("capture",),
        ("release", session, LEASE_KEY, holder_id),
    ]


@pytest.mark.asyncio
async def test_competing_capture_fails_before_operation_without_content_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def busy(*_args: object, **_kwargs: object) -> object:
        raise LeaseBusyError("private-lease-key")

    async def operation() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        "general_ludd.decision_codification.coordination._acquire_decision_lease",
        busy,
    )

    with pytest.raises(
        DecisionCaptureCoordinationError,
        match="coordination is unavailable",
    ) as error:
        await run_with_decision_capture_lease(
            _session(),
            lease_key=LEASE_KEY,
            operation=operation,
        )

    assert called is False
    assert "private-lease-key" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("released", [0, 2])
async def test_capture_failure_or_lost_release_is_content_free(
    monkeypatch: pytest.MonkeyPatch,
    released: int,
) -> None:
    async def acquire(*_args: object, **_kwargs: object) -> object:
        return object()

    async def release(*_args: object, **_kwargs: object) -> int:
        return released

    async def operation() -> None:
        if released == 0:
            raise RuntimeError("private-capture-detail")

    monkeypatch.setattr(
        "general_ludd.decision_codification.coordination._acquire_decision_lease",
        acquire,
    )
    monkeypatch.setattr(
        "general_ludd.decision_codification.coordination._release_decision_lease",
        release,
    )

    with pytest.raises(
        DecisionCaptureCoordinationError,
        match="coordination failed closed",
    ) as error:
        await run_with_decision_capture_lease(
            _session(),
            lease_key=LEASE_KEY,
            operation=operation,
        )

    assert "private-capture-detail" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lease_key",
    [
        "decision-capture:raw-private-id",
        "decision-capture:" + "A" * 64,
        "other:" + "a" * 64,
    ],
)
async def test_capture_coordination_rejects_non_opaque_keys_before_db_access(
    lease_key: str,
) -> None:
    operation: Callable[[], Awaitable[None]] = _never_called

    with pytest.raises(DecisionCaptureCoordinationError, match="key is invalid"):
        await run_with_decision_capture_lease(
            _session(),
            lease_key=lease_key,
            operation=operation,
        )


async def _never_called() -> None:
    raise AssertionError("operation must not run")
