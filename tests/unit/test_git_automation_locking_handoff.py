"""Public task-handoff and retained ownership contracts for the Git mutex."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from general_ludd.git_automation import locking


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a private repository-shaped lock anchor without invoking Git."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


@pytest.mark.asyncio
async def test_live_task_handoff_cannot_allow_critical_section_overlap(git_repo: Path) -> None:
    """A foreign entry must be refused or fenced from its acquiring task."""
    repo = str(git_repo)
    handed = await locking.async_git_repo_lock(repo, timeout=0.3)
    ready, release = asyncio.Event(), asyncio.Event()
    foreign_entered, overlap = False, False
    refusal: str | None = None

    async def consumer() -> None:
        nonlocal foreign_entered, refusal
        try:
            with handed:
                foreign_entered = True
                ready.set()
                await release.wait()
        except RuntimeError as exc:
            refusal = str(exc)
            ready.set()

    other = asyncio.create_task(consumer())
    try:
        await asyncio.wait_for(ready.wait(), timeout=1.0)
        if foreign_entered:
            try:
                acquired_again = await locking.async_git_repo_lock(repo, timeout=0.1)
            except TimeoutError:
                pass
            else:
                with acquired_again:
                    overlap = True
        else:
            assert refusal is not None and "owner" in refusal.lower()
    finally:
        release.set()
        await asyncio.wait_for(other, timeout=1.0)
        handed.__exit__(None, None, None)
    assert not overlap, "original task re-entered while foreign task held the handed context"
    assert not locking._async_workers


@pytest.mark.asyncio
async def test_wait_for_result_binds_nested_use_to_consuming_task(git_repo: Path) -> None:
    """A completed acquisition task can hand its sole context to its consumer."""
    repo = str(git_repo)
    handed = await asyncio.wait_for(locking.async_git_repo_lock(repo, timeout=0.3), timeout=1.0)
    with handed:
        nested = await locking.async_git_repo_lock(repo, timeout=0.1)
        with nested:
            pass
    assert not locking._async_workers


@pytest.mark.asyncio
async def test_completed_owner_transfer_refuses_multiple_reservations_without_changes(git_repo: Path) -> None:
    """An ambiguous transfer preserves both native reservations for cleanup."""
    repo = str(git_repo)

    async def reserve_pair() -> tuple[AbstractContextManager[None], AbstractContextManager[None]]:
        outer = await locking.async_git_repo_lock(repo, timeout=0.3)
        inner = await locking.async_git_repo_lock(repo, timeout=0.3)
        return outer, inner

    acquisition = asyncio.create_task(reserve_pair())
    outer, inner = await acquisition
    key = locking._normalize(str(git_repo / ".git"))
    token = (acquisition, key)
    worker = locking._async_workers[token]
    before = dict(locking._async_workers)
    try:
        with pytest.raises(RuntimeError, match="one reservation"):
            outer.__enter__()
        assert locking._async_workers == before
        assert worker.owner_token == token and worker.users == 2
        assert locking._file_lock_depth[key] == 2
        # Resolving the actual ambiguity permits the one remaining result.
        inner.__exit__(None, None, None)
        with outer:
            assert worker.owner_token[0] is asyncio.current_task()
            assert worker.users == 1
    finally:
        inner.__exit__(None, None, None)
        outer.__exit__(None, None, None)
    assert not locking._async_workers
    assert locking._file_lock_depth[key] == 0


@pytest.mark.asyncio
async def test_completed_owner_transfer_preserves_conflicting_worker_and_original_binding(git_repo: Path) -> None:
    """An occupied consumer identity cannot displace another reservation."""
    repo = str(git_repo)
    acquisition = asyncio.create_task(locking.async_git_repo_lock(repo, timeout=0.3))
    handed = await acquisition
    consumer = asyncio.current_task()
    assert consumer is not None
    key = locking._normalize(str(git_repo / ".git"))
    token = (acquisition, key)
    worker = locking._async_workers[token]
    conflicting = locking._AsyncGitLockWorker((consumer, key))
    conflicting.users = 1
    with locking._async_workers_guard:
        locking._async_workers[(consumer, key)] = conflicting
    before = dict(locking._async_workers)
    try:
        with pytest.raises(RuntimeError, match="conflicts with a registered worker"):
            handed.__enter__()
        assert locking._async_workers == before
        assert worker.owner_token == token and worker.users == 1
        assert conflicting.owner_token == (consumer, key) and conflicting.users == 1
        assert locking._file_lock_depth[key] == 1
    finally:
        locking._release_async_worker(conflicting)
        handed.__exit__(None, None, None)
    assert not locking._async_workers
    assert locking._file_lock_depth[key] == 0


def test_completed_loop_result_retains_public_synchronous_context_use(git_repo: Path) -> None:
    """The returned synchronous context remains usable after its loop closes."""
    acquired = asyncio.run(locking.async_git_repo_lock(str(git_repo), timeout=0.3))
    with acquired:
        key = locking._normalize(str(git_repo / ".git"))
        assert locking._file_lock_depth[key] == 1
    assert not locking._async_workers
    assert locking._file_lock_depth[key] == 0
