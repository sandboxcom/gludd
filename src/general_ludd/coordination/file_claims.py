"""In-memory, thread-safe file claim registry for agent file-overlap coordination.

Workers call ``claim`` before editing files and ``release`` when they finish.
Any worker can then call ``overlaps`` or ``should_wait`` to discover conflicts
with other concurrent workers.  ``all_claims`` and ``merge_plan`` expose the
global state for merge-planning.

State is ephemeral — stored in process memory only.  Do NOT persist to a DB;
claims represent active-worker intent and are meaningless across restarts.

Claims carry a TTL (default 900s / 15min).  A worker that crashes without
calling ``release`` would otherwise poison every file it claimed forever
(#53) — any future worker touching an overlapping path would burn its push
retries and go BLOCKED permanently.  Stale claims (older than ``ttl_seconds``
and not refreshed) are treated as absent by ``overlaps``/``all_claims`` and
are actively reaped (removed) the next time either is called, or explicitly
via ``reap_stale``.  Re-claiming (heartbeating) the same worker_id refreshes
its timestamp even when the file set is unchanged.

D10 (#53 livelock): ``claim_or_conflict`` provides atomic claim-or-detect so
the commit path never sees transient overlap. Files are sorted before claiming
(total-order determinism), preventing the livelock where two agents claim
overlapping sets — each sees the other's claim in ``overlaps``, both release,
both retry, repeat. Stale claims are reaped before the conflict check so a
crashed worker's ghost claim never blocks a live worker.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 900.0


class FileClaimRegistry:
    """Thread-safe in-memory registry mapping worker IDs to claimed file paths.

    All public methods are safe to call concurrently from multiple threads.
    The canonical data model is ``_worker_files``: a dict from worker_id to
    the frozenset of paths it is currently editing.  ``_file_workers`` is the
    inverted index (file -> set of worker_ids) and is kept in sync on every
    mutation.  ``_worker_claimed_at`` tracks, per worker, the monotonic time
    of its most recent claim (a re-claim/heartbeat refreshes this).
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.Lock()
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        # worker_id -> frozenset[file_path]
        self._worker_files: dict[str, frozenset[str]] = {}
        # file_path -> set[worker_id]  (inverted index, always in sync)
        self._file_workers: dict[str, set[str]] = {}
        # worker_id -> monotonic timestamp of the most recent claim() call
        self._worker_claimed_at: dict[str, float] = {}
        # workers we've already logged a reap warning for (avoid log spam;
        # cleared if the worker claims again).
        self._warned_reaped: set[str] = set()

    # ------------------------------------------------------------------
    # Internal helpers (must be called with self._lock held)
    # ------------------------------------------------------------------

    def _remove_worker_from_index(self, worker_id: str) -> None:
        """Drop all index entries for *worker_id* without touching _worker_files."""
        files = self._worker_files.get(worker_id, frozenset())
        for path in files:
            workers_for_path = self._file_workers.get(path)
            if workers_for_path is not None:
                workers_for_path.discard(worker_id)
                if not workers_for_path:
                    del self._file_workers[path]

    def _is_stale(self, worker_id: str, now: float) -> bool:
        """Return True if *worker_id*'s claim has exceeded the TTL.

        Unknown workers are not stale (they simply have no claim).
        """
        claimed_at = self._worker_claimed_at.get(worker_id)
        if claimed_at is None:
            return False
        return (now - claimed_at) > self._ttl_seconds

    def _reap_stale_locked(self, now: float) -> list[str]:
        """Remove all stale worker claims. Must be called with self._lock held.

        Returns the sorted list of reaped worker_ids.
        """
        stale = [
            worker_id
            for worker_id in list(self._worker_files)
            if self._is_stale(worker_id, now)
        ]
        for worker_id in stale:
            self._remove_worker_from_index(worker_id)
            del self._worker_files[worker_id]
            del self._worker_claimed_at[worker_id]
            if worker_id not in self._warned_reaped:
                self._warned_reaped.add(worker_id)
                logger.warning(
                    "file_claims: reaped stale claim for worker=%s (ttl=%.0fs)",
                    worker_id,
                    self._ttl_seconds,
                )
        return sorted(stale)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def claim(self, worker_id: str, files: Iterable[str]) -> None:
        """Record that *worker_id* is editing *files*.

        If *worker_id* already has claims, they are replaced by this call
        (re-claiming is treated as updating the set of edited files).
        The operation is idempotent when the file set is unchanged.

        Also acts as a heartbeat: calling ``claim`` again for a worker that
        already holds claims refreshes its TTL clock, even when the file set
        is unchanged (so a live worker's claims never go stale while it keeps
        checking in).
        """
        sorted_files = sorted(files)
        file_set = frozenset(sorted_files)
        now = self._clock()
        with self._lock:
            # Remove stale index entries for this worker before updating.
            self._remove_worker_from_index(worker_id)
            self._worker_files[worker_id] = file_set
            self._worker_claimed_at[worker_id] = now
            self._warned_reaped.discard(worker_id)
            for path in file_set:
                self._file_workers.setdefault(path, set()).add(worker_id)

    def claim_or_conflict(self, worker_id: str, files: Iterable[str]) -> bool:
        """Atomically sort files, reap stale claims, check for conflicts, and
        either claim the files (return True) or detect an active overlapping
        claim (return False).

        The operation is all-or-nothing: if ANY file is contested by another
        non-stale worker, NONE of the files are claimed and this worker's prior
        claims are left undisturbed.  This is the atomic replacement for the
        non-atomic ``claim()`` + ``overlaps()`` pattern that could livelock
        when two workers simultaneously claimed overlapping sets, each saw the
        other in ``overlaps()``, both released, and both retried (#53 / D10).

        Files are sorted before claiming for total-order determinism, so that
        two agents claiming the same set in different orders are treated
        identically.
        """
        sorted_files = sorted(files)
        file_set = frozenset(sorted_files)
        now = self._clock()
        with self._lock:
            self._reap_stale_locked(now)
            # Conflict check: all-or-nothing.  Exclude the calling worker
            # so a re-claim (heartbeat) from the same worker_id is not
            # treated as a self-conflict.
            for path in file_set:
                others = self._file_workers.get(path, set()) - {worker_id}
                if others:
                    return False
            # No conflicts — claim
            self._remove_worker_from_index(worker_id)
            self._worker_files[worker_id] = file_set
            self._worker_claimed_at[worker_id] = now
            self._warned_reaped.discard(worker_id)
            for path in file_set:
                self._file_workers.setdefault(path, set()).add(worker_id)
            return True

    def release(self, worker_id: str) -> None:
        """Drop all claims for *worker_id*.

        No-op if *worker_id* is not registered; never raises.
        """
        with self._lock:
            if worker_id not in self._worker_files:
                return
            self._remove_worker_from_index(worker_id)
            del self._worker_files[worker_id]
            self._worker_claimed_at.pop(worker_id, None)
            self._warned_reaped.discard(worker_id)

    def reap_stale(self, now: float | None = None) -> list[str]:
        """Actively remove all claims whose TTL has expired.

        *now* defaults to ``self._clock()``.  Returns the sorted list of
        worker_ids that were reaped (empty if none were stale).  Safe to call
        at any time; also called lazily by ``overlaps``, ``all_claims`` and
        ``merge_plan`` so callers never observe a ghost claim.
        """
        if now is None:
            now = self._clock()
        with self._lock:
            return self._reap_stale_locked(now)

    def overlaps(self, worker_id: str) -> dict[str, list[str]]:
        """Return the conflict map for *worker_id*.

        For each file that *worker_id* claims AND at least one other worker also
        claims, the result contains ``{file: [other_worker_ids]}``.

        Returns ``{}`` when *worker_id* is unknown or has no conflicts.
        Stale claims (past TTL, not refreshed) are reaped first and never
        appear here.
        """
        now = self._clock()
        with self._lock:
            self._reap_stale_locked(now)
            my_files = self._worker_files.get(worker_id, frozenset())
            result: dict[str, list[str]] = {}
            for path in my_files:
                others = [
                    w
                    for w in self._file_workers.get(path, set())
                    if w != worker_id
                ]
                if others:
                    result[path] = sorted(others)
        return result

    def should_wait(self, worker_id: str) -> list[str]:
        """Return the deduplicated list of other workers *worker_id* should wait for.

        These are all workers that share at least one file with *worker_id*.
        Order is deterministic (sorted) but carries no priority information.
        """
        ov = self.overlaps(worker_id)
        seen: set[str] = set()
        for workers in ov.values():
            seen.update(workers)
        return sorted(seen)

    def all_claims(self) -> dict[str, list[str]]:
        """Return a snapshot of the inverted index: ``{file: [worker_ids]}``.

        Only files with at least one worker are included.  Stale claims are
        reaped first and never appear here.
        """
        now = self._clock()
        with self._lock:
            self._reap_stale_locked(now)
            return {
                path: sorted(workers)
                for path, workers in self._file_workers.items()
                if workers
            }

    def claims_with_age(self) -> dict[str, dict[str, object]]:
        """Return per-worker claim details: files, claimed_at, and age.

        ``{worker_id: {"files": [...], "claimed_at": float, "age_seconds": float}}``.
        Stale claims are reaped first and never appear here. Additive helper
        for API responses that want claim freshness alongside the file map.
        """
        now = self._clock()
        with self._lock:
            self._reap_stale_locked(now)
            return {
                worker_id: {
                    "files": sorted(files),
                    "claimed_at": self._worker_claimed_at.get(worker_id),
                    "age_seconds": now - self._worker_claimed_at[worker_id],
                }
                for worker_id, files in self._worker_files.items()
                if worker_id in self._worker_claimed_at
            }

    def merge_plan(self) -> dict[str, str]:
        """Classify each contested file as ``"serialize"`` or ``"union"``.

        A file is contested when >1 worker claims it.  The default heuristic
        classifies every contested file as ``"union"`` (meaning the changes can
        be combined) — callers can override per file if they have richer
        context.

        Returns ``{}`` when there are no contested files.  Stale claims are
        reaped first and never appear here.
        """
        now = self._clock()
        with self._lock:
            self._reap_stale_locked(now)
            return {
                path: "union"
                for path, workers in self._file_workers.items()
                if len(workers) > 1
            }
