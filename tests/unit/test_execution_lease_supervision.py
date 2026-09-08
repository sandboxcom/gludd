"""Behavioral contracts for durable execution-lease supervision."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.execution_supervision import (
    ExecutionLeaseIdentity,
    ExecutionLeaseSupervisor,
    LeaseTerminationOutcome,
)
from general_ludd.event_loop.lease import LeaseRenewalStatus


def _session_factory(session: MagicMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=context)


def _supervisor(
    session: MagicMock,
    *,
    event_bus: MagicMock | None = None,
) -> ExecutionLeaseSupervisor:
    return ExecutionLeaseSupervisor(
        session_factory=_session_factory(session),
        identity=ExecutionLeaseIdentity(
            bucket_key="core:TODO-1",
            holder_id="event-loop-owner",
            todo_version=8,
        ),
        event_bus=event_bus,
        ttl_seconds=60,
        heartbeat_interval_seconds=5.0,
    )


@pytest.mark.parametrize(
    ("ttl_seconds", "interval_seconds", "message"),
    [
        (0, 1.0, "ttl_seconds must be positive"),
        (60, 0.0, "heartbeat_interval_seconds must be positive"),
        (60, 60.0, "heartbeat interval must be shorter"),
        (60, 61.0, "heartbeat interval must be shorter"),
    ],
)
def test_rejects_unsafe_heartbeat_configuration(
    ttl_seconds: int,
    interval_seconds: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ExecutionLeaseSupervisor(
            session_factory=MagicMock(),
            identity=ExecutionLeaseIdentity("core:TODO-1", "owner", 8),
            ttl_seconds=ttl_seconds,
            heartbeat_interval_seconds=interval_seconds,
        )


@pytest.mark.asyncio
async def test_heartbeat_renews_exact_attempt_in_independent_session() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    event_bus = MagicMock()
    supervisor = _supervisor(session, event_bus=event_bus)

    with patch(
        "general_ludd.event_loop.execution_supervision.renew_lease",
        new=AsyncMock(return_value=LeaseRenewalStatus.RENEWED),
    ) as renew:
        status = await supervisor.heartbeat_once()

    assert status is LeaseRenewalStatus.RENEWED
    assert supervisor.is_cancellation_requested() is False
    renew.assert_awaited_once_with(
        session,
        bucket_key="core:TODO-1",
        holder_id="event-loop-owner",
        todo_version=8,
        ttl_seconds=60,
    )
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    event = event_bus.publish.call_args.args[0]
    assert event.name == "execution_lease_heartbeat"
    assert event.payload == {
        "bucket_key": "core:TODO-1",
        "holder_id": "event-loop-owner",
        "name": "execution_lease_heartbeat",
        "status": "renewed",
        "todo_version": 8,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [LeaseRenewalStatus.CANCEL_REQUESTED, LeaseRenewalStatus.STALE],
)
async def test_nonrenewable_heartbeat_fails_closed(
    status: LeaseRenewalStatus,
) -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    supervisor = _supervisor(session)

    with patch(
        "general_ludd.event_loop.execution_supervision.renew_lease",
        new=AsyncMock(return_value=status),
    ):
        observed = await supervisor.heartbeat_once()

    assert observed is status
    assert supervisor.is_cancellation_requested() is True
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_heartbeat_database_failure_cancels_without_leaking_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    supervisor = _supervisor(session)

    with patch(
        "general_ludd.event_loop.execution_supervision.renew_lease",
        new=AsyncMock(side_effect=RuntimeError("credential=super-secret")),
    ):
        status = await supervisor.heartbeat_once()

    assert status is LeaseRenewalStatus.STALE
    assert supervisor.is_cancellation_requested() is True
    session.rollback.assert_awaited_once()
    assert "RuntimeError" in caplog.text
    assert "super-secret" not in caplog.text


@pytest.mark.asyncio
async def test_owner_cancellation_is_persisted_before_local_callback_flips() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    supervisor = _supervisor(session)
    callback_state_during_request: list[bool] = []

    async def _request(*args: object, **kwargs: object) -> bool:
        callback_state_during_request.append(supervisor.is_cancellation_requested())
        return True

    with patch(
        "general_ludd.event_loop.execution_supervision.request_lease_cancellation",
        new=AsyncMock(side_effect=_request),
    ) as request:
        requested = await supervisor.request_cancellation()

    assert requested is True
    assert callback_state_during_request == [False]
    assert supervisor.is_cancellation_requested() is True
    request.assert_awaited_once_with(
        session,
        bucket_key="core:TODO-1",
        holder_id="event-loop-owner",
        todo_version=8,
    )
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_database_failure_still_stops_runner_and_redacts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    event_bus = MagicMock()
    supervisor = _supervisor(session, event_bus=event_bus)

    with patch(
        "general_ludd.event_loop.execution_supervision.request_lease_cancellation",
        new=AsyncMock(side_effect=RuntimeError("token=must-not-leak")),
    ):
        requested = await supervisor.request_cancellation()

    assert requested is False
    assert supervisor.is_cancellation_requested() is True
    session.rollback.assert_awaited_once()
    event = event_bus.publish.call_args.args[0]
    assert event.payload["status"] == "failed"
    assert event.payload["exception_type"] == "RuntimeError"
    assert "must-not-leak" not in caplog.text


@pytest.mark.asyncio
async def test_terminal_proof_confirms_then_reclaims_exact_attempt() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    event_bus = MagicMock()
    supervisor = _supervisor(session, event_bus=event_bus)

    with (
        patch(
            "general_ludd.event_loop.execution_supervision.confirm_lease_termination",
            new=AsyncMock(return_value=True),
        ) as confirm,
        patch(
            "general_ludd.event_loop.execution_supervision.reclaim_expired_leases",
            new=AsyncMock(return_value=1),
        ) as reclaim,
    ):
        outcome = await supervisor.confirm_termination()

    assert outcome == LeaseTerminationOutcome(confirmed=True, reclaimed=1)
    confirm.assert_awaited_once_with(
        session,
        bucket_key="core:TODO-1",
        holder_id="event-loop-owner",
        todo_version=8,
    )
    reclaim.assert_awaited_once_with(session)
    session.commit.assert_awaited_once()
    event = event_bus.publish.call_args.args[0]
    assert event.name == "execution_lease_termination_confirmed"
    assert event.payload["reclaimed"] == 1


@pytest.mark.asyncio
async def test_failed_terminal_confirmation_never_reclaims() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    supervisor = _supervisor(session)

    with (
        patch(
            "general_ludd.event_loop.execution_supervision.confirm_lease_termination",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "general_ludd.event_loop.execution_supervision.reclaim_expired_leases",
            new_callable=AsyncMock,
        ) as reclaim,
    ):
        outcome = await supervisor.confirm_termination()

    assert outcome == LeaseTerminationOutcome(confirmed=False, reclaimed=0)
    reclaim.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_confirmation_failure_retains_lease_and_redacts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    supervisor = _supervisor(session)

    with patch(
        "general_ludd.event_loop.execution_supervision.confirm_lease_termination",
        new=AsyncMock(side_effect=RuntimeError("dsn=must-not-leak")),
    ):
        outcome = await supervisor.confirm_termination()

    assert outcome == LeaseTerminationOutcome(confirmed=False, reclaimed=0)
    session.rollback.assert_awaited_once()
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text


@pytest.mark.asyncio
async def test_run_heartbeats_immediately_and_stops_without_sleeping() -> None:
    session = MagicMock()
    supervisor = _supervisor(session)

    async def _heartbeat_once() -> LeaseRenewalStatus:
        supervisor.stop()
        return LeaseRenewalStatus.RENEWED

    supervisor.heartbeat_once = AsyncMock(side_effect=_heartbeat_once)  # type: ignore[method-assign]

    await supervisor.run()

    supervisor.heartbeat_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_repeats_after_interval_until_cancellation() -> None:
    session = MagicMock()
    supervisor = _supervisor(session)
    supervisor.heartbeat_once = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            LeaseRenewalStatus.RENEWED,
            LeaseRenewalStatus.CANCEL_REQUESTED,
        ]
    )

    async def _expire_interval(awaitable: object, *, timeout: float) -> None:
        assert timeout == 5.0
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()
        raise TimeoutError

    with patch(
        "general_ludd.event_loop.execution_supervision.asyncio.wait_for",
        new=AsyncMock(side_effect=_expire_interval),
    ):
        await supervisor.run()

    assert supervisor.heartbeat_once.await_count == 2


def test_event_bus_failure_never_breaks_lease_state_machine() -> None:
    session = MagicMock()
    event_bus = MagicMock()
    event_bus.publish.side_effect = RuntimeError("telemetry offline")
    supervisor = _supervisor(session, event_bus=event_bus)

    supervisor.publish("execution_lease_heartbeat", status="renewed")

    event_bus.publish.assert_called_once()
