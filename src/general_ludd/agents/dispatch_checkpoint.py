"""B3.1.5 — Durable hibernation store + dispatch-lifecycle checkpoints.

The pre-existing :class:`HibernationStore` used an ephemeral per-process MAC
key, so a writer crash abandoned every in-flight dispatch: the new process
had no key to re-verify the snapshots, and the dispatch lifecycle wrote no
checkpoints to resume from anyway. This module closes both gaps.

* :class:`DurableHibernationStore` keys the MAC from a long-lived file
  (default ``~/.local/share/general-ludd/hibernation.key``, 0o600, generated
  on first boot) so a restarted writer can re-verify and rehydrate snapshots
  its dead predecessor wrote.
* :class:`DispatchState` captures the resumable bits of a single
  ``_dispatch_execute_job`` invocation (resolved profiles, prompt text, tool
  loop progress, lease holder) so an interrupted dispatch can be re-run
  rather than silently dropped.
* :class:`CheckpointManager` dehydrates a snapshot + dispatch state at three
  boundaries (pre-model, per-tool-iter, clear-on-persist), lists interrupted
  dispatches on boot, and persists a small spool-offset sidecar so a
  restarted writer child does not re-apply already-applied inbound envelopes.

Design constraints (inherited from :mod:`hibernation`):
  - Pydantic-validated JSON only, never pickle.
  - Path-traversal safe; integrity-checked via HMAC-SHA256.
  - Owner-only files (0o600) — a snapshot can carry sensitive prompts.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from filelock import FileLock

from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    DispatchState,
    HibernationHandle,
    HibernationStore,
    IntegrityError,
    default_hibernation_dir,
)
from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event
from general_ludd.schemas.project_identity import ProjectWorkIdentity, validate_project_id

logger = logging.getLogger(__name__)

__all__ = [
    "CheckpointManager",
    "DispatchResumedEvent",
    "DispatchState",
    "DurableHibernationStore",
]

# Default location of the long-lived MAC key. Lives alongside the snapshots
# directory (sibling of the hibernation base) so a test passing either path
# via a kwarg keeps everything tmp_path-scoped. Constructed lazily on first
# access because default_hibernation_dir() reads env vars that a test may
# patch AFTER this module is imported.
def _default_key_file() -> Path:
    return default_hibernation_dir().parent / "hibernation.key"

# Sidecar filename stem: "<task_id>.spool.json". Same sanitizer as
# HibernationStore._path_for so a hostile task_id cannot escape the dir.
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def _safe_stem(task_id: str) -> str:
    stem = _UNSAFE_ID.sub("_", task_id).strip("._") or "unnamed"
    return stem


class DispatchResumedEvent(Event):
    """Emitted when an interrupted dispatch is resumed after a crash.

    The observability contract (No Unseen Events): every successful resume
    surfaces as an event so an operator can see crash-recovery happening
    rather than inferring it from a tick log.
    """

    def __init__(self, todo_id: str, phase: str, **kwargs: Any) -> None:
        """Initialize a content-free resume event."""
        super().__init__(
            type="dispatch_resumed",
            payload={"todo_id": todo_id, "phase": phase},
            **kwargs,
        )


class DurableHibernationStore(HibernationStore):
    """``HibernationStore`` keyed by a long-lived MAC key file.

    The base class generates a random 32-byte key per instance and holds it
    only in RAM — snapshots are scoped to one process and intentionally not
    portable across a restart. This subclass loads (or generates) the key
    from *key_file* so a restarted writer can re-verify and rehydrate its
    predecessor's snapshots. The file is created owner-only (0o600) on first
    boot; a missing parent directory is created.
    """

    def __init__(
        self,
        base_dir: str | Path | None = None,
        *,
        key_file: str | Path | None = None,
    ) -> None:
        """Initialize the durable store and its long-lived signing key."""
        super().__init__(base_dir)
        self._key_file: Path = (
            Path(key_file).resolve() if key_file is not None else _default_key_file()
        )
        self._mac_key = self._load_or_create_key(
            self._key_file,
            has_prior_checkpoints=any(self.base_dir.glob("*.snapshot.json")),
        )

    @staticmethod
    def _load_or_create_key(
        key_file: Path,
        *,
        has_prior_checkpoints: bool = False,
    ) -> bytes:
        """Return the 32-byte MAC key, creating it on first boot.

        Owner-only (0o600) on creation; if the file exists it is read as-is.
        A non-empty but short/wrong-length file is overwritten — it is not a
        valid key. Parent directories are created (the default key file lives
        under ``~/.local/share/general-ludd`` which may not exist yet).
        """
        key_file.parent.mkdir(parents=True, exist_ok=True)
        if key_file.exists():
            data = key_file.read_bytes()
            if len(data) == 32:
                return data
            if has_prior_checkpoints:
                raise IntegrityError(
                    "durable hibernation key is corrupt while prior checkpoints "
                    "exist; refusing to replace ownership proof"
                )
            logger.warning(
                "hibernation key file %s is wrong size (%d); regenerating",
                key_file,
                len(data),
            )
        elif has_prior_checkpoints:
            raise IntegrityError(
                "durable hibernation key is missing while prior checkpoints "
                "exist; refusing to mint a replacement ownership proof"
            )
        key = secrets.token_bytes(32)
        # Atomic owner-only write: O_CREAT|O_TRUNC + 0o600 at open time so
        # the file is never briefly world-readable.
        fd = os.open(
            str(key_file),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
        # Belt-and-braces: ensure owner-only even if the file pre-existed
        # with looser perms and O_CREAT did not reset the mode.
        with _suppress_oserror():
            os.chmod(key_file, 0o600)
        return key


class CheckpointManager:
    """Drives the three checkpoint boundaries of a dispatch lifecycle.

    Boundaries (see ``_dispatch_execute_job`` in loop.py):

      1. **pre_model** — after claim + prompt resolution, before the model
         is called. A crash here resumes by re-running with the preserved
         prompt_text.
      2. **mid_tool_loop** — updated per ``ToolCallLoop`` iteration. A crash
         here resumes the tool loop with the prior iteration count.
      3. **clear_on_persist** — after ``persist_task_return`` succeeds, the
         checkpoint is deleted; the dispatch is committed and not
         resumable.

    The manager is intentionally storage-mechanism-agnostic: it wraps a
    :class:`HibernationStore` (typically the durable variant) so the
    integrity / path-jail guarantees are inherited.
    """

    def __init__(
        self,
        store: HibernationStore,
        event_bus: EventBus | None = None,
    ) -> None:
        """Initialize checkpoint storage with an optional event sink."""
        self._store = store
        self._bus = event_bus

    @property
    def store(self) -> HibernationStore:
        """Return the underlying integrity-checked snapshot store."""
        return self._store

    # ------------------------------------------------------------------ #
    # Checkpoint write/clear
    # ------------------------------------------------------------------ #
    def checkpoint(
        self,
        snap: AgentEnvironmentSnapshot,
        *,
        phase: str,
    ) -> HibernationHandle:
        """Dehydrate *snap* (with its embedded ``dispatch_state``) under *phase*.

        The snapshot's ``dispatch_state.phase_marker`` is updated to *phase*
        before serialization so a resumed dispatch sees where its predecessor
        stopped. Returns the integrity handle (the caller does not usually
        need it — the on-disk file is what survives the crash).
        """
        state = snap.dispatch_state
        if state is None:
            return self._store.dehydrate(snap)
        state.phase_marker = phase
        identity = self._resume_identity(
            snap.task_id,
            project_id=state.project_id,
            shard_id=state.resume_shard_id,
        )
        if state.todo_id != snap.task_id:
            raise ValueError("dispatch state todo_id must match snapshot task_id")
        # The storage key itself is project scoped. The durable payload retains
        # the business todo id in DispatchState and is normalized on listing.
        stored = snap.model_copy(
            deep=True,
            update={"task_id": identity.resume_shard_id},
        )
        return self._store.dehydrate(stored)

    def clear(
        self,
        task_id: str,
        *,
        project_id: str,
        shard_id: str,
    ) -> None:
        """Remove the checkpoint for *task_id*.

        Called from the ``clear_on_persist`` boundary: the dispatch committed
        so its checkpoint is no longer actionable. Idempotent — clearing a
        missing checkpoint is a no-op.
        """
        identity = self._resume_identity(
            task_id,
            project_id=project_id,
            shard_id=shard_id,
        )
        path = self._store._path_for(identity.resume_shard_id)
        with _suppress_oserror():
            path.unlink(missing_ok=True)
        # Best-effort cleanup of any spool sidecar too.
        sidecar = self._sidecar_path(identity.resume_shard_id)
        with _suppress_oserror():
            sidecar.unlink(missing_ok=True)
        claim = self._resume_claim_path(identity.resume_shard_id)
        with self._resume_claim_lock(identity.resume_shard_id), _suppress_oserror():
            claim.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # Resume enumeration
    # ------------------------------------------------------------------ #
    def list_interrupted(self) -> list[AgentEnvironmentSnapshot]:
        """Return every checkpoint snapshot currently on disk.

        On boot the event loop calls this once; each returned snapshot is a
        dispatch interrupted by a crash. The caller is responsible for
        filtering (skip COMPLETED todos) and re-acquiring leases before
        re-running.
        """
        out: list[AgentEnvironmentSnapshot] = []
        for candidate in sorted(self._store.base_dir.glob("*.snapshot.json")):
            raw = self._read_raw_envelope(candidate)
            if raw is None:
                continue
            checksum = raw.get("checksum")
            if not isinstance(checksum, str):
                continue
            try:
                snap = self._store.hydrate(
                    HibernationHandle(
                        task_id=candidate.name,
                        path=str(candidate),
                        checksum=checksum,
                        size_bytes=candidate.stat().st_size,
                    )
                )
            except Exception:
                logger.warning(
                    "checkpoint manager: unverifiable snapshot at %s; skipping",
                    candidate,
                )
                continue
            # Skip legacy (pre-B3.1.5) snapshots that have no dispatch_state —
            # they are deep-recursion hibernation files, not dispatch
            # checkpoints, and have no resumable work.
            if snap.dispatch_state is None:
                continue
            state = snap.dispatch_state
            try:
                identity = self._resume_identity(
                    state.todo_id,
                    project_id=state.project_id,
                    shard_id=state.resume_shard_id,
                )
            except ValueError:
                logger.warning(
                    "checkpoint manager: invalid ownership scope at %s; skipping",
                    candidate,
                )
                continue
            if snap.task_id != identity.resume_shard_id:
                logger.warning(
                    "checkpoint manager: storage shard mismatch at %s; skipping",
                    candidate,
                )
                continue
            snap.task_id = state.todo_id
            out.append(snap)
        return out

    def filter_actionable_sync(
        self,
        snaps: list[AgentEnvironmentSnapshot],
        *,
        statuses: dict[str, str],
    ) -> list[AgentEnvironmentSnapshot]:
        """Filter *snaps* to those whose todo is NOT already COMPLETED.

        Pure-sync helper: the caller pre-resolves the per-todo status from
        the DB and passes a {todo_id: status} map. This keeps the filter
        testable without an async DB fixture and avoids an awaitable
        boundary inside a list operation.
        """
        return [
            s for s in snaps
            if statuses.get(s.task_id, "PENDING") != "COMPLETED"
        ]

    def mark_resumed(self, task_id: str, *, phase: str) -> None:
        """Emit the ``dispatch_resumed`` observability event.

        Called by the resume path after a checkpoint has been hydrated and
        the dispatch has been scheduled to re-run. No-op when no bus was
        wired (test fixture / log-only deployments).
        """
        if self._bus is None:
            logger.info(
                "dispatch resumed (no bus): todo=%s phase=%s", task_id, phase,
            )
            return
        self._bus.publish(DispatchResumedEvent(todo_id=task_id, phase=phase))

    def _resume_claim_path(self, shard_id: str) -> Path:
        return self._store.base_dir / f"{_safe_stem(shard_id)}.resume-claim.json"

    def _resume_claim_lock(self, shard_id: str) -> FileLock:
        lock_path = self._store.base_dir / f"{_safe_stem(shard_id)}.resume-claim.lock"
        return FileLock(str(lock_path), timeout=10, mode=0o600)

    @staticmethod
    def _resume_identity(
        task_id: str,
        *,
        project_id: str | None,
        shard_id: str | None,
    ) -> ProjectWorkIdentity:
        """Validate that task, project, and shard name the same owned work."""
        if not isinstance(project_id, str):
            raise ValueError("project_id is required for a resume shard")
        identity = ProjectWorkIdentity(project_id=project_id, todo_id=task_id)
        if shard_id != identity.resume_shard_id:
            raise ValueError("resume shard does not match project and task identity")
        return identity

    @staticmethod
    def _valid_resume_token(value: str, field_name: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value.encode("utf-8")) > 256
            or any(char in value for char in "\r\n\x00")
        ):
            raise ValueError(f"{field_name} must be bounded non-empty text")
        return value

    def claim_resume(
        self,
        task_id: str,
        *,
        project_id: str,
        shard_id: str,
        owner_id: str,
        ttl_seconds: int = 300,
    ) -> bool:
        """Atomically own one resumable checkpoint shard across processes.

        The maintained ``filelock`` library serializes stale-claim takeover and
        refresh. The small owner-only JSON record survives process restarts; an
        unexpired claim held by another event loop fails closed.
        """
        validate_project_id(project_id)
        self._valid_resume_token(task_id, "task_id")
        self._valid_resume_token(shard_id, "shard_id")
        self._valid_resume_token(owner_id, "owner_id")
        identity = self._resume_identity(
            task_id,
            project_id=project_id,
            shard_id=shard_id,
        )
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or not 1 <= ttl_seconds <= 86_400
        ):
            raise ValueError("ttl_seconds must be an integer between 1 and 86400")
        claim_path = self._resume_claim_path(identity.resume_shard_id)
        now = time.time()
        with self._resume_claim_lock(identity.resume_shard_id):
            current: dict[str, Any] = {}
            try:
                value = json.loads(claim_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    current = value
            except FileNotFoundError:
                current = {}
            except (OSError, json.JSONDecodeError):
                return False
            current_owner = current.get("owner_id")
            current_expiry = current.get("expires_at")
            if current_owner == owner_id:
                if (
                    current.get("project_id") != project_id
                    or current.get("shard_id") != shard_id
                ):
                    return False
            elif isinstance(current_expiry, (int, float)) and current_expiry > now:
                return False
            payload = {
                "expires_at": now + ttl_seconds,
                "owner_id": owner_id,
                "project_id": project_id,
                "shard_id": shard_id,
                "task_id": task_id,
            }
            tmp = claim_path.with_name(f"{claim_path.name}.{os.getpid()}.tmp")
            fd = os.open(
                str(tmp),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            try:
                os.write(
                    fd,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
                )
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, claim_path)
            return True

    def release_resume_claim(
        self,
        task_id: str,
        *,
        project_id: str,
        shard_id: str,
        owner_id: str,
    ) -> bool:
        """Release one resume shard only for its exact durable owner."""
        self._valid_resume_token(task_id, "task_id")
        self._valid_resume_token(owner_id, "owner_id")
        identity = self._resume_identity(
            task_id,
            project_id=project_id,
            shard_id=shard_id,
        )
        claim_path = self._resume_claim_path(identity.resume_shard_id)
        with self._resume_claim_lock(identity.resume_shard_id):
            try:
                value = json.loads(claim_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            if (
                not isinstance(value, dict)
                or value.get("owner_id") != owner_id
                or value.get("project_id") != project_id
                or value.get("shard_id") != shard_id
                or value.get("task_id") != task_id
            ):
                return False
            claim_path.unlink(missing_ok=True)
            return True

    # ------------------------------------------------------------------ #
    # Spool-offset sidecar
    # ------------------------------------------------------------------ #
    def _sidecar_path(self, task_id: str) -> Path:
        return self._store.base_dir / f"{_safe_stem(task_id)}.spool.json"

    def spool_sidecar_path(self, task_id: str) -> Path:
        """Public accessor for the spool-offset sidecar path (test surface)."""
        return self._sidecar_path(task_id)

    def write_spool_offset(self, task_id: str, *, offset: int) -> None:
        """Persist *offset* — the byte offset the writer child has drained to.

        Atomic write (tmp + os.replace) so a crash mid-write cannot leave a
        truncated sidecar that would mislead a restarted child into skipping
        half the spool. Owner-only (0o600) at open time.
        """
        path = self._sidecar_path(task_id)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps({"offset": offset, "todo_id": task_id}).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)

    def read_spool_offset(self, task_id: str) -> int | None:
        """Return the last-persisted spool offset for *task_id*, or None.

        None means "no sidecar — the caller starts at offset 0". A corrupt
        sidecar is logged and treated as None (fail-safe: re-drain from 0
        rather than silently skip the spool).
        """
        path = self._sidecar_path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "spool sidecar for %s unreadable; re-draining from 0", task_id,
            )
            return None
        offset = data.get("offset")
        if not isinstance(offset, int) or offset < 0:
            return None
        return offset

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _read_raw_envelope(path: Path) -> dict[str, Any] | None:
        """Read a snapshot envelope *without* integrity verification.

        ``list_interrupted`` enumerates files written by a prior process; we
        cannot re-MAC them here (the durable key IS available, but a tampered
        file should be skipped during enumeration and surfaced only when an
        operator explicitly attempts hydration). Returns the parsed envelope
        dict, or None on read/parse failure.
        """
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(envelope, dict):
            return None
        return envelope


class _suppress_oserror:
    """Suppress OSError on best-effort cleanup (mirror hibernation.py style)."""

    def __enter__(self) -> _suppress_oserror:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None
