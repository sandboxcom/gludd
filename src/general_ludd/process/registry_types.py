"""Data and ownership-capability types for the managed-process registry."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProcessRegistryError(Exception):
    """Raised when a registry operation is refused."""


class OwnedProcessHandle(Protocol):
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
    origin: str = ""
    registered_at: float = field(default_factory=time.time)
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


class _LeaseRegistry(Protocol):
    def _release_owned_process(self, record: ManagedProcess) -> bool:
        """Remove the exact record owned by one lease."""
        ...


class ManagedProcessLease:
    """Single-owner capability for one exact managed-process record."""

    def __init__(self, registry: _LeaseRegistry, record: ManagedProcess) -> None:
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
