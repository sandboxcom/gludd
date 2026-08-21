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

import contextlib
import errno
import logging
import os
import threading
import time
from collections.abc import Iterator
from types import TracebackType

logger = logging.getLogger(__name__)

# Name of the per-repo cross-process lock file, placed inside ``.git`` so it
# travels with the repo and is naturally excluded from the work tree.
_LOCK_FILENAME = "gludd-git.lock"

# How long to wait to acquire the cross-process file lock before giving up.
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


def _reset_after_fork() -> None:
    """Discard parent-owned mutex state in a newly forked child."""
    global _registry_guard

    for fd in tuple(_file_lock_fds.values()):
        with contextlib.suppress(OSError):
            os.close(fd)
    _file_lock_fds.clear()
    _file_lock_depth.clear()
    _file_lock_owner.clear()
    _repo_locks.clear()
    _registry_guard = threading.Lock()


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
    lexical_ancestor = os.path.abspath(anchor)
    while True:
        try:
            physical_ancestor = os.path.realpath(lexical_ancestor)
            contains_path = os.path.commonpath((resolved, physical_ancestor)) == physical_ancestor
        except (OSError, ValueError):
            contains_path = False
        if contains_path:
            suffix = os.path.relpath(resolved, physical_ancestor)
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
         repo in one thread never self-deadlock), then
      2. the cross-process flock on ``<repo>/.git/gludd-git.lock`` (serializes
         across processes; bounded by ``timeout``; crashed ownership is
         released by kernel descriptor cleanup).

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

    Raises ``TimeoutError`` if the cross-process lock cannot be acquired within
    ``timeout`` seconds.
    """
    git_dir = _git_dir(repo_path)
    key = _normalize(git_dir) if git_dir is not None else _normalize(repo_path)
    inproc = _get_inprocess_lock(key)
    inproc.acquire()
    try:
        if git_dir is None:
            # No .git directory to anchor a cross-process lock; the in-process
            # lock alone still serializes the in-daemon race (the common case).
            yield
        else:
            with _file_lock(git_dir, key, timeout=timeout, stale_after=stale_after):
                yield
    finally:
        inproc.release()


async def async_git_repo_lock(
    repo_path: str,
    *,
    timeout: float = _DEFAULT_ACQUIRE_TIMEOUT,
    stale_after: float = _DEFAULT_STALE_AFTER,
) -> contextlib.AbstractContextManager[None]:
    """Async-friendly acquire of :func:`git_repo_lock`.

    Acquires the (potentially blocking, timeout-bearing) lock off the event loop
    via ``run_in_executor`` so waiting on a contended repo never blocks the
    loop, then returns an already-entered context manager. Use as::

        cm = await async_git_repo_lock(repo_path)
        with cm:
            ...  # run git

    For purely synchronous call sites (like repo.py's ``_run_git``) use
    :func:`git_repo_lock` directly.
    """
    import asyncio
    import concurrent.futures

    class _EnteredAsyncGitRepoLock(contextlib.AbstractContextManager[None]):
        def __init__(self, cm: contextlib.AbstractContextManager[None], executor: concurrent.futures.Executor) -> None:
            self._cm = cm
            self._executor = executor
            self._entered = False
            self._closed = False

        def __enter__(self) -> None:
            self._entered = True
            return None

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool | None:
            if self._closed:
                return None
            self._closed = True
            future = self._executor.submit(self._cm.__exit__, exc_type, exc, tb)
            try:
                return future.result()
            finally:
                self._executor.shutdown(wait=True)

    cm = git_repo_lock(repo_path, timeout=timeout, stale_after=stale_after)
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="gludd-git-lock")
    try:
        await loop.run_in_executor(executor, cm.__enter__)
    except BaseException:
        executor.shutdown(wait=True)
        raise
    return _EnteredAsyncGitRepoLock(cm, executor)
