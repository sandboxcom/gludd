"""In-process registry of OS processes that gludd has started.

The registry is the safety foundation for agent-driven process management. Two
invariants make signalling safe:

1. **Confinement** — only PIDs that were explicitly :meth:`register`ed (i.e.
   processes gludd itself started) can be signalled or monitored through the
   registry. There is no path to signal an arbitrary system PID.
2. **Identity (anti PID-reuse)** — each record stores the process's OS
   ``create_time`` captured at registration. Before any signal is delivered the
   live process's ``create_time`` is re-read and compared; if the kernel has
   recycled the PID for a different process the identity check fails and the
   signal is refused. This closes the classic TOCTOU where a stale PID is reused
   by an unrelated (possibly more privileged) process between registration and
   signalling.

Thread-safety: spawn sites run on worker threads (the ansible runner offloads via
``asyncio.to_thread``; deploy/inference use their own threads), so every read and
mutation of the shared map is serialised under an :class:`~threading.RLock`. It is
an ``RLock`` because :meth:`signal` and :meth:`reap` compose other locked helpers
on the same thread; a plain ``Lock`` would self-deadlock.

``psutil`` (a core dependency) provides ``create_time`` and liveness. It is
imported lazily so the pure data structure still works in stripped environments;
when it is unavailable identity cannot be verified and signalling fails closed.
"""

from __future__ import annotations

import logging
import os
import threading

from general_ludd.process.registry_identity import read_create_time
from general_ludd.process.registry_types import (
    ManagedProcess,
    ManagedProcessLease,
    ProcessRegistryError,
)
from general_ludd.process.registry_types import (
    OwnedProcessHandle as _OwnedProcessHandle,
)
from general_ludd.process.signal_policy import (
    allowed_signals as _allowed_signals,
)
from general_ludd.process.signal_policy import (
    deliver_signal as _deliver_signal,
)
from general_ludd.process.signal_policy import (
    resolve_signal as _resolve_signal,
)
from general_ludd.process.signal_policy import (
    validate_target as _validate_signal_target,
)

logger = logging.getLogger(__name__)

# Module-scope alias so annotations inside ProcessRegistry (which has a method
# named `list` that shadows the builtin in class body) resolve the builtin.
_PidList = list[int]

# create_time() is a float of seconds since epoch; identical reads are exactly
# equal, but allow a tiny tolerance for float round-tripping through JSON.
_CREATE_TIME_TOLERANCE_S = 0.5
_DEFAULT_MAX_RECORDS = 256


_ManagedProcessList = list[ManagedProcess]


def _read_create_time(pid: int) -> float | None:
    """Return the live process's create_time, or None if absent/unreadable."""
    return read_create_time(pid)


class ProcessRegistry:
    """Thread-safe registry of gludd-managed processes."""

    def __init__(self, *, max_records: int = _DEFAULT_MAX_RECORDS) -> None:
        """Initialize an empty registry with a fixed record capacity."""
        if isinstance(max_records, bool) or not isinstance(max_records, int):
            raise TypeError("max_records must be an integer")
        if max_records < 1:
            raise ValueError("max_records must be at least 1")
        self._procs: dict[int, ManagedProcess] = {}
        self._lock = threading.RLock()
        self._sealed: bool = False
        self._max_records = max_records
        self._owner_leases_acquired = 0
        self._owner_leases_released = 0
        self._stale_records_pruned = 0
        self._capacity_rejections = 0

    def seal(self) -> None:
        """Prevent further structural modification after daemon initialization.

        Once sealed the registry rejects the unrestricted ``register``,
        ``deregister``, and ``reap`` methods. Runtime owners may still use
        :meth:`lease_owned_process`, which binds registration and cleanup to a
        concrete started-process handle. :meth:`active_snapshot` may only evict
        records that fail the PID/create-time identity check.

        Sealing is one-way and idempotent: calling ``seal`` a second time is
        a no-op.
        """
        self._sealed = True

    @property
    def is_sealed(self) -> bool:
        """True after :meth:`seal` has been called."""
        return self._sealed

    def _require_unsealed(self, operation: str) -> None:
        if self._sealed:
            raise ProcessRegistryError(
                f"Registry is sealed — cannot {operation} after daemon init. "
                f"Call seal() only after all managed processes are registered."
            )

    # -- registration -----------------------------------------------------

    def register(
        self,
        pid: int,
        command: list[str] | tuple[str, ...] | str,
        *,
        pgid: int | None = None,
        job_id: str | None = None,
        project_id: str | None = None,
        origin: str = "",
    ) -> ManagedProcess:
        """Record a process gludd has just started.

        ``create_time`` is captured now so later signals can verify identity. If
        ``pgid`` is not supplied it is best-effort resolved via ``os.getpgid``.

        Raises :class:`ProcessRegistryError` if the registry is sealed.
        """
        self._require_unsealed("register")
        command_list = [command] if isinstance(command, str) else list(command)
        if pgid is None:
            try:
                pgid = os.getpgid(pid)
            except (ProcessLookupError, PermissionError, OSError):
                pgid = None
        record = ManagedProcess(
            pid=int(pid),
            command=command_list,
            pgid=pgid,
            job_id=job_id,
            project_id=project_id,
            origin=origin,
            create_time=_read_create_time(pid),
        )
        with self._lock:
            self._prune_stale_locked(self._max_records)
            if int(pid) not in self._procs and len(self._procs) >= self._max_records:
                self._capacity_rejections += 1
                raise ProcessRegistryError(
                    f"managed-process registry capacity {self._max_records} reached"
                )
            self._procs[int(pid)] = record
        logger.debug("registered managed process pid=%s origin=%s", pid, origin)
        return record

    def deregister(self, pid: int) -> ManagedProcess | None:
        """Drop a process from the registry (e.g. after it exits). Idempotent.

        Raises :class:`ProcessRegistryError` if the registry is sealed.
        """
        self._require_unsealed("deregister")
        with self._lock:
            return self._procs.pop(int(pid), None)

    def lease_owned_process(
        self,
        process: _OwnedProcessHandle,
        command: list[str] | tuple[str, ...] | str,
        *,
        pgid: int | None = None,
        job_id: str | None = None,
        project_id: str | None = None,
        origin: str = "",
    ) -> ManagedProcessLease:
        """Register a started child through an identity-bound owner lease.

        This is the narrow runtime mutation path that remains valid after
        :meth:`seal`. The caller must hold the concrete process handle it
        started. Registration fails closed when the PID or its creation time
        cannot be established. Capacity enforcement first performs one bounded
        PID-safe prune across at most ``max_records`` entries.
        """
        pid_value = process.pid
        if (
            isinstance(pid_value, bool)
            or not isinstance(pid_value, int)
            or pid_value < 1
        ):
            raise ProcessRegistryError(
                "owned process must be started and expose a positive integer pid"
            )
        create_time = _read_create_time(pid_value)
        if create_time is None:
            raise ProcessRegistryError(
                f"cannot verify owned process identity for pid {pid_value}"
            )
        if pgid is None:
            try:
                pgid = os.getpgid(pid_value)
            except (ProcessLookupError, PermissionError, OSError):
                pgid = None
        command_list = [command] if isinstance(command, str) else list(command)
        record = ManagedProcess(
            pid=pid_value,
            command=command_list,
            pgid=pgid,
            job_id=job_id,
            project_id=project_id,
            origin=origin,
            create_time=create_time,
        )
        if not self._identity_ok(record):
            raise ProcessRegistryError(
                f"owned process pid {pid_value} exited or changed identity "
                "before registration"
            )

        with self._lock:
            self._prune_stale_locked(self._max_records)
            if pid_value in self._procs:
                raise ProcessRegistryError(
                    f"owned process pid {pid_value} is already managed"
                )
            if len(self._procs) >= self._max_records:
                self._capacity_rejections += 1
                raise ProcessRegistryError(
                    f"managed-process registry capacity {self._max_records} reached"
                )
            self._procs[pid_value] = record
            self._owner_leases_acquired += 1
        logger.info(
            "MANAGED_PROCESS_LEASE_ACQUIRED pid=%s origin=%s",
            pid_value,
            origin,
        )
        return ManagedProcessLease(self, record)

    def _release_owned_process(self, record: ManagedProcess) -> bool:
        """Release only ``record``; never remove a newer record for its PID."""
        with self._lock:
            if self._procs.get(record.pid) is not record:
                return False
            del self._procs[record.pid]
            self._owner_leases_released += 1
        logger.info(
            "MANAGED_PROCESS_LEASE_RELEASED pid=%s origin=%s",
            record.pid,
            record.origin,
        )
        return True

    # -- queries ----------------------------------------------------------

    def get(self, pid: int) -> ManagedProcess | None:
        """Return the managed record for ``pid`` when present."""
        with self._lock:
            return self._procs.get(int(pid))

    def is_managed(self, pid: int) -> bool:
        """Return whether ``pid`` currently has a managed record."""
        with self._lock:
            return int(pid) in self._procs

    def list(self, *, active_only: bool = False) -> list[ManagedProcess]:
        """Return managed processes. ``active_only`` filters to live + identity-OK."""
        with self._lock:
            records = list(self._procs.values())
        if not active_only:
            return records
        return [r for r in records if self._identity_ok(r)]

    def active_snapshot(self) -> _ManagedProcessList:
        """Return live records after one bounded, PID-safe stale prune.

        Unlike the unrestricted :meth:`reap` setup API, this method remains
        safe after sealing because it can only remove a record whose live
        PID/create-time identity no longer matches. The registry capacity is
        also the hard upper bound on identity probes per call.
        """
        with self._lock:
            self._prune_stale_locked(self._max_records)
            return list(self._procs.values())

    def metrics(self) -> dict[str, int]:
        """Return content-free counters for owner lifecycle observability."""
        with self._lock:
            return {
                "capacity": self._max_records,
                "current": len(self._procs),
                "owner_leases_acquired_total": self._owner_leases_acquired,
                "owner_leases_released_total": self._owner_leases_released,
                "stale_records_pruned_total": self._stale_records_pruned,
                "capacity_rejections_total": self._capacity_rejections,
            }

    # -- identity / liveness ---------------------------------------------

    def _identity_ok(self, record: ManagedProcess) -> bool:
        """True when ``record``'s PID is live AND is the same process we recorded.

        A live PID whose ``create_time`` differs from the recorded value has been
        recycled by the kernel for a different process — treated as NOT ours.
        When identity cannot be established (no psutil, or we never captured a
        create_time) this returns False: callers fail closed.
        """
        live_ct = _read_create_time(record.pid)
        if live_ct is None:
            return False
        if record.create_time is None:
            return False
        return abs(live_ct - record.create_time) <= _CREATE_TIME_TOLERANCE_S

    def is_alive(self, pid: int) -> bool:
        """True only if the recorded PID is still our process (identity-checked)."""
        record = self.get(pid)
        if record is None:
            return False
        return self._identity_ok(record)

    # -- signalling -------------------------------------------------------

    def signal(
        self,
        pid: int,
        sig: int | str,
        *,
        group: bool = False,
    ) -> None:
        """Deliver ``sig`` to a managed process, refusing anything unsafe.

        ``sig`` may be a signal number or a name (e.g. ``"SIGTERM"``) and must be
        in the allow-list. The target must be registered and pass the identity
        check. With ``group=True`` the signal is sent to the process group
        (``os.killpg``) so child processes are included — useful for the ansible
        runner's ``setsid`` workers.

        Raises :class:`ProcessRegistryError` on any refusal (unknown PID,
        disallowed signal, failed identity check) — never a silent no-op.
        """
        signum = self.resolve_signal(sig)
        with self._lock:
            record = self._procs.get(int(pid))
            if record is None:
                raise ProcessRegistryError(
                    f"refusing to signal pid {pid}: not a gludd-managed process"
                )
            target_pgid = _validate_signal_target(record, self._identity_ok)
        _deliver_signal(
            int(pid),
            signum,
            group=group,
            pgid=target_pgid,
        )

    @staticmethod
    def resolve_signal(sig: int | str) -> int:
        """Map a signal name or number to an allowed signal number, else raise."""
        return _resolve_signal(sig)

    @staticmethod
    def allowed_signals() -> dict[str, int]:
        """Return a copy of the signal allow-list (name -> number)."""
        return _allowed_signals()

    # -- maintenance ------------------------------------------------------

    def _prune_stale_locked(self, limit: int) -> _PidList:
        """Prune at most ``limit`` stale identities while ``_lock`` is held."""
        evicted: list[int] = []
        for pid, record in list(self._procs.items())[:limit]:
            if self._identity_ok(record):
                continue
            if self._procs.get(pid) is record:
                del self._procs[pid]
                evicted.append(pid)
        self._stale_records_pruned += len(evicted)
        if evicted:
            logger.info(
                "MANAGED_PROCESS_STALE_PRUNED count=%s",
                len(evicted),
            )
        return evicted

    def reap(self) -> _PidList:
        """Drop records whose process has exited or whose PID was reused.

        Returns the list of PIDs evicted. Safe to call periodically from the
        event loop to keep the registry bounded.

        Raises :class:`ProcessRegistryError` if the registry is sealed.
        """
        self._require_unsealed("reap")
        with self._lock:
            return self._prune_stale_locked(self._max_records)


_DEFAULT_REGISTRY: ProcessRegistry | None = None
_DEFAULT_REGISTRY_LOCK = threading.Lock()


def default_registry() -> ProcessRegistry:
    """Return the process-wide singleton registry (lazily created)."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        with _DEFAULT_REGISTRY_LOCK:
            if _DEFAULT_REGISTRY is None:
                _DEFAULT_REGISTRY = ProcessRegistry()
    return _DEFAULT_REGISTRY


def set_default_registry(registry: ProcessRegistry) -> None:
    """Eagerly set the process-wide singleton registry and seal it.

    Must be called before any code calls ``default_registry()``. After this
    call the registry is sealed — unrestricted ``register``, ``deregister``,
    and ``reap`` are rejected until shutdown. Started-child owners use
    ``lease_owned_process`` for the narrow runtime lifecycle path.

    Called once from the daemon's startup sequence so the process registry
    exists and is immutable before the HTTP server accepts requests.
    """
    global _DEFAULT_REGISTRY
    with _DEFAULT_REGISTRY_LOCK:
        if _DEFAULT_REGISTRY is not None:
            raise RuntimeError(
                "default_registry is already set — eager init must happen "
                "before any code calls default_registry()"
            )
        registry.seal()
        _DEFAULT_REGISTRY = registry
