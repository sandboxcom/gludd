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
from typing import Any

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


class ProcessRegistryError(Exception):
    """Raised when a registry operation is refused (unknown/identity/signal)."""


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

    def __init__(self) -> None:
        self._procs: dict[int, ManagedProcess] = {}
        self._lock = threading.RLock()
        self._sealed: bool = False

    def seal(self) -> None:
        """Prevent further structural modification after daemon initialization.

        Once sealed the registry rejects ``register``, ``deregister``, and
        ``reap`` — the mutation surface that could be abused to inject or
        evict entries after the daemon's trusted setup phase is complete.
        Read-only operations (``get``, ``is_managed``, ``list``, ``is_alive``,
        ``signal``) remain available.

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

    # -- queries ----------------------------------------------------------

    def get(self, pid: int) -> ManagedProcess | None:
        with self._lock:
            return self._procs.get(int(pid))

    def is_managed(self, pid: int) -> bool:
        with self._lock:
            return int(pid) in self._procs

    def list(self, *, active_only: bool = False) -> list[ManagedProcess]:
        """Return managed processes. ``active_only`` filters to live + identity-OK."""
        with self._lock:
            records = list(self._procs.values())
        if not active_only:
            return records
        return [r for r in records if self._identity_ok(r)]

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

    def reap(self) -> _PidList:
        """Drop records whose process has exited or whose PID was reused.

        Returns the list of PIDs evicted. Safe to call periodically from the
        event loop to keep the registry bounded.

        Raises :class:`ProcessRegistryError` if the registry is sealed.
        """
        self._require_unsealed("reap")
        evicted: list[int] = []
        with self._lock:
            for pid, record in list(self._procs.items()):
                if not self._identity_ok(record):
                    del self._procs[pid]
                    evicted.append(pid)
        if evicted:
            logger.debug("reaped %d exited/reused managed pids", len(evicted))
        return evicted


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
    call the registry is sealed — ``register``, ``deregister``, and ``reap``
    are rejected until shutdown.

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
