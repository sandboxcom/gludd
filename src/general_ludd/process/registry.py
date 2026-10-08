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
import signal as _signal
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Module-scope alias so annotations inside ProcessRegistry (which has a method
# named `list` that shadows the builtin in class body) resolve the builtin.
_PidList = list[int]

# The signals an agent may deliver to a managed process. This is an allow-list:
# anything outside it is rejected before it reaches the kernel. It covers the
# graceful-shutdown, reload, user-defined "event", and hard-stop families the
# feature needs, plus job-control stop/continue.
_ALLOWED_SIGNALS: dict[str, int] = {
    name: int(getattr(_signal, name))
    for name in (
        "SIGTERM",
        "SIGINT",
        "SIGHUP",
        "SIGQUIT",
        "SIGUSR1",
        "SIGUSR2",
        "SIGKILL",
        "SIGSTOP",
        "SIGCONT",
    )
    if hasattr(_signal, name)
}

# create_time() is a float of seconds since epoch; identical reads are exactly
# equal, but allow a tiny tolerance for float round-tripping through JSON.
_CREATE_TIME_TOLERANCE_S = 0.5
_DEFAULT_MAX_RECORDS = 256


class ProcessRegistryError(Exception):
    """Raised when a registry operation is refused (unknown/identity/signal)."""


class _OwnedProcessHandle(Protocol):
    """Started process handle accepted by the trusted owner-lease path."""

    @property
    def pid(self) -> int | None:
        """Return the child PID after it has started."""
        ...


@dataclass
class ManagedProcess:
    """Metadata for a single gludd-started OS process."""

    pid: int
    command: list[str]
    pgid: int | None = None
    job_id: str | None = None
    project_id: str | None = None
    # Free-form provenance, e.g. "ansible_runner", "compute_deploy", "mcp_stdio".
    origin: str = ""
    # Wall-clock epoch when gludd registered the process.
    registered_at: float = field(default_factory=time.time)
    # OS process creation time (psutil.Process.create_time()); the identity key
    # used to detect PID reuse. None when psutil was unavailable at registration.
    create_time: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of this process metadata."""
        return {
            "pid": self.pid,
            "command": list(self.command),
            "pgid": self.pgid,
            "job_id": self.job_id,
            "project_id": self.project_id,
            "origin": self.origin,
            "registered_at": self.registered_at,
            "create_time": self.create_time,
        }


class ManagedProcessLease:
    """Single-owner capability for one exact managed-process record.

    A lease can remove only the same record object it created. If the process
    exits and its PID is later reused, releasing the old lease cannot evict the
    replacement record.
    """

    def __init__(
        self,
        registry: ProcessRegistry,
        record: ManagedProcess,
    ) -> None:
        """Initialize a lease for one exact registry record."""
        self._registry = registry
        self._record = record
        self._released = False

    @property
    def pid(self) -> int:
        """Return the PID captured by this lease."""
        return self._record.pid

    def release(self) -> bool:
        """Release this exact record once; return whether it was removed."""
        if self._released:
            return False
        self._released = True
        return self._registry._release_owned_process(self._record)

    def __enter__(self) -> ManagedProcessLease:
        """Return this lease for context-managed ownership."""
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        """Release the owned record when its context exits."""
        self.release()


_ManagedProcessList = list[ManagedProcess]


def _psutil() -> Any | None:
    """Return the psutil module, or None if it is not importable."""
    try:
        import psutil

        return psutil
    except Exception:
        return None


def _read_create_time(pid: int) -> float | None:
    """Return the live process's create_time, or None if absent/unreadable."""
    ps = _psutil()
    if ps is None:
        return None
    try:
        return float(ps.Process(pid).create_time())
    except Exception:
        return None


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
            if not self._identity_ok(record):
                raise ProcessRegistryError(
                    f"refusing to signal pid {pid}: process is gone or its PID was "
                    f"reused by a different process (identity check failed)"
                )
            target_pgid = record.pgid
        try:
            if group and target_pgid is not None:
                os.killpg(target_pgid, signum)
            else:
                os.kill(int(pid), signum)
        except ProcessLookupError as exc:
            raise ProcessRegistryError(
                f"process {pid} disappeared before the signal was delivered"
            ) from exc
        except PermissionError as exc:
            raise ProcessRegistryError(
                f"not permitted to signal process {pid}"
            ) from exc
        logger.info(
            "delivered %s to managed pid=%s (group=%s)",
            _signal.Signals(signum).name,
            pid,
            group,
        )

    @staticmethod
    def resolve_signal(sig: int | str) -> int:
        """Map a signal name or number to an allowed signal number, else raise."""
        if isinstance(sig, str):
            name = sig.strip().upper()
            if not name.startswith("SIG"):
                name = "SIG" + name
            if name not in _ALLOWED_SIGNALS:
                raise ProcessRegistryError(
                    f"signal {sig!r} is not in the allow-list "
                    f"({', '.join(sorted(_ALLOWED_SIGNALS))})"
                )
            return _ALLOWED_SIGNALS[name]
        signum = int(sig)
        if signum not in _ALLOWED_SIGNALS.values():
            raise ProcessRegistryError(
                f"signal number {signum} is not in the allow-list"
            )
        return signum

    @staticmethod
    def allowed_signals() -> dict[str, int]:
        """Return a copy of the signal allow-list (name -> number)."""
        return dict(_ALLOWED_SIGNALS)

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
