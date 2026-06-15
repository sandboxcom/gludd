"""In-memory, thread-safe file claim registry for agent file-overlap coordination.

Workers call ``claim`` before editing files and ``release`` when they finish.
Any worker can then call ``overlaps`` or ``should_wait`` to discover conflicts
with other concurrent workers.  ``all_claims`` and ``merge_plan`` expose the
global state for merge-planning.

State is ephemeral — stored in process memory only.  Do NOT persist to a DB;
claims represent active-worker intent and are meaningless across restarts.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable


class FileClaimRegistry:
    """Thread-safe in-memory registry mapping worker IDs to claimed file paths.

    All public methods are safe to call concurrently from multiple threads.
    The canonical data model is ``_worker_files``: a dict from worker_id to
    the frozenset of paths it is currently editing.  ``_file_workers`` is the
    inverted index (file -> set of worker_ids) and is kept in sync on every
    mutation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # worker_id -> frozenset[file_path]
        self._worker_files: dict[str, frozenset[str]] = {}
        # file_path -> set[worker_id]  (inverted index, always in sync)
        self._file_workers: dict[str, set[str]] = {}

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def claim(self, worker_id: str, files: Iterable[str]) -> None:
        """Record that *worker_id* is editing *files*.

        If *worker_id* already has claims, they are replaced by this call
        (re-claiming is treated as updating the set of edited files).
        The operation is idempotent when the file set is unchanged.
        """
        file_set = frozenset(files)
        with self._lock:
            # Remove stale index entries for this worker before updating.
            self._remove_worker_from_index(worker_id)
            self._worker_files[worker_id] = file_set
            for path in file_set:
                self._file_workers.setdefault(path, set()).add(worker_id)

    def release(self, worker_id: str) -> None:
        """Drop all claims for *worker_id*.

        No-op if *worker_id* is not registered; never raises.
        """
        with self._lock:
            if worker_id not in self._worker_files:
                return
            self._remove_worker_from_index(worker_id)
            del self._worker_files[worker_id]

    def overlaps(self, worker_id: str) -> dict[str, list[str]]:
        """Return the conflict map for *worker_id*.

        For each file that *worker_id* claims AND at least one other worker also
        claims, the result contains ``{file: [other_worker_ids]}``.

        Returns ``{}`` when *worker_id* is unknown or has no conflicts.
        """
        with self._lock:
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

        Only files with at least one worker are included.
        """
        with self._lock:
            return {
                path: sorted(workers)
                for path, workers in self._file_workers.items()
                if workers
            }

    def merge_plan(self) -> dict[str, str]:
        """Classify each contested file as ``"serialize"`` or ``"union"``.

        A file is contested when >1 worker claims it.  The default heuristic
        classifies every contested file as ``"union"`` (meaning the changes can
        be combined) — callers can override per file if they have richer
        context.

        Returns ``{}`` when there are no contested files.
        """
        with self._lock:
            return {
                path: "union"
                for path, workers in self._file_workers.items()
                if len(workers) > 1
            }
