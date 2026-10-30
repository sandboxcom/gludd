"""Adversarial lifecycle tests for the asynchronous Git repository mutex."""

from __future__ import annotations

import asyncio
import contextlib
import gc
import multiprocessing
import os
import threading
import time
from collections.abc import Generator
from multiprocessing.connection import Connection
from pathlib import Path
from types import TracebackType
from typing import Literal
from unittest.mock import patch

import pytest

from general_ludd.git_automation import locking


class _ThreadAffineTracker:
    """Model the thread ownership rules of ``threading.RLock``."""

    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.owner: int | None = None
        self.depth = 0
        self.exits = 0

    def context(self) -> _ThreadAffineContext:
        """Return another context sharing this logical mutex."""
        return _ThreadAffineContext(self)


class _ThreadAffineContext:
    """Fail quickly when logical re-entry moves to another worker thread."""

    def __init__(self, tracker: _ThreadAffineTracker) -> None:
        self.tracker = tracker

    def __enter__(self) -> None:
        ident = threading.get_ident()
        with self.tracker.guard:
            if self.tracker.owner is None:
                self.tracker.owner = ident
            elif self.tracker.owner != ident:
                raise RuntimeError("logical re-entry moved to a foreign thread")
            self.tracker.depth += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        ident = threading.get_ident()
        with self.tracker.guard:
            assert self.tracker.owner == ident
            self.tracker.depth -= 1
            self.tracker.exits += 1
            if self.tracker.depth == 0:
                self.tracker.owner = None


class _BlockingContext:
    """Controllable acquire used to exercise every cancellation boundary."""

    def __init__(self, entered: threading.Event, proceed: threading.Event) -> None:
        self.entered = entered
        self.proceed = proceed
        self.exit_count = 0

    def __enter__(self) -> None:
        self.entered.set()
        assert self.proceed.wait(timeout=2.0)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.exit_count += 1


class _TwoPhaseContext(_BlockingContext):
    """Expose both the acquire-cleanup and release-cleanup await boundaries."""

    def __init__(
        self,
        entered: threading.Event,
        proceed: threading.Event,
        exit_started: threading.Event,
        exit_proceed: threading.Event,
    ) -> None:
        super().__init__(entered, proceed)
        self.exit_started = exit_started
        self.exit_proceed = exit_proceed

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_started.set()
        assert self.exit_proceed.wait(timeout=2.0)
        super().__exit__(exc_type, exc, traceback)


class _RecordingContext:
    """Record releases and the exception passed through a wrapper."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.exited = threading.Event()
        self.exit_exception: BaseException | None = None

    def __enter__(self) -> None:
        self.entered.set()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, traceback
        self.exit_exception = exc
        self.exited.set()
        return False


@pytest.fixture
def git_repo(tmp_path: Path) -> Generator[Path]:
    """Provide a minimal repository shape with a real lock-file anchor."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    yield repo


@pytest.mark.asyncio
async def test_cancelled_acquire_cleanup_keeps_event_loop_responsive() -> None:
    """Cancellation cleanup must await the worker without blocking the loop."""
    entered = threading.Event()
    proceed = threading.Event()
    context = _BlockingContext(entered, proceed)
    beats = 0
    stop_heartbeat = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal beats
        while not stop_heartbeat.is_set():
            beats += 1
            await asyncio.sleep(0.01)

    with patch.object(locking, "git_repo_lock", return_value=context):
        acquire = asyncio.create_task(locking.async_git_repo_lock("."))
        assert await asyncio.to_thread(entered.wait, 1.0)
        pulse = asyncio.create_task(heartbeat())
        timer = threading.Timer(0.15, proceed.set)
        timer.start()
        acquire.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await acquire
        finally:
            timer.cancel()
            stop_heartbeat.set()
            await pulse

    assert beats >= 3, "mutex cleanup blocked the event loop"
    assert context.exit_count == 1


@pytest.mark.asyncio
async def test_repeated_cancellation_still_releases_exactly_once() -> None:
    """Cancellation at both cleanup awaits cannot orphan the mutex."""
    entered = threading.Event()
    proceed = threading.Event()
    exit_started = threading.Event()
    exit_proceed = threading.Event()
    context = _TwoPhaseContext(entered, proceed, exit_started, exit_proceed)
    loop = asyncio.get_running_loop()

    with patch.object(locking, "git_repo_lock", return_value=context):
        acquire = asyncio.create_task(locking.async_git_repo_lock("."))
        assert await asyncio.to_thread(entered.wait, 1.0)
        acquire.cancel()

        def cancel_release_boundary() -> None:
            assert exit_started.wait(timeout=1.0)
            loop.call_soon_threadsafe(acquire.cancel)
            exit_proceed.set()

        second_cancel = threading.Thread(target=cancel_release_boundary)
        second_cancel.start()
        proceed.set()
        try:
            with pytest.raises(asyncio.CancelledError):
                await acquire
        finally:
            exit_proceed.set()
            second_cancel.join(timeout=1.0)

    assert context.exit_count == 1


@pytest.mark.asyncio
async def test_nested_async_use_reuses_owner_worker() -> None:
    """One task may re-enter a repo mutex without changing thread owner."""
    tracker = _ThreadAffineTracker()

    with patch.object(
        locking,
        "git_repo_lock",
        side_effect=lambda *args, **kwargs: tracker.context(),
    ):
        outer = await locking.async_git_repo_lock("repo")
        with outer:
            inner = await locking.async_git_repo_lock("repo")
            with inner:
                assert tracker.depth == 2

    assert tracker.depth == 0
    assert tracker.exits == 2


@pytest.mark.asyncio
async def test_abandoned_acquired_context_releases_when_task_dies() -> None:
    """A cancelled task cannot strand an acquired-but-not-entered wrapper."""
    context = _RecordingContext()
    parked = asyncio.Event()

    async def owner() -> None:
        acquired = await locking.async_git_repo_lock("repo")
        assert acquired is not None
        parked.set()
        await asyncio.Future()

    with patch.object(locking, "git_repo_lock", return_value=context):
        task = asyncio.create_task(owner())
        await asyncio.wait_for(parked.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        del task
        await asyncio.to_thread(gc.collect)

    assert context.exited.wait(timeout=1.0), "dead task stranded its mutex"


@pytest.mark.asyncio
async def test_same_repo_tasks_serialize_and_owner_is_fenced(git_repo: Path) -> None:
    """A foreign task waits while the recorded process/thread owner holds."""
    outer = await locking.async_git_repo_lock(str(git_repo), timeout=1.0)
    contender_entered = asyncio.Event()

    async def contend() -> None:
        inner = await locking.async_git_repo_lock(str(git_repo), timeout=1.0)
        with inner:
            contender_entered.set()

    with outer:
        key = locking._normalize(str(git_repo / ".git"))
        owner = locking._file_lock_owner[key]
        assert owner[0] == os.getpid()
        assert owner[1] != threading.get_ident()
        contender = asyncio.create_task(contend())
        await asyncio.sleep(0.05)
        assert not contender_entered.is_set()

    await asyncio.wait_for(contender, timeout=1.0)
    assert contender_entered.is_set()


@pytest.mark.asyncio
async def test_different_repo_tasks_overlap(tmp_path: Path) -> None:
    """Independent repositories retain parallel forward progress."""
    repos = [tmp_path / name for name in ("a", "b")]
    for repo in repos:
        repo.mkdir()
        (repo / ".git").mkdir()
    both_entered = asyncio.Event()
    entered = 0
    release = asyncio.Event()

    async def hold(repo: Path) -> None:
        nonlocal entered
        context = await locking.async_git_repo_lock(str(repo), timeout=1.0)
        with context:
            entered += 1
            if entered == 2:
                both_entered.set()
            await release.wait()

    holders = [asyncio.create_task(hold(repo)) for repo in repos]
    await asyncio.wait_for(both_entered.wait(), timeout=1.0)
    release.set()
    await asyncio.gather(*holders)


def test_stale_unowned_metadata_recovers_without_inode_replacement(git_repo: Path) -> None:
    """Stale metadata is refreshed while the stable fencing inode survives."""
    lock_path = git_repo / ".git" / locking._LOCK_FILENAME
    lock_path.touch()
    stale = time.time() - 10_000
    os.utime(lock_path, (stale, stale))
    inode = lock_path.stat().st_ino

    with locking.git_repo_lock(str(git_repo), timeout=1.0, stale_after=1.0):
        assert lock_path.stat().st_ino == inode
        assert lock_path.stat().st_mtime > stale


@pytest.mark.asyncio
async def test_exception_group_propagates_intact_after_release() -> None:
    """Release preserves the original aggregate failure and its leaves."""
    context = _RecordingContext()
    aggregate = ExceptionGroup("body failures", [ValueError("a"), OSError("b")])

    with patch.object(locking, "git_repo_lock", return_value=context):
        acquired = await locking.async_git_repo_lock("repo")
        with pytest.raises(ExceptionGroup) as caught, acquired:
            raise aggregate

    assert caught.value is aggregate
    assert context.exit_exception is aggregate
    assert context.exited.is_set()


@pytest.mark.asyncio
async def test_entered_wrapper_is_single_use_and_owner_fenced() -> None:
    """One acquired wrapper cannot impersonate nested or future ownership."""
    context = _RecordingContext()

    with patch.object(locking, "git_repo_lock", return_value=context):
        acquired = await locking.async_git_repo_lock("repo")
        assert acquired.__enter__() is None
        with pytest.raises(RuntimeError, match="already entered"):
            acquired.__enter__()
        acquired.__exit__(None, None, None)
        with pytest.raises(RuntimeError, match="already closed"):
            acquired.__enter__()


def test_fork_reset_discards_inherited_descriptors_and_registries() -> None:
    """A child cannot inherit Python ownership or a parent lock descriptor."""
    key = "adversarial-fork-reset"
    inherited_fd, peer_fd = os.pipe()
    old_registry_guard = locking._registry_guard
    old_async_guard = locking._async_workers_guard
    locking._file_lock_fds[key] = inherited_fd
    locking._file_lock_depth[key] = 1
    locking._file_lock_owner[key] = (os.getpid(), threading.get_ident())
    locking._repo_locks[key] = threading.RLock()
    try:
        locking._reset_after_fork()
        with pytest.raises(OSError):
            os.fstat(inherited_fd)
        assert not locking._file_lock_fds
        assert not locking._file_lock_depth
        assert not locking._file_lock_owner
        assert not locking._repo_locks
        assert not locking._async_workers
        assert locking._registry_guard is not old_registry_guard
        assert locking._async_workers_guard is not old_async_guard
    finally:
        with contextlib.suppress(OSError):
            os.close(inherited_fd)
        os.close(peer_fd)


def _crash_with_lock(repo_path: str, sender: Connection) -> None:
    """Acquire the kernel mutex and simulate abrupt process death."""
    with locking.git_repo_lock(repo_path, timeout=1.0, stale_after=60.0):
        sender.send("held")
        sender.close()
        os._exit(73)


def test_process_death_releases_kernel_mutex(git_repo: Path) -> None:
    """Kernel descriptor cleanup hands a crashed owner's repo to a successor."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_crash_with_lock, args=(str(git_repo), sender))
    try:
        process.start()
        sender.close()
        assert receiver.poll(5.0)
        assert receiver.recv() == "held"
        process.join(timeout=5.0)
        assert process.exitcode == 73
        with locking.git_repo_lock(str(git_repo), timeout=1.0, stale_after=60.0):
            pass
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)
        receiver.close()
        with contextlib.suppress(OSError):
            sender.close()
        process.close()


@pytest.mark.asyncio
async def test_cancelled_waiter_hands_off_without_repo_downtime(git_repo: Path) -> None:
    """A cancelled middle waiter releases before the next operation starts."""
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder() -> None:
        context = await locking.async_git_repo_lock(str(git_repo), timeout=1.0)
        with context:
            holder_ready.set()
            await release_holder.wait()

    owner = asyncio.create_task(holder())
    await asyncio.wait_for(holder_ready.wait(), timeout=1.0)
    waiter = asyncio.create_task(locking.async_git_repo_lock(str(git_repo), timeout=1.0))
    await asyncio.sleep(0.02)
    waiter.cancel()
    release_holder.set()
    await owner
    with pytest.raises(asyncio.CancelledError):
        await waiter

    successor = await asyncio.wait_for(
        locking.async_git_repo_lock(str(git_repo), timeout=1.0),
        timeout=1.0,
    )
    with successor:
        pass
