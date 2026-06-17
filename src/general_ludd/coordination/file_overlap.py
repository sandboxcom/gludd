"""In-product file-overlap coordinator with merge-aware waiting (issue #31).

Background
----------
gludd runs roles in PARALLEL. Overlap between two roles' work was, until now,
modelled only ABSTRACTLY: the scheduler (``scheduling/scheduler.py``) serializes
``WorkItem``s that share an opaque ``resources`` frozenset, and the Makefile owns
the merge tooling. There was NO in-product, file-PATH-aware coordinator that
makes two roles touching the same files actually wait / serialize at runtime —
``FileClaimRegistry`` (this package's other module) only *records* who claims
what and reports overlaps; it does not block, serialize, or wait for a merge.

This module fills that gap with :class:`FileOverlapCoordinator`:

* **Serialization** — given a role's declared / inferred target file globs it
  detects overlap with currently-active roles' file sets and serializes the
  overlapping work by acquiring per-path async locks. Disjoint paths never
  contend, so non-overlapping roles still run fully concurrently.
* **Merge-aware waiting** — a role can wait until every overlapping role has
  not only *completed* but had its changes *merged* (``release(..., merged=True)``)
  before it proceeds, so it builds on the merged tree rather than a half-written
  one.
* **Deadlock safety** — per-path locks are always acquired in a single global
  order (sorted by path), so two roles that both want ``{a, b}`` can never grab
  them in opposite orders and deadlock.
* **Never blocks forever** — every wait/acquire is bounded by a timeout and
  raises :class:`OverlapTimeout` rather than hanging.

Scheduler integration
----------------------
:func:`paths_to_resources` maps a role's file globs to the SAME opaque resource
strings the scheduler already understands, so a caller can fold file overlap
into the existing ``WorkItem.resources`` model *without changing the scheduler*::

    item = WorkItem(id=role_id, resources=paths_to_resources(role_paths))

Two roles whose globs overlap then map to a shared resource token and the
existing :class:`~general_ludd.scheduling.scheduler.Scheduler` serializes them
at PLAN time; this coordinator serializes them at RUN time. The two layers are
consistent because they derive overlap from the same path set.
"""

from __future__ import annotations

import asyncio
import fnmatch
import time
from collections.abc import Iterable
from dataclasses import dataclass, field


class OverlapTimeout(TimeoutError):
    """Raised when a wait/acquire exceeds its timeout (never blocks forever)."""


# --------------------------------------------------------------------------- #
# glob / path helpers                                                         #
# --------------------------------------------------------------------------- #


def _normalize(path: str) -> str:
    """Canonical key for a path or glob.

    Collapses redundant separators and a leading ``./`` so two spellings of the
    same target map to one lock. Does NOT touch the filesystem (globs may name
    paths that do not exist yet).
    """
    p = path.strip()
    while p.startswith("./"):
        p = p[2:]
    # Collapse repeated slashes without resolving symlinks / the filesystem.
    while "//" in p:
        p = p.replace("//", "/")
    return p.rstrip("/") or "/"


def _is_glob(token: str) -> bool:
    return any(ch in token for ch in "*?[")


def _path_match(name: str, pattern: str) -> bool:
    """fnmatch, but a single ``*`` / ``?`` does NOT cross a path separator.

    Plain ``fnmatch`` treats ``*`` as matching everything including ``/``, so
    ``src/*.py`` would wrongly match ``src/sub/foo.py``. We match per path
    segment instead: each segment of *name* must match the corresponding
    segment of *pattern*, with a ``**`` segment matching any number of
    segments (the usual recursive-glob convention).
    """
    return _seg_match(name.split("/"), pattern.split("/"))


def _seg_match(name_parts: list[str], pat_parts: list[str]) -> bool:
    """Segment-wise glob match; a ``**`` pattern segment spans zero+ segments."""
    if not pat_parts:
        return not name_parts
    head, *rest = pat_parts
    if head == "**":
        # ** consumes zero or more name segments.
        return _seg_match(name_parts, rest) or (
            bool(name_parts) and _seg_match(name_parts[1:], pat_parts)
        )
    if not name_parts:
        return False
    if not fnmatch.fnmatch(name_parts[0], head):
        return False
    return _seg_match(name_parts[1:], rest)


def globs_overlap(a: str, b: str) -> bool:
    """Return True iff two path globs can match a common concrete path.

    Conservative and filesystem-free:
      * equal normalized tokens overlap,
      * a literal that matches the other side's glob (either direction) overlaps,
      * two globs overlap if either matches the other's literal prefix, or they
        share a directory prefix — we err toward declaring overlap so the
        coordinator never lets two genuinely-overlapping roles run concurrently.
    """
    na, nb = _normalize(a), _normalize(b)
    if na == nb:
        return True
    ag, bg = _is_glob(na), _is_glob(nb)
    if ag and not bg:
        return _path_match(nb, na)
    if bg and not ag:
        return _path_match(na, nb)
    if not ag and not bg:
        # Two literals: overlap only if one is a directory-prefix of the other.
        return na.startswith(nb + "/") or nb.startswith(na + "/")
    # Both globs: compare their non-glob prefixes (the directory up to the first
    # wildcard). Overlapping prefixes ⇒ potentially-overlapping match sets.
    pa, pb = _glob_prefix(na), _glob_prefix(nb)
    return pa.startswith(pb) or pb.startswith(pa)


def _glob_prefix(glob: str) -> str:
    """The leading non-wildcard portion of a glob (up to the first wildcard)."""
    out: list[str] = []
    for ch in glob:
        if ch in "*?[":
            break
        out.append(ch)
    return "".join(out)


def paths_to_resources(paths: Iterable[str]) -> frozenset[str]:
    """Map a role's file globs to scheduler resource tokens.

    Each normalized path/glob becomes a ``file:<path>`` resource string. Folding
    these into a :class:`~general_ludd.scheduling.scheduler.WorkItem`'s
    ``resources`` makes the EXISTING scheduler serialize roles that declare the
    SAME path — no scheduler change required. (Glob-vs-glob fuzzy overlap is the
    run-time coordinator's job; the resource map handles exact-token overlap,
    which is the common case of two roles naming the same file.)
    """
    return frozenset(f"file:{_normalize(p)}" for p in paths)


# --------------------------------------------------------------------------- #
# coordinator                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class _ActiveRole:
    """Bookkeeping for one role that has registered an active file set.

    Lifecycle: a role is *active* (``released`` False) until it calls
    ``release``. A plain ``release(merged=False)`` marks it ``released`` (so
    non-merge-aware waiters clear) but it stays in the registry, un-``merged``,
    so MERGE-AWARE waiters keep blocking until a later ``release(merged=True)``.
    ``progress`` fires on every release so a waiter wakes and re-evaluates.
    """

    role_id: str
    paths: frozenset[str]
    released: bool = False
    merged: bool = False
    # Fires on every release (merged or not) so waiters re-evaluate; an Event
    # never "un-sets", so we replace it with a fresh one after each wake.
    progress: asyncio.Event = field(default_factory=asyncio.Event)


class FileOverlapCoordinator:
    """Serialize overlapping file work between roles, with merge-aware waiting.

    Usage (async)::

        coord = FileOverlapCoordinator()
        coord.register_active("role-A", ["src/foo.py", "src/bar/*.py"])

        # role-B wants to touch overlapping paths — it serializes behind A:
        async with coord.guard("role-B", ["src/foo.py"]):
            ...  # only runs once A has released (and, if merge_aware, merged)

        coord.release("role-A", merged=True)

    Guarantees:
      * Disjoint path sets never contend (full concurrency).
      * Overlapping path sets serialize via per-path async locks taken in a
        single global (sorted) order ⇒ no lock-ordering deadlock.
      * ``wait_for_clear`` / ``guard`` never block past ``timeout`` —
        :class:`OverlapTimeout` is raised instead.
      * With ``merge_aware=True`` a waiter resumes only after every overlapping
        role has released *and* signalled its changes are merged.
    """

    def __init__(self, *, default_timeout: float = 30.0) -> None:
        self._default_timeout = default_timeout
        # Guards the registry dicts; held only briefly, never across an await.
        self._state_lock = asyncio.Lock()
        self._active: dict[str, _ActiveRole] = {}
        # Per-path serialization locks, created lazily and reused.
        self._path_locks: dict[str, asyncio.Lock] = {}

    # ----- registry ---------------------------------------------------- #

    def register_active(self, role_id: str, paths: Iterable[str]) -> None:
        """Record that *role_id* is actively working on *paths*.

        Re-registering a role replaces its previous path set. Synchronous so a
        role can declare its targets before entering the event loop's wait.
        """
        norm = frozenset(_normalize(p) for p in paths)
        existing = self._active.get(role_id)
        if existing is not None and not existing.released:
            existing.paths = norm
            existing.merged = False
        else:
            self._active[role_id] = _ActiveRole(role_id=role_id, paths=norm)

    def release(self, role_id: str, *, merged: bool = False) -> None:
        """Release *role_id*'s active file set.

        ``merged=True`` additionally signals that the role's changes have landed
        in the shared tree, unblocking merge-aware waiters. ``merged=False``
        (the default) unblocks plain (non-merge-aware) waiters but a merge-aware
        waiter keeps waiting until a later ``release(..., merged=True)`` or the
        role is dropped. Never raises for an unknown role.
        """
        role = self._active.get(role_id)
        if role is None:
            return
        role.released = True
        role.merged = merged
        # Wake every current waiter, then arm a fresh event for the next round.
        role.progress.set()
        role.progress = asyncio.Event()
        if merged:
            # Fully done and merged — drop it so its paths are clear for
            # merge-aware waiters too.
            self._active.pop(role_id, None)

    def active_roles(self) -> dict[str, frozenset[str]]:
        """Snapshot of currently-active (not-yet-released) roles → their paths."""
        return {
            rid: r.paths
            for rid, r in self._active.items()
            if not r.released
        }

    # ----- overlap detection ------------------------------------------- #

    def _overlapping_roles(
        self, role_id: str | None, paths: frozenset[str]
    ) -> list[_ActiveRole]:
        """Roles (other than *role_id*) still in the registry whose paths
        overlap *paths*. Includes released-but-not-merged roles so merge-aware
        callers can still see them; callers filter on ``released`` / ``merged``.
        """
        out: list[_ActiveRole] = []
        for rid, role in self._active.items():
            if rid == role_id:
                continue
            if any(globs_overlap(p, q) for p in paths for q in role.paths):
                out.append(role)
        return out

    def overlaps(self, paths: Iterable[str], *, role_id: str | None = None) -> list[str]:
        """Return ids of active roles whose paths overlap *paths* (sorted)."""
        norm = frozenset(_normalize(p) for p in paths)
        return sorted(
            r.role_id
            for r in self._overlapping_roles(role_id, norm)
            if not r.released
        )

    # ----- per-path lock acquisition (ordered, deadlock-safe) ---------- #

    def _lock_for(self, path: str) -> asyncio.Lock:
        lock = self._path_locks.get(path)
        if lock is None:
            lock = asyncio.Lock()
            self._path_locks[path] = lock
        return lock

    async def _acquire_paths(
        self, paths: frozenset[str], deadline: float
    ) -> list[asyncio.Lock]:
        """Acquire one lock per path in GLOBAL sorted order (deadlock-safe).

        Always taking locks in the same order means two roles contending for the
        same set can never grab them in opposite orders, so a circular wait is
        impossible. On timeout, any locks already taken are released and
        :class:`OverlapTimeout` is raised — never an unbounded wait.
        """
        held: list[asyncio.Lock] = []
        for path in sorted(paths):
            lock = self._lock_for(path)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._release_locks(held)
                raise OverlapTimeout(
                    f"timed out acquiring per-path lock for {path!r}"
                )
            try:
                await asyncio.wait_for(lock.acquire(), timeout=remaining)
            except TimeoutError as exc:
                self._release_locks(held)
                raise OverlapTimeout(
                    f"timed out acquiring per-path lock for {path!r}"
                ) from exc
            held.append(lock)
        return held

    @staticmethod
    def _release_locks(locks: list[asyncio.Lock]) -> None:
        for lock in locks:
            if lock.locked():
                lock.release()

    # ----- merge-aware waiting ----------------------------------------- #

    async def wait_for_clear(
        self,
        paths: Iterable[str],
        *,
        role_id: str | None = None,
        merge_aware: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Block until *paths* are clear of overlapping active roles.

        Waits for every currently-overlapping role to release. When
        ``merge_aware`` is True a role counts as clear only once it released
        with ``merged=True`` (i.e. its changes have landed); otherwise any
        release clears it. Bounded by ``timeout`` (default: the coordinator's
        ``default_timeout``) — :class:`OverlapTimeout` is raised rather than
        hanging forever. Returns immediately when there is no overlap.
        """
        norm = frozenset(_normalize(p) for p in paths)
        tmo = self._default_timeout if timeout is None else timeout
        deadline = time.monotonic() + tmo

        while True:
            overlapping = self._overlapping_roles(role_id, norm)
            if merge_aware:
                # Clear only once an overlapping role has MERGED. A merged role
                # is dropped from the registry, so any role still present that
                # is not merged is a blocker (released-but-unmerged included).
                pending = [r for r in overlapping if not r.merged]
            else:
                # Clear as soon as the overlapping role has released.
                pending = [r for r in overlapping if not r.released]
            if not pending:
                return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                blockers = sorted(r.role_id for r in pending)
                raise OverlapTimeout(
                    f"timed out after {tmo}s waiting for overlapping roles "
                    f"{blockers} to clear paths {sorted(norm)}"
                )

            # Wait for the nearest blocker to make progress (release), then
            # re-evaluate. A non-merged release under merge_aware loops back and
            # keeps waiting for the merge.
            waiters = [asyncio.ensure_future(r.progress.wait()) for r in pending]
            try:
                done, _pending = await asyncio.wait(
                    waiters,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for w in waiters:
                    w.cancel()
            if not done:
                blockers = sorted(r.role_id for r in pending)
                raise OverlapTimeout(
                    f"timed out after {tmo}s waiting for overlapping roles "
                    f"{blockers} to clear paths {sorted(norm)}"
                )

    def guard(
        self,
        role_id: str,
        paths: Iterable[str],
        *,
        merge_aware: bool = False,
        timeout: float | None = None,
    ) -> _OverlapGuard:
        """Async context manager that serializes *role_id*'s overlapping work.

        On enter: waits for overlapping roles to clear (merge-aware optional),
        then acquires per-path locks in deadlock-safe order and registers the
        role active. On exit: releases the locks and the role
        (``merged=False``; call :meth:`release` with ``merged=True`` explicitly
        once the changes are actually merged). Use as::

            async with coord.guard("role-B", paths):
                ...  # the only role touching these paths right now
        """
        norm = frozenset(_normalize(p) for p in paths)
        return _OverlapGuard(
            self,
            role_id,
            norm,
            merge_aware=merge_aware,
            timeout=self._default_timeout if timeout is None else timeout,
        )


class _OverlapGuard:
    """Async context manager returned by :meth:`FileOverlapCoordinator.guard`."""

    def __init__(
        self,
        coord: FileOverlapCoordinator,
        role_id: str,
        paths: frozenset[str],
        *,
        merge_aware: bool,
        timeout: float,
    ) -> None:
        self._coord = coord
        self._role_id = role_id
        self._paths = paths
        self._merge_aware = merge_aware
        self._timeout = timeout
        self._held: list[asyncio.Lock] = []

    async def __aenter__(self) -> _OverlapGuard:
        deadline = time.monotonic() + self._timeout
        # First wait until overlapping roles have cleared (bounded).
        await self._coord.wait_for_clear(
            self._paths,
            role_id=self._role_id,
            merge_aware=self._merge_aware,
            timeout=self._timeout,
        )
        # Then take per-path locks in global order so concurrent guards on the
        # same paths serialize without a lock-ordering deadlock.
        self._held = await self._coord._acquire_paths(self._paths, deadline)
        self._coord.register_active(self._role_id, self._paths)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._coord.release(self._role_id, merged=False)
        self._coord._release_locks(self._held)
        self._held = []
