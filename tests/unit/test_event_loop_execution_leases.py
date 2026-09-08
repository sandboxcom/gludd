"""Execution-lease integration contracts for the EventLoop dispatcher."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.execution_supervision import (
    ExecutionLeaseIdentity,
    LeaseTerminationOutcome,
    OwnedExecutionCancelled,
)
from general_ludd.event_loop.lease import LeaseBusyError
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import TodoStatus


def _todo(*, todo_id: str = "TODO-1", version: int = 8) -> SimpleNamespace:
    return SimpleNamespace(
        todo_id=todo_id,
        version=version,
        queue="core",
        work_type="code",
        project_id=None,
        confidence=None,
        resource_profile="low_resource",
    )


def _claim_loop(todo: SimpleNamespace) -> tuple[EventLoop, AsyncMock, AsyncMock]:
    session = AsyncMock()
    repo = AsyncMock()
    repo.count_active.return_value = 0
    repo.recover_queued_legacy_self_improve.return_value = []
    repo.claim_runnable.return_value = [todo]
    loop = EventLoop(session=session, todo_repo=repo)
    loop._active_session = session
    loop._tick_project_id = None
    return loop, repo, session


@pytest.mark.asyncio
async def test_claim_uses_process_stable_owner_and_todo_version_fence() -> None:
    todo = _todo()
    loop, _, _ = _claim_loop(todo)
    loop.config["event_loop"] = {
        "execution_lease_ttl_seconds": 90,
        "execution_lease_heartbeat_interval_seconds": 10.0,
    }

    with patch(
        "general_ludd.event_loop.lease.acquire_leases_batch",
        new_callable=AsyncMock,
    ) as acquire:
        await loop._phase_claim_runnable_todos()

    assert loop._lease_owner_id.startswith("event-loop-")
    assert loop._tick_state["claimed_todos"] == [todo]
    assert acquire.await_args.kwargs["holder_id"] == loop._lease_owner_id
    assert acquire.await_args.kwargs["todo_versions"] == {"core:TODO-1": 8}
    assert acquire.await_args.kwargs["ttl_seconds"] == 90
    assert loop._tick_state["execution_lease_versions"] == {"TODO-1": 8}


@pytest.mark.asyncio
async def test_lease_conflict_returns_claim_to_queue_and_dispatches_nothing() -> None:
    todo = _todo()
    loop, repo, _ = _claim_loop(todo)

    with patch(
        "general_ludd.event_loop.lease.acquire_leases_batch",
        new=AsyncMock(side_effect=LeaseBusyError("already owned")),
    ):
        await loop._phase_claim_runnable_todos()

    assert loop._tick_state["claimed_todos"] == []
    assert loop._tick_state["lease_conflict_todo_ids"] == ["TODO-1"]
    repo.transition.assert_awaited_once_with(
        "TODO-1",
        TodoStatus.QUEUED,
        8,
        project_id=None,
    )


@pytest.mark.asyncio
async def test_invalid_lease_timing_returns_claim_to_queue_without_acquiring() -> None:
    todo = _todo()
    loop, repo, _ = _claim_loop(todo)
    loop.config["event_loop"] = {
        "execution_lease_ttl_seconds": 30,
        "execution_lease_heartbeat_interval_seconds": 30.0,
    }

    with patch(
        "general_ludd.event_loop.lease.acquire_leases_batch",
        new_callable=AsyncMock,
    ) as acquire:
        await loop._phase_claim_runnable_todos()

    acquire.assert_not_awaited()
    assert loop._tick_state["claimed_todos"] == []
    repo.transition.assert_awaited_once()


def _session_factory(session: MagicMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=context)
    return factory


@pytest.mark.asyncio
async def test_normal_isolated_dispatch_releases_only_exact_owner_lease() -> None:
    todo = _todo()
    session = MagicMock()
    session.commit = AsyncMock()
    loop = EventLoop(session=None)
    loop._session_factory = _session_factory(session)
    loop._tick_state["execution_lease_todo_ids"] = [todo.todo_id]
    loop._dispatch_execute_job = AsyncMock()  # type: ignore[method-assign]

    with patch(
        "general_ludd.event_loop.loop.release_lease",
        new_callable=AsyncMock,
    ) as release:
        await loop._dispatch_execute_job_isolated(todo)

    release.assert_awaited_once_with(
        session,
        "core:TODO-1",
        holder_id=loop._lease_owner_id,
    )
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_isolated_dispatch_heartbeats_exact_claim_until_release() -> None:
    todo = _todo()
    session = MagicMock()
    session.commit = AsyncMock()
    loop = EventLoop(session=None)
    loop._session_factory = _session_factory(session)
    loop._tick_state["execution_lease_todo_ids"] = [todo.todo_id]
    loop._tick_state["execution_lease_versions"] = {todo.todo_id: todo.version}
    loop._dispatch_execute_job = AsyncMock()  # type: ignore[method-assign]
    supervisor = MagicMock()
    supervisor.run = AsyncMock()
    supervisor.stop = MagicMock()
    loop._execution_lease_supervisor_for_todo = MagicMock(  # type: ignore[method-assign]
        return_value=supervisor
    )

    with patch(
        "general_ludd.event_loop.loop.release_lease",
        new_callable=AsyncMock,
    ) as release:
        await loop._dispatch_execute_job_isolated(todo)

    loop._dispatch_execute_job.assert_awaited_once()
    assert (
        loop._dispatch_execute_job.await_args.kwargs["_lease_supervisor_override"]
        is supervisor
    )
    supervisor.run.assert_awaited_once()
    supervisor.stop.assert_called()
    release.assert_awaited_once()


def test_supervisor_factory_uses_claimed_version_and_event_bus() -> None:
    todo = _todo()
    session = MagicMock()
    factory = _session_factory(session)
    event_bus = MagicMock()
    loop = EventLoop(
        session=None,
        event_bus=event_bus,
        config={
            "event_loop": {
                "execution_lease_ttl_seconds": 90,
                "execution_lease_heartbeat_interval_seconds": 10.0,
            }
        },
    )
    loop._session_factory = factory
    loop._tick_state["execution_lease_todo_ids"] = [todo.todo_id]
    loop._tick_state["execution_lease_versions"] = {todo.todo_id: todo.version}

    with patch(
        "general_ludd.event_loop.loop.ExecutionLeaseSupervisor",
    ) as supervisor_type:
        supervisor = loop._execution_lease_supervisor_for_todo(todo)

    assert supervisor is supervisor_type.return_value
    kwargs = supervisor_type.call_args.kwargs
    assert kwargs["session_factory"] is factory
    assert kwargs["event_bus"] is event_bus
    assert kwargs["identity"] == ExecutionLeaseIdentity(
        bucket_key="core:TODO-1",
        holder_id=loop._lease_owner_id,
        todo_version=8,
    )
    assert kwargs["ttl_seconds"] == 90
    assert kwargs["heartbeat_interval_seconds"] == 10.0


@pytest.mark.asyncio
async def test_owned_runner_receives_thread_safe_lease_cancel_callback() -> None:
    runner = MagicMock()
    runner.prepare_job_dirs.return_value = {"root": "/tmp/EXEC-TODO-1"}
    runner.write_vars.return_value = "/tmp/EXEC-TODO-1/env/extravars"
    runner.run_playbook.return_value = {"status": "successful", "rc": 0}
    loop = EventLoop(session=None, runner=runner)
    todo = _todo()
    todo.work_type = "maintenance"
    supervisor = MagicMock()
    supervisor.is_cancellation_requested.return_value = False

    async def _passthrough(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    with patch(
        "general_ludd.event_loop.loop.asyncio.to_thread",
        new=AsyncMock(side_effect=_passthrough),
    ):
        await loop._dispatch_execute_job(
            todo,
            _lease_supervisor_override=supervisor,
        )

    callback = runner.run_playbook.call_args.kwargs["cancel_requested"]
    assert callback is supervisor.is_cancellation_requested


@pytest.mark.asyncio
async def test_coroutine_cancel_waits_for_owned_runner_terminal_proof() -> None:
    runner_started = threading.Event()
    allow_runner_exit = threading.Event()
    runner_stopped = threading.Event()
    cancel_signal = threading.Event()
    runner = MagicMock()

    def _blocking_runner(**kwargs: Any) -> dict[str, Any]:
        runner_started.set()
        assert allow_runner_exit.wait(timeout=5.0)
        assert kwargs["cancel_requested"]() is True
        runner_stopped.set()
        return {"status": "cancelled", "rc": 130}

    runner.run_playbook.side_effect = _blocking_runner
    loop = EventLoop(session=None, runner=runner)
    supervisor = MagicMock()
    supervisor.is_cancellation_requested.side_effect = cancel_signal.is_set

    async def _request() -> bool:
        cancel_signal.set()
        allow_runner_exit.set()
        return True

    async def _confirm() -> LeaseTerminationOutcome:
        assert runner_stopped.is_set()
        return LeaseTerminationOutcome(confirmed=True, reclaimed=1)

    supervisor.request_cancellation = AsyncMock(side_effect=_request)
    supervisor.confirm_termination = AsyncMock(side_effect=_confirm)
    task = asyncio.create_task(
        loop._run_playbook_with_lease_supervision(
            supervisor,
            playbook="noop.yml",
            private_data_dir="/tmp/EXEC-TODO-1",
            env={},
        )
    )
    while not runner_started.is_set():
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runner_stopped.is_set()
    supervisor.request_cancellation.assert_awaited_once()
    supervisor.confirm_termination.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_owned_runner_confirms_before_requeue_and_never_releases() -> None:
    todo = _todo()
    session = MagicMock()
    session.commit = AsyncMock()
    loop = EventLoop(session=None)
    loop._session_factory = _session_factory(session)
    loop._tick_state["execution_lease_todo_ids"] = [todo.todo_id]
    loop._tick_state["execution_lease_versions"] = {todo.todo_id: todo.version}
    supervisor = MagicMock()
    supervisor.run = AsyncMock()
    supervisor.stop = MagicMock()
    supervisor.request_cancellation = AsyncMock(return_value=True)
    supervisor.confirm_termination = AsyncMock(
        return_value=LeaseTerminationOutcome(confirmed=True, reclaimed=1)
    )
    loop._execution_lease_supervisor_for_todo = MagicMock(  # type: ignore[method-assign]
        return_value=supervisor
    )
    loop._dispatch_execute_job = AsyncMock(  # type: ignore[method-assign]
        side_effect=OwnedExecutionCancelled
    )

    with (
        patch(
            "general_ludd.event_loop.loop.release_lease",
            new_callable=AsyncMock,
        ) as release,
        pytest.raises(OwnedExecutionCancelled),
    ):
        await loop._dispatch_execute_job_isolated(todo)

    supervisor.request_cancellation.assert_awaited_once()
    supervisor.confirm_termination.assert_awaited_once()
    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_isolated_dispatch_failure_retains_lease() -> None:
    todo = _todo()
    session = MagicMock()
    session.commit = AsyncMock()
    loop = EventLoop(session=None)
    loop._session_factory = _session_factory(session)
    loop._tick_state["execution_lease_todo_ids"] = [todo.todo_id]
    loop._tick_state["execution_lease_versions"] = {todo.todo_id: todo.version}
    supervisor = MagicMock()
    supervisor.run = AsyncMock()
    supervisor.stop = MagicMock()
    supervisor.request_cancellation = AsyncMock()
    supervisor.confirm_termination = AsyncMock()
    loop._execution_lease_supervisor_for_todo = MagicMock(  # type: ignore[method-assign]
        return_value=supervisor
    )
    loop._dispatch_execute_job = AsyncMock(  # type: ignore[method-assign]
        side_effect=ConnectionError("response lost")
    )

    with (
        patch(
            "general_ludd.event_loop.loop.release_lease",
            new_callable=AsyncMock,
        ) as release,
        pytest.raises(ConnectionError, match="response lost"),
    ):
        await loop._dispatch_execute_job_isolated(todo)

    release.assert_not_awaited()
    supervisor.request_cancellation.assert_not_awaited()
    supervisor.confirm_termination.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_active_without_terminal_proof_is_never_requeued() -> None:
    todo = _todo()
    session = AsyncMock()
    candidates = MagicMock()
    candidates.scalars.return_value.all.return_value = [todo]
    no_leases = MagicMock()
    no_leases.scalars.return_value.all.return_value = []
    session.execute.side_effect = [candidates, no_leases]
    repo = AsyncMock()
    loop = EventLoop(session=session, todo_repo=repo)
    loop._active_session = session

    await loop._reap_stuck_todos()

    repo.transition.assert_not_awaited()
    assert loop._tick_state["unfenced_stuck_todo_ids"] == {"TODO-1"}
