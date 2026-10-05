"""Sandbox contracts, configuration, results, and validation helpers.

Defines the formal interface boundary that every sandbox executor
(ProcessExecutor, DockerExecutor, KubernetesExecutor, FirecrackerBackend,
GvisorBackend) must satisfy. Callers depend on these contracts, not on
concrete executor types.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from general_ludd.sandbox.resource_limits import ResourceLimits

_DEFAULT_MAX_PROCESSES = 50

# ---------------------------------------------------------------------------
# IsolationLevel
# ---------------------------------------------------------------------------


class IsolationLevel(StrEnum):
    """Strength of sandbox boundary.

    Ordered weakest-to-strongest; higher = tighter isolation:
      NONE          — bare OS process, no sandbox
      PROCESS       — Landlock / Bubblewrap / Seatbelt (process-level)
      CONTAINER     — Docker / Kubernetes container
      VM_USERSPACE  — gVisor userspace kernel
      VM_HARDWARE   — Firecracker KVM microVM (strongest)
    """

    NONE = "none"
    PROCESS = "process"
    CONTAINER = "container"
    VM_USERSPACE = "vm_userspace"
    VM_HARDWARE = "vm_hardware"

    @classmethod
    def _missing_(cls, value: object) -> IsolationLevel | None:
        if isinstance(value, str):
            for member in cls:
                if member.value == value.lower():
                    return member
        return None

    def __repr__(self) -> str:
        """Return a stable qualified representation for diagnostics."""
        return f"{self.__class__.__name__}.{self.name}"


ISOLATION_RANK: dict[IsolationLevel, int] = {
    IsolationLevel.NONE: 2,
    IsolationLevel.PROCESS: 4,
    IsolationLevel.CONTAINER: 6,
    IsolationLevel.VM_USERSPACE: 8,
    IsolationLevel.VM_HARDWARE: 10,
}
"""Numeric rank for each isolation level. Higher = stronger isolation."""


def isolation_rank(level: IsolationLevel) -> int:
    """Return the numeric rank for *level*.

    >>> isolation_rank(IsolationLevel.CONTAINER)
    6
    """
    return ISOLATION_RANK[level]


def isolation_exceeds(candidate: IsolationLevel, baseline: IsolationLevel) -> bool:
    """True iff *candidate* provides strictly stronger isolation than *baseline*."""
    return isolation_rank(candidate) > isolation_rank(baseline)


def is_valid_isolation_level(value: object) -> bool:
    """True iff *value* is a recognised :class:`IsolationLevel` string or member."""
    if isinstance(value, IsolationLevel):
        return True
    if isinstance(value, str):
        try:
            IsolationLevel(value)
            return True
        except ValueError:
            return False
    return False


# ---------------------------------------------------------------------------
# SandboxBackend Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SandboxBackend(Protocol):
    """Interface every sandbox executor implements.

    Concrete backends: ``ProcessExecutor``, ``DockerExecutor``,
    ``KubernetesExecutor``, ``FirecrackerBackend``, ``GvisorBackend``.
    """

    name: str
    """Human-readable backend identifier (``"process"``, ``"firecracker"``, …)."""

    def __init__(self, config: SandboxConfig) -> None:
        """Initialize the backend from a sandbox configuration."""
        ...

    def available(self) -> bool:
        """True iff this backend is usable on the current host."""
        ...

    def execute(self, command: str, **kwargs: Any) -> SandboxResult:
        """Run *command* inside the sandbox and return the result."""
        ...

    def cleanup(self) -> None:
        """Release sandbox resources. Best-effort + must not raise."""
        ...


# ---------------------------------------------------------------------------
# SandboxConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SandboxConfig:
    """Immutable sandbox configuration contract.

    All sandbox executors accept this config. Each field describes a
    dimension of the sandboxing policy. The ``isolation`` field is the
    canonical isolation-strength selector; ``backend`` is a hint for the
    detection chain (``"auto"`` = best available for the chosen level).
    """

    backend: str = "auto"
    isolation: IsolationLevel = IsolationLevel.NONE
    memory_mb: int = 512
    cpu_seconds: int = 300
    timeout: int = 300
    max_output_bytes: int = 1_000_000
    max_processes: int = _DEFAULT_MAX_PROCESSES
    allow_network: bool = False
    allowed_hosts: list[str] = field(default_factory=list)
    fail_open: bool = False
    jail_dir: str = ""
    image_path: str = ""
    vsock_port: int = 0

    def to_resource_limits(self) -> ResourceLimits:
        """Construct :class:`ResourceLimits` from this config."""
        return ResourceLimits(
            memory_bytes=self.memory_mb * 1024 * 1024 if self.memory_mb > 0 else None,
            timeout_seconds=self.cpu_seconds,
            pids_limit=self.max_processes,
        )

    @classmethod
    def from_resource_limits(
        cls,
        limits: ResourceLimits,
        *,
        backend: str = "auto",
        isolation: IsolationLevel = IsolationLevel.NONE,
    ) -> SandboxConfig:
        """Construct a config from :class:`ResourceLimits`."""
        memory_mb = 512
        if limits.memory_bytes is not None and limits.memory_bytes > 0:
            memory_mb = limits.memory_bytes // (1024 * 1024)
        return cls(
            backend=backend,
            isolation=isolation,
            memory_mb=memory_mb,
            cpu_seconds=limits.timeout_seconds,
            timeout=limits.timeout_seconds,
            max_processes=(
                limits.pids_limit
                if limits.pids_limit is not None and limits.pids_limit > 0
                else _DEFAULT_MAX_PROCESSES
            ),
        )


MINIMAL_SANDBOX_CONFIG = SandboxConfig(
    backend="process",
    isolation=IsolationLevel.NONE,
    fail_open=True,
)
"""Minimal sandbox config — no isolation, fail-open. Use for development."""

STRICT_SANDBOX_CONFIG = SandboxConfig(
    backend="firecracker",
    isolation=IsolationLevel.VM_HARDWARE,
    fail_open=False,
)
"""Strict sandbox config — hardware virtualization, fail-closed. Production."""


# ---------------------------------------------------------------------------
# SandboxResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SandboxResult:
    """Immutable result of a sandboxed command execution.

    All backends normalize their results to this contract so callers do
    not need import-scoped coupling to any particular executor.
    """

    returncode: int
    stdout: str
    stderr: str
    memory_used_bytes: int = 0
    cpu_time_ms: int = 0
    pid: int = 0
    was_killed: bool = False

    @property
    def success(self) -> bool:
        """Return whether the sandboxed command exited successfully."""
        return self.returncode == 0


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------


def validate_config(config: SandboxConfig) -> list[str]:
    """Validate *config* and return a list of human-readable error messages.

    Returns an empty list when the config is valid. Does not raise.
    """
    errors: list[str] = []
    for f in fields(config):
        if f.name == "memory_mb" and isinstance(config.memory_mb, int) and config.memory_mb < 0:
            errors.append(f"memory_mb must be >= 0, got {config.memory_mb}")
        if f.name == "cpu_seconds" and isinstance(config.cpu_seconds, int) and config.cpu_seconds < 0:
            errors.append(f"cpu_seconds must be >= 0, got {config.cpu_seconds}")
        if f.name == "timeout" and isinstance(config.timeout, int) and config.timeout < 0:
            errors.append(f"timeout must be >= 0, got {config.timeout}")
        if f.name == "max_processes" and isinstance(config.max_processes, int) and config.max_processes < 0:
            errors.append(f"max_processes must be >= 0, got {config.max_processes}")
        if f.name == "max_output_bytes" and isinstance(config.max_output_bytes, int) and config.max_output_bytes < 0:
            errors.append(f"max_output_bytes must be >= 0, got {config.max_output_bytes}")
    return errors


__all__ = [
    "ISOLATION_RANK",
    "MINIMAL_SANDBOX_CONFIG",
    "STRICT_SANDBOX_CONFIG",
    "IsolationLevel",
    "SandboxBackend",
    "SandboxConfig",
    "SandboxResult",
    "is_valid_isolation_level",
    "isolation_exceeds",
    "isolation_rank",
    "validate_config",
]
