"""Per-repo git serialization (issue #63).

gludd runs roles in PARALLEL, and many of those roles call git against the
SAME working tree at the same time. Concurrent mutating git invocations race on
``.git/index.lock`` (git aborts with "Another git process is running for this
repository"), on ``HEAD``, and on the commit graph (lost / interleaved commits).
There is no serialization in plain ``subprocess.run(["git", ...])``.

``git_repo_lock(repo_path)`` is the serialization choke point. It implements
TWO layers so the guarantee holds both inside one daemon process and across
several processes sharing a repo on disk:

(a) **In-process re-entrant lock registry** — keyed by
    ``os.path.realpath(repo_path)`` so every thread / coroutine in one daemon
    that touches a given repo serializes on the SAME lock object. The lock is
    re-entrant (``threading.RLock``) so a nested git call on the same repo from
    the same thread (e.g. ``commit()`` calling ``_run_git`` three times, or a
    helper that itself runs git while already holding the lock) does NOT
    self-deadlock.

(b) **Cross-process file lock** — an advisory ``flock`` on
    ``<repo>/.git/gludd-git.lock``. A second daemon / a stray external git
    wrapper that goes through this lock will block until the holder releases.
    The file lock has a configurable acquire timeout. The inode is never
    unlinked: the kernel releases ``flock`` ownership when the last owning
    descriptor closes, including abnormal process exit, while a stable inode
    prevents split-brain locking between old and newly created files.

This module is the central serializer wired into
``git_automation/repo.py``'s ``_run_git`` (the single choke point every
GitAutomation git call flows through). Other modules that shell out to git
directly against a shared repo — notably ``worktree/core.py``,
``execution/engine.py``, ``pr_delivery.py``, and ``git_intel.py`` — own their
own call sites and SHOULD adopt ``git_repo_lock`` around their mutating git
invocations too; this module is deliberately import-light so they can.

``_run_git`` is synchronous (plain ``subprocess.run``), so the primary API is a
synchronous context manager. ``async_git_repo_lock`` is provided for async
callers (it acquires the same locks off the event loop via ``run_in_executor``
so it never blocks the loop while waiting on a contended repo).
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import contextlib
import errno
import logging
import os
import re
import shlex
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from types import CodeType, TracebackType
from typing import Protocol, TypeVar

logger = logging.getLogger(__name__)

# Name of the per-repo cross-process lock file, placed inside ``.git`` so it
# travels with the repo and is naturally excluded from the work tree.
_LOCK_FILENAME = "gludd-git.lock"

# How long to wait across both in-process and cross-process acquisition.
_DEFAULT_ACQUIRE_TIMEOUT = 60.0

# If the lock file has not been touched in this long, treat the holder as dead
# (crashed without releasing) and break the lock. Must be comfortably larger
# than the longest legitimate git op (clone uses a 120s timeout in repo.py).
_DEFAULT_STALE_AFTER = 300.0

# Poll interval while waiting for a contended file lock.
_POLL_INTERVAL = 0.05


# --- in-process re-entrant lock registry ---------------------------------

# Guards the registry dict itself; held only for the dict lookup, never while
# a per-repo lock is held (so it can never become a contention bottleneck).
_registry_guard = threading.Lock()
_repo_locks: dict[str, threading.RLock] = {}

# Re-entrancy depth for the CROSS-PROCESS file lock, per repo key. flock is
# advisory per open-file-description, NOT re-entrant across separate ``os.open``
# fds in the same process — a nested ``git_repo_lock`` opening a second fd would
# block on itself. We are always holding the per-repo RLock (a single thread)
# when we touch this, so a plain int keyed by repo is safe: depth 0 -> actually
# flock; depth > 0 -> a nested re-entry that must NOT re-flock.
_file_lock_depth: dict[str, int] = {}

# Re-entrant ownership must include the process as well as the thread. A forked
# child inherits Python dictionaries but is a distinct contender and must never
# mistake its parent's depth counter for its own ownership.
_file_lock_owner: dict[str, tuple[int, int]] = {}

# Descriptors currently participating in acquisition or ownership. The child
# side of ``fork`` closes its inherited copies before clearing Python lock
# state, so a dead parent cannot leave the mutex held through the child.
_file_lock_fds: dict[str, int] = {}


class _AsyncGitLockWorker:
    """One thread-affine lock worker owned by one task and repository."""

    def __init__(self, owner_token: tuple[object, str]) -> None:
        self.owner_token = owner_token
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="gludd-git-lock",
        )
        self.users = 0


# A nested async acquisition must execute on the same worker that owns the
# underlying RLock. Different tasks intentionally get different workers so a
# foreign task cannot be mistaken for a re-entrant owner.
_async_workers_guard = threading.Lock()
_async_workers: dict[tuple[object, str], _AsyncGitLockWorker] = {}

_NativeResult = TypeVar("_NativeResult")


def _reset_after_fork() -> None:
    """Discard parent-owned mutex state in a newly forked child."""
    global _async_workers_guard, _registry_guard

    for fd in tuple(_file_lock_fds.values()):
        with contextlib.suppress(OSError):
            os.close(fd)
    _file_lock_fds.clear()
    _file_lock_depth.clear()
    _file_lock_owner.clear()
    _repo_locks.clear()
    _async_workers.clear()
    _registry_guard = threading.Lock()
    _async_workers_guard = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def _normalize(repo_path: str) -> str:
    """Canonical key for a repo path.

    ``os.path.realpath`` collapses symlinks and ``..`` so two different spellings
    of the same repo (``./repo`` vs ``/abs/repo`` vs a symlinked path) map to one
    lock object. Falls back to ``abspath`` if the path does not yet exist.
    """
    try:
        return os.path.realpath(repo_path)
    except OSError:
        return os.path.abspath(repo_path)


def _get_inprocess_lock(key: str) -> threading.RLock:
    with _registry_guard:
        lock = _repo_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _repo_locks[key] = lock
        return lock


# --- cross-process file lock ---------------------------------------------

# fcntl is POSIX-only. On platforms without it (Windows) we degrade to the
# in-process lock alone rather than failing to import.
try:
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX fallback
    _HAVE_FCNTL = False


_GIT_PATH_FILE_MAX_BYTES = 4096


def _resolve_git_path(value: str, *, relative_to: str) -> str | None:
    """Resolve one bounded Git metadata value without invoking Git."""
    if len(value.encode("utf-8")) > _GIT_PATH_FILE_MAX_BYTES:
        return None
    lines = value.splitlines()
    if len(lines) != 1 or not lines[0]:
        return None
    candidate = lines[0]
    if not os.path.isabs(candidate):
        candidate = os.path.join(relative_to, candidate)
    # Preserve the caller's absolute path spelling here (notably macOS
    # ``/tmp`` versus ``/private/tmp``).  The lock registry canonicalizes the
    # result through ``_normalize`` before comparing repository identities, so
    # symlink aliases still converge on one mutex without making this metadata
    # helper return a surprising path spelling.
    resolved = os.path.abspath(candidate)
    return resolved if os.path.isdir(resolved) else None


def _restore_anchor_spelling(path: str, *, anchor: str) -> str:
    """Express ``path`` through the nearest lexical alias used by ``anchor``.

    Git records physical paths in linked-worktree metadata on some platforms
    (for example ``/private/tmp`` on macOS), while callers may enter the same
    checkout through a stable lexical alias (``/tmp``).  Walk the anchor's
    ancestors until one physically contains the resolved metadata path, then
    rebuild the suffix from that lexical ancestor.  Repository identity still
    converges through :func:`_normalize` before lock lookup.
    """
    resolved = os.path.abspath(path)
    try:
        physical_path = os.path.realpath(resolved)
    except OSError:
        physical_path = resolved
    lexical_ancestor = os.path.abspath(anchor)
    while True:
        try:
            physical_ancestor = os.path.realpath(lexical_ancestor)
            contains_path = os.path.commonpath((physical_path, physical_ancestor)) == physical_ancestor
        except (OSError, ValueError):
            contains_path = False
        if contains_path:
            suffix = os.path.relpath(physical_path, physical_ancestor)
            if suffix == os.curdir:
                return lexical_ancestor
            return os.path.join(lexical_ancestor, suffix)
        parent = os.path.dirname(lexical_ancestor)
        if parent == lexical_ancestor:
            return resolved
        lexical_ancestor = parent


def _read_git_path(path: str, *, relative_to: str) -> str | None:
    """Read and resolve one bounded Git metadata path file."""
    try:
        with open(path, encoding="utf-8") as stream:
            value = stream.read(_GIT_PATH_FILE_MAX_BYTES + 1)
    except (OSError, UnicodeError):
        return None
    return _resolve_git_path(value, relative_to=relative_to)


def _git_dir(repo_path: str) -> str | None:
    """Return the ``.git`` directory for ``repo_path`` if one exists.

    When ``repo_path`` is the root of a regular checkout, returns ``<repo>/.git``
    (a directory). When ``repo_path`` is inside a git worktree, ``.git`` is a
    FILE containing ``gitdir: <path>`` — in that case the bounded Git metadata
    is resolved directly. A linked worktree's private git directory contains a
    ``commondir`` file whose path is relative to that private directory. Reading
    these two documented files avoids a recursive Git invocation and keeps test
    doubles for the caller's actual Git command isolated.

    If no ``.git`` exists yet (uninitialised) or the rev-parse fails, returns
    ``None`` and the caller falls back to the in-process lock alone.
    """
    git_dir = os.path.join(repo_path, ".git")
    if os.path.isdir(git_dir):
        return git_dir
    if os.path.isfile(git_dir):
        try:
            with open(git_dir, encoding="utf-8") as stream:
                pointer = stream.read(_GIT_PATH_FILE_MAX_BYTES + 1)
        except (OSError, UnicodeError):
            logger.debug(
                "Git metadata read failed for %s; cross-process lock will not be available",
                repo_path,
            )
            return None
        if len(pointer.encode("utf-8")) > _GIT_PATH_FILE_MAX_BYTES:
            return None
        lines = pointer.splitlines()
        if len(lines) != 1 or not lines[0].startswith("gitdir: "):
            return None
        private_dir = _resolve_git_path(
            lines[0].removeprefix("gitdir: "),
            relative_to=repo_path,
        )
        if private_dir is None:
            return None
        common_path = os.path.join(private_dir, "commondir")
        if not os.path.exists(common_path):
            return _restore_anchor_spelling(private_dir, anchor=repo_path)
        common_dir = _read_git_path(common_path, relative_to=private_dir)
        if common_dir is None:
            return None
        return _restore_anchor_spelling(common_dir, anchor=repo_path)
    return None


def git_common_directory(repo_path: str) -> str | None:
    """Return the shared Git directory for a checkout or linked worktree."""
    value = _git_dir(repo_path)
    return os.path.realpath(value) if value is not None else None


def _break_if_stale(lock_path: str, stale_after: float) -> bool:
    """Report stale lock metadata without unlinking the mutex inode.

    We use the file's mtime as a liveness signal: a live holder bumps it (via
    ``os.utime``) on acquisition. A stale timestamp is diagnostic only because
    ``flock`` belongs to an open file description and the kernel releases it on
    close or process exit. Unlinking here would let a new caller lock a new
    inode while an existing holder or waiter still owns the old one.
    """
    try:
        mtime = os.path.getmtime(lock_path)
    except OSError:
        return False
    age = time.time() - mtime
    if age > stale_after:
        logger.warning(
            "git lock metadata is stale for %s (age %.0fs > %.0fs); retaining the kernel mutex inode",
            lock_path,
            age,
            stale_after,
        )
        return True
    return False


@contextlib.contextmanager
def _file_lock(
    git_dir: str,
    key: str,
    *,
    timeout: float,
    stale_after: float,
) -> Iterator[None]:
    """Hold an advisory flock on ``<git_dir>/gludd-git.lock``.

    Blocks (polling, non-blocking flock) until the lock is free or ``timeout``
    elapses. Stale metadata is reported but the stable mutex inode is never
    removed: kernel descriptor cleanup handles crashed owners safely. On
    timeout raises ``TimeoutError`` so a stuck repo surfaces as a clean failure
    rather than an unbounded hang.

    Re-entrant within the process: callers always hold the per-repo RLock when
    they get here, so ``key``'s depth counter is single-threaded. A nested entry
    (depth > 0) skips re-flocking — opening a second fd and flocking it would
    otherwise deadlock against this process's own first fd.
    """
    if not _HAVE_FCNTL:  # pragma: no cover - non-POSIX fallback
        yield
        return

    owner = (os.getpid(), threading.get_ident())
    if _file_lock_depth.get(key, 0) > 0 and _file_lock_owner.get(key) == owner:
        # Already held by this process (re-entrant nested acquire). Do not open
        # a second fd / re-flock; just account the depth and pass through.
        _file_lock_depth[key] += 1
        try:
            yield
        finally:
            _file_lock_depth[key] -= 1
        return

    # Defensive fallback for runtimes that fork outside Python's registered
    # hooks: copied depth belongs to another PID and must not grant entry.
    if _file_lock_depth.get(key, 0) > 0:
        inherited_fd = _file_lock_fds.pop(key, None)
        if inherited_fd is not None:
            with contextlib.suppress(OSError):
                os.close(inherited_fd)
        _file_lock_depth[key] = 0
        _file_lock_owner.pop(key, None)

    lock_path = os.path.join(git_dir, _LOCK_FILENAME)
    deadline = time.monotonic() + timeout
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except FileNotFoundError:
        # The .git directory can disappear between _git_dir() and open()
        # during cleanup, and unit tests may mock isdir(). Keep the in-process
        # lock rather than failing before the caller's git command runs.
        yield
        return
    _file_lock_fds[key] = fd
    stale_reported = False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"timed out after {timeout}s acquiring git lock {lock_path!r} "
                        f"(another git process holds the repo)"
                    ) from exc
                if not stale_reported:
                    stale_reported = _break_if_stale(lock_path, stale_after)
                time.sleep(_POLL_INTERVAL)
        # We hold the lock. Stamp the file so other waiters' staleness check
        # sees a fresh holder, and mark this process as the holder (depth 1) so
        # nested re-entries skip re-flocking.
        with contextlib.suppress(OSError):
            os.utime(lock_path, None)
        _file_lock_depth[key] = 1
        _file_lock_owner[key] = owner
        try:
            yield
        finally:
            _file_lock_depth[key] = 0
            _file_lock_owner.pop(key, None)
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        if _file_lock_fds.get(key) == fd:
            _file_lock_fds.pop(key, None)
        with contextlib.suppress(OSError):
            os.close(fd)


# --- public API -----------------------------------------------------------


@contextlib.contextmanager
def git_repo_lock(
    repo_path: str,
    *,
    timeout: float = _DEFAULT_ACQUIRE_TIMEOUT,
    stale_after: float = _DEFAULT_STALE_AFTER,
) -> Iterator[None]:
    """Serialize mutating git operations against the repo at ``repo_path``.

    Acquires, in order:
      1. the in-process re-entrant lock for this repo (serializes threads /
         coroutines in THIS process; re-entrant so nested git calls on the same
         repo in one thread never self-deadlock; bounded by ``timeout``), then
      2. the cross-process flock on ``<repo>/.git/gludd-git.lock`` (serializes
         across processes; bounded by the time remaining before the same
         deadline; crashed ownership is released by kernel descriptor cleanup).

    The in-process lock is taken FIRST so that, within one process, only one
    thread at a time ever contends for the (more expensive, timeout-bearing)
    file lock — and re-entrant acquisition by an already-holding thread skips
    straight through both layers: the RLock re-enters cheaply, and ``_file_lock``
    tracks a per-repo depth counter so a nested entry does NOT open a second fd
    and re-flock (advisory flock is per open-file-description and would deadlock
    a second fd against this process's own first fd). So a nested
    ``git_repo_lock`` on the same repo in one thread never self-deadlocks.

    Usage::

        with git_repo_lock(repo_path):
            subprocess.run(["git", "commit", ...], cwd=repo_path)

    Raises ``TimeoutError`` if either lock layer cannot be acquired within the
    single ``timeout`` budget.
    """
    git_dir = _git_dir(repo_path)
    key = _normalize(git_dir) if git_dir is not None else _normalize(repo_path)
    inproc = _get_inprocess_lock(key)
    deadline = time.monotonic() + timeout
    if not inproc.acquire(timeout=max(0.0, timeout)):
        raise TimeoutError(
            f"timed out after {timeout}s acquiring in-process git lock for {key!r} "
            "(another thread holds the repo)"
        )
    try:
        if git_dir is None:
            # No .git directory to anchor a cross-process lock; the in-process
            # lock alone still serializes the in-daemon race (the common case).
            yield
        else:
            remaining = max(0.0, deadline - time.monotonic())
            with _file_lock(git_dir, key, timeout=remaining, stale_after=stale_after):
                yield
    finally:
        inproc.release()


def _reserve_async_worker(repo_path: str) -> _AsyncGitLockWorker:
    """Reserve the thread-affine worker for this task/repository pair."""
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("async_git_repo_lock requires a running asyncio task")
    git_dir = _git_dir(repo_path)
    key = _normalize(git_dir) if git_dir is not None else _normalize(repo_path)
    owner_token = (task, key)
    with _async_workers_guard:
        worker = _async_workers.get(owner_token)
        if worker is None:
            worker = _AsyncGitLockWorker(owner_token)
            _async_workers[owner_token] = worker
        worker.users += 1
        return worker


def _adopt_completed_async_worker(worker: _AsyncGitLockWorker) -> None:
    """Fence a live owner and adopt only its sole completed-task result."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    # Keep the public synchronous returned-context API usable after the
    # acquiring event loop has completed.
    consumer: object = task if task is not None else threading.current_thread()
    with _async_workers_guard:
        previous_token = worker.owner_token
        if _async_workers.get(previous_token) is not worker:
            raise RuntimeError("async Git lock context has no registered owner")
        if previous_token[0] is consumer:
            return
        previous_owner = previous_token[0]
        if not isinstance(previous_owner, asyncio.Task) or not previous_owner.done():
            raise RuntimeError("async Git lock context belongs to another live owner task")
        if worker.users != 1:
            raise RuntimeError("async Git lock owner transfer requires one reservation")
        consumer_token = (consumer, previous_token[1])
        existing = _async_workers.get(consumer_token)
        if existing is not None and existing is not worker:
            raise RuntimeError("async Git lock owner transfer conflicts with a registered worker")
        # Publish one identity under the same registry guard used by reserve
        # and release. The native RLock/flock stay held by the same worker.
        _async_workers.pop(previous_token)
        worker.owner_token = consumer_token
        _async_workers[consumer_token] = worker


def _release_async_worker(worker: _AsyncGitLockWorker) -> None:
    """Release one reservation and stop an idle owner worker."""
    shutdown = False
    with _async_workers_guard:
        if worker.users <= 0:
            raise RuntimeError("async Git lock worker released without an owner")
        worker.users -= 1
        if worker.users == 0:
            if _async_workers.get(worker.owner_token) is worker:
                _async_workers.pop(worker.owner_token)
            shutdown = True
    if shutdown:
        worker.executor.shutdown(wait=True)


async def _await_native_future(
    future: concurrent.futures.Future[_NativeResult],
) -> _NativeResult:
    """Await native worker completion while deferring repeated cancellation."""
    wrapped = asyncio.wrap_future(future)
    while not future.done():
        try:
            await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # Cleanup owns the mutex until the worker completes. A later
            # cancellation request must not interrupt that ownership handoff.
            continue
    return future.result()


class _EnteredAsyncGitRepoLock(contextlib.AbstractContextManager[None]):
    """Already-acquired, single-use wrapper around a thread-owned mutex."""

    def __init__(
        self,
        cm: contextlib.AbstractContextManager[None],
        worker: _AsyncGitLockWorker,
    ) -> None:
        self._cm = cm
        self._worker = worker
        self._state_guard = threading.Lock()
        self._entered = False
        self._closed = False

    def __enter__(self) -> None:
        with self._state_guard:
            if self._closed:
                raise RuntimeError("async Git lock context is already closed")
            if self._entered:
                raise RuntimeError("async Git lock context is already entered")
            _adopt_completed_async_worker(self._worker)
            self._entered = True
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        with self._state_guard:
            if self._closed:
                return None
            self._closed = True
            self._entered = False
        future = self._worker.executor.submit(self._cm.__exit__, exc_type, exc, tb)
        try:
            return future.result()
        finally:
            _release_async_worker(self._worker)

    def __del__(self) -> None:
        """Release an acquired mutex abandoned by a dying task."""
        with contextlib.suppress(BaseException):
            self.__exit__(None, None, None)


async def async_git_repo_lock(
    repo_path: str,
    *,
    timeout: float = _DEFAULT_ACQUIRE_TIMEOUT,
    stale_after: float = _DEFAULT_STALE_AFTER,
) -> contextlib.AbstractContextManager[None]:
    """Acquire :func:`git_repo_lock` without blocking the event loop.

    The returned synchronous context manager is already acquired. Nested calls
    from one task and repository reuse a thread-affine worker, preserving the
    underlying ``RLock`` ownership. Cancellation waits asynchronously for the
    bounded acquire and releases any resulting ownership before propagating::

        cm = await async_git_repo_lock(repo_path)
        with cm:
            ...  # run git

    For purely synchronous call sites (like repo.py's ``_run_git``) use
    :func:`git_repo_lock` directly.
    """
    worker = _reserve_async_worker(repo_path)
    cm = git_repo_lock(repo_path, timeout=timeout, stale_after=stale_after)
    enter_future = worker.executor.submit(cm.__enter__)
    try:
        await asyncio.shield(asyncio.wrap_future(enter_future))
    except BaseException as original:
        acquired = False
        if isinstance(original, asyncio.CancelledError):
            with contextlib.suppress(BaseException):
                await _await_native_future(enter_future)
                acquired = True
        try:
            if acquired:
                exit_future = worker.executor.submit(cm.__exit__, None, None, None)
                await _await_native_future(exit_future)
        finally:
            _release_async_worker(worker)
        raise
    return _EnteredAsyncGitRepoLock(cm, worker)

# Native Make owns these public names; this boundary is not a generic executor.
_MAKE_COMMIT_TARGETS = frozenset({
    "git-commit", "commit-no-verify", "git-commit-no-verify", "git-amend-msg",
    "repo-commit", "ship-commit", "ship-commit-files", "git-commit-file",
    "test-and-commit", "commit-bootstrap", "commit-and-ship", "commit-and-ship-push",
})
_MAKE_TRANSACTION_FD = "GLUDD_COMMIT_TRANSACTION_FD"


def _assert_make_transaction(repo_path: str, descriptor: str) -> int:
    """Bind the inherited open description to the actual common-directory mutex."""
    common = git_common_directory(repo_path)
    if common is None or not _HAVE_FCNTL:
        raise ValueError("native Make transactions require a POSIX Git common directory")
    if not descriptor.isascii() or not descriptor.isdecimal() or len(descriptor) > 10:
        raise ValueError("missing or invalid inherited commit-lock descriptor")
    fd = int(descriptor)
    if fd < 3 or fd > 2_147_483_647:
        raise ValueError("commit-lock descriptor must be a native nonstandard C integer")
    held = os.fstat(fd)
    expected = os.stat(os.path.join(common, _LOCK_FILENAME), follow_symlinks=False)
    if not stat.S_ISREG(held.st_mode) or not stat.S_ISREG(expected.st_mode):
        raise ValueError("commit-lock descriptor must name the native regular mutex")
    if (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError("commit-lock descriptor belongs to a different repository")
    # fstat/path equality is not lock authority. An independent open description
    # refuses here while another caller owns the inode; an inherited owner can
    # reassert its existing native flock without releasing it.
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def _native_make_options() -> tuple[str, tuple[int, ...]]:
    """Retain native inspection modes and GNU Make pipe/FIFO jobserver semantics."""
    tokens = shlex.split(os.environ.get("MAKEFLAGS", "").partition(" -- ")[0])
    short = tokens[0] if tokens and not tokens[0].startswith("-") and "=" not in tokens[0] else ""
    for mode, letter, long_flags in (
        ("query", "q", {"-q", "--question"}),
        ("dry-run", "n", {"-n", "--just-print", "--dry-run", "--recon"}),
        ("touch", "t", {"-t", "--touch"}),
    ):
        if letter in short or any(token in long_flags for token in tokens):
            return mode, ()
    pairs: set[tuple[int, int]] = set()
    for token in tokens:
        if token.startswith("--jobserver-auth=fifo:"):
            continue
        if not token.startswith(("--jobserver-auth=", "--jobserver-fds=")):
            continue
        match = re.fullmatch(r"--jobserver-(?:auth|fds)=([0-9]{1,10}),([0-9]{1,10})", token)
        if match is None:
            raise ValueError("unusable native GNU Make jobserver input")
        pair = int(match[1]), int(match[2])
        if pair[0] == pair[1] or any(fd < 3 or fd > 2_147_483_647 for fd in pair):
            raise ValueError("unusable native GNU Make jobserver descriptors")
        for fd in pair:
            if not stat.S_ISFIFO(os.fstat(fd).st_mode):
                raise ValueError("native GNU Make jobserver descriptor is not a pipe")
        pairs.add(pair)
    if len(pairs) > 1:
        raise ValueError("mismatched native GNU Make jobserver descriptor pairs")
    return "execute", next(iter(pairs), ())


class _CommitObserver(Protocol):
    """The existing stream owner surface needed by one native commit."""

    def __call__(
        self, command: Sequence[str], log_path: Path, *,
        observed_root: Path, label: str, run_id: str,
        heartbeat_seconds: float, quiet_seconds: float, max_seconds: float,
        retain_runs: int, environment: Mapping[str, str], cwd: Path, pass_fds: tuple[int, ...],
    ) -> int:
        """Execute and drain the existing owned command."""


def _run_make_transaction(
    target: str,
    make: str,
    makefile: str,
    *,
    observed_root: str,
    heartbeat_seconds: float,
    quiet_seconds: float,
    max_seconds: float,
    retain_runs: int,
    observer_fn: _CommitObserver | None = None,
    identity_fn: Callable[[], str] | None = None,
) -> int:
    """Hold the established mutex through one supervised native Make transaction."""
    if target not in _MAKE_COMMIT_TARGETS:
        raise ValueError("unsupported native commit target")
    if Path(make).name not in {"make", "gmake"} or any(char.isspace() for char in make):
        raise ValueError("native commit execution requires one Make executable")
    root = Path.cwd().resolve()
    selected_makefile = Path(makefile).resolve()
    observation = Path(observed_root).resolve()
    if not selected_makefile.is_file() or not selected_makefile.is_relative_to(root):
        raise ValueError("native commit Makefile must belong to the selected checkout")
    if not observation.is_relative_to(root):
        raise ValueError("native commit observations must stay private to the selected checkout")
    mode, jobserver_fds = _native_make_options()
    if mode != "execute":
        native_status = 1 if mode == "query" else 0
        print(
            f"AGENT_NEXT target={target} outcome=planned native_status={native_status} mode={mode} "
            "gate_evidence=false release_ready=false source_checkpoint=false "
            "next_action=Native query/dry-run/touch inspection executes no staging, guards, "
            "collection, commit or target touch; use the actual Make target only after "
            "its source/ownership prerequisites. Query1 means execution would be required.",
            file=sys.stderr,
        )
        return native_status
    common = git_common_directory(str(root))
    if common is None or not _HAVE_FCNTL:
        raise ValueError("native Make transactions require a POSIX Git common directory")

    # Native stream_command supplies its actual current function and identity
    # owner. The direct Python API keeps source-owned defaults for focused callers.
    if (observer_fn is None) != (identity_fn is None):
        raise ValueError("native observer and identity owner must be bound together")
    if observer_fn is None or identity_fn is None:
        source_scripts = str(Path(__file__).resolve().parents[3] / "scripts")
        if not sys.path or sys.path[0] != source_scripts:
            sys.path.insert(0, source_scripts)
        from stream_command import _new_run_id, stream_command
        observer_fn = stream_command
        identity_fn = _new_run_id
    observer_code = getattr(observer_fn, "__code__", None)
    if not isinstance(observer_code, CodeType):
        raise ValueError("native observer must bind its actual source function")
    print(
        f"COMMIT-LOCK: source={Path(__file__).resolve()} "
        f"observer={Path(observer_code.co_filename).resolve()}",
        file=sys.stderr, flush=True,
    )
    print(f"COMMIT-LOCK: waiting target={target} repository={common}", file=sys.stderr, flush=True)
    with git_repo_lock(str(root)):
        fd = _file_lock_fds[_normalize(common)]
        _assert_make_transaction(str(root), str(fd))
        environment = dict(os.environ)
        environment[_MAKE_TRANSACTION_FD] = str(fd)
        run_id = identity_fn()
        result = observer_fn(
            [make, "--no-print-directory", "-f", str(selected_makefile), "_commit-locked-" + target],
            observation / "commit-transaction" / (run_id + ".log"),
            observed_root=observation, label="commit-transaction", run_id=run_id,
            heartbeat_seconds=heartbeat_seconds, quiet_seconds=quiet_seconds,
            max_seconds=max_seconds, retain_runs=retain_runs,
            environment=environment, cwd=root, pass_fds=(fd, *jobserver_fds),
        )
    outcome = "passed" if result == 0 else "failed"
    next_action = (
        "A local source checkpoint is not full-gate or exact-SHA hosted/deployment proof; "
        "retain its collection and obtain independent source review."
        if result == 0 else
        "Preserve this native receipt and any staged or applied state; inspect the exact "
        "failed child/input before reconciliation or another commit."
    )
    print(
        f"AGENT_NEXT target={target} outcome={outcome} native_status={result} "
        f"gate_evidence=false release_ready=false next_owner=observed-status "
        f"label=commit-transaction run_id={run_id}; {next_action}",
        file=sys.stderr,
    )
    return result


def main(
    argv: Sequence[str] | None = None, *,
    observer_fn: _CommitObserver | None = None,
    identity_fn: Callable[[], str] | None = None,
    require_make_target: bool = False,
) -> int:
    """Expose only native Make commit execution and inherited-lock validation."""
    parser = argparse.ArgumentParser(description="Native repository-locked Make commit transaction")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--make-target", choices=sorted(_MAKE_COMMIT_TARGETS))
    mode.add_argument("--assert-transaction", action="store_true")
    parser.add_argument("--label", choices=["commit-transaction"], required=require_make_target)
    parser.add_argument("--make", default="make")
    parser.add_argument("--makefile", default="Makefile")
    parser.add_argument("--observed-root", default=".gate-logs/observed")
    parser.add_argument("--heartbeat-secs", type=float, default=30.0)
    parser.add_argument("--quiet-secs", type=float, default=0.0)
    parser.add_argument("--max-secs", type=float, default=3600.0)
    parser.add_argument("--retain-runs", type=int, default=20)
    args = parser.parse_args(argv)
    if require_make_target and args.assert_transaction:
        parser.error("native commit dispatch requires --make-target, not --assert-transaction")
    if not require_make_target and args.make_target:
        parser.error("native commits must enter through the existing stream_command Make owner")
    try:
        if args.assert_transaction:
            _assert_make_transaction(os.getcwd(), os.environ.get(_MAKE_TRANSACTION_FD, ""))
            return 0
        return _run_make_transaction(
            str(args.make_target), str(args.make), str(args.makefile),
            observed_root=str(args.observed_root),
            heartbeat_seconds=float(args.heartbeat_secs), quiet_seconds=float(args.quiet_secs),
            max_seconds=float(args.max_secs), retain_runs=int(args.retain_runs),
            observer_fn=observer_fn, identity_fn=identity_fn,
        )
    except (OSError, TimeoutError, ValueError) as error:
        print(f"COMMIT-LOCK: refused: {error}", file=sys.stderr)
        print(
            "AGENT_NEXT target=commit-transaction outcome=refused native_status=2 "
            "gate_evidence=false release_ready=false next_action=Preserve owned state and "
            "repair the reported common-directory, descriptor, ownership or input prerequisite; "
            "do not delete locks or retry an unchanged refusal.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
