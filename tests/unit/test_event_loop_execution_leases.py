"""Execution-lease integration contracts for the EventLoop dispatcher."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

    with patch(
        "general_ludd.event_loop.lease.acquire_leases_batch",
        new_callable=AsyncMock,
    ) as acquire:
        await loop._phase_claim_runnable_todos()

    assert loop._lease_owner_id.startswith("event-loop-")
    assert loop._tick_state["claimed_todos"] == [todo]
    assert acquire.await_args.kwargs["holder_id"] == loop._lease_owner_id
    assert acquire.await_args.kwargs["todo_versions"] == {"core:TODO-1": 8}


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
async def test_uncertain_isolated_dispatch_failure_retains_lease() -> None:
    todo = _todo()
    session = MagicMock()
    session.commit = AsyncMock()
    loop = EventLoop(session=None)
    loop._session_factory = _session_factory(session)
    loop._tick_state["execution_lease_todo_ids"] = [todo.todo_id]
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
