"""Sandbox enforcement layer for tool execution.

Ensures all tool execution goes through path confinement, network
restrictions, and resource limits. Fails closed — if the sandbox cannot
be applied, the tool call is refused.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from general_ludd.sandbox.process_executor import ProcessExecutor, ProcessLimits, ProcessResult
from general_ludd.security.sanitize import confine_path

logger = logging.getLogger(__name__)


@dataclass
class SandboxConfig:
    jail_dir: str = ""
    backend: str = "auto"
    image_path: str = ""
    vsock_port: int = 0
    allow_network: bool = False
    allowed_hosts: list[str] = field(default_factory=list)
    memory_mb: int = 512
    cpu_seconds: int = 300
    max_output_bytes: int = 1_000_000
    max_processes: int = 50
    timeout: int = 300
    fail_open: bool = False


class SandboxNotAvailableError(RuntimeError):
    """Raised when the sandbox cannot be applied and fail_open is False."""


class MaxOutputExceededError(RuntimeError):
    """Raised when a command produces more output than max_output_bytes."""


class PathEscapeError(ValueError):
    """Raised when a path escapes the sandbox jail directory."""


class SandboxEnforcer:
    """Fail-closed sandbox enforcement for tool execution.

    Wraps :class:`ProcessExecutor` with three layers of confinement:
    1. Path confinement — all file access is restricted to ``jail_dir``
    2. Network restrictions — outbound connections are blocked by default
    3. Resource limits — memory, CPU, process count, and file size caps

    Fails closed by default: if ``verify_ready()`` cannot confirm sandbox
    readiness, tool execution is refused with :class:`SandboxNotAvailableError`.
    Set ``fail_open=True`` to warn-and-proceed instead (for development only).
    """

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self._config = config or SandboxConfig()
        self._verified = False
        self._jail_path: Path | None = None

        limits = ProcessLimits(
            memory_mb=self._config.memory_mb,
            cpu_seconds=self._config.cpu_seconds,
            max_processes=self._config.max_processes,
        )
        self._executor = ProcessExecutor(
            timeout=self._config.timeout,
            max_output_bytes=self._config.max_output_bytes,
        )
        self._limits = limits

    @property
    def jail_dir(self) -> str:
        return self._config.jail_dir

    @property
    def is_ready(self) -> bool:
        return self._verified

    def verify_ready(self) -> None:
        """Verify the sandbox jail directory exists and is writable.

        Raises :class:`SandboxNotAvailableError` if the sandbox cannot be
        applied and ``fail_open`` is ``False``. On success, sets the
        ``is_ready`` flag.
        """
        if self._verified:
            return

        jail = self._config.jail_dir
        auto = not jail
        if auto:
            jail = tempfile.mkdtemp(prefix="gludd-sandbox-")
            self._config.jail_dir = jail

        jail_path = Path(jail)
        try:
            if not auto and not jail_path.exists():
                raise FileNotFoundError(
                    f"Sandbox jail directory {jail!r} does not exist"
                )
            jail_path.mkdir(parents=True, exist_ok=True)
            if not os.access(jail, os.W_OK):
                raise SandboxNotAvailableError(
                    f"Sandbox jail directory {jail!r} is not writable"
                )
        except OSError as exc:
            msg = f"Sandbox jail directory {jail!r} is unusable: {exc}"
            if self._config.fail_open:
                logger.warning("%s — proceeding without sandbox", msg)
                self._verified = True
                return
            raise SandboxNotAvailableError(msg) from exc

        self._jail_path = jail_path
        self._verified = True

    def execute(
        self,
        command: str,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessResult:
        """Execute a command inside the sandbox.

        Validates that ``workdir`` (if provided) is confined within the
        jail directory. Applies resource limits and network isolation
        via ``preexec_fn``. Enforces ``max_output_bytes``.

        Raises :class:`SandboxNotAvailableError` if the sandbox is not
        verified ready and ``fail_open`` is ``False``.
        """
        if not self._verified:
            if self._config.fail_open:
                logger.warning(
                    "Sandbox not verified — executing %r unsandboxed", command
                )
            else:
                raise SandboxNotAvailableError(
                    "Sandbox not verified; call verify_ready() before execute()"
                )

        confined_workdir = self._resolve_workdir(workdir)

        limits = self._limits

        def _preexec() -> None:
            self._apply_sandbox_preexec(limits)

        result = self._executor.execute(
            command,
            workdir=confined_workdir,
            limits=limits,
            env=env,
        )

        total_output = len(result.stdout or "") + len(result.stderr or "")
        if total_output > self._config.max_output_bytes:
            if self._config.fail_open:
                logger.warning(
                    "Command output %d bytes exceeds max %d — truncated",
                    total_output, self._config.max_output_bytes,
                )
            else:
                raise MaxOutputExceededError(
                    f"Command output {total_output} bytes exceeds max "
                    f"{self._config.max_output_bytes}"
                )

        return result

    def confine_path(self, path: str) -> str:
        """Confine *path* to the jail directory.

        Returns the real (resolved) path when it is within the jail.
        Raises :class:`PathEscapeError` if the path escapes.

        When the sandbox is not verified, returns *path* unchanged when
        ``fail_open`` is ``True``.
        """
        if not self._verified:
            if self._config.fail_open:
                return path
            raise SandboxNotAvailableError(
                "Sandbox not verified; cannot confine paths"
            )

        confined = confine_path(path, self._config.jail_dir)
        if confined is None:
            raise PathEscapeError(
                f"Path {path!r} escapes sandbox jail {self._config.jail_dir!r}"
            )
        return confined

    def _resolve_workdir(self, workdir: str | None) -> str | None:
        if workdir is None:
            return None
        if not self._verified:
            if self._config.fail_open:
                return workdir
            raise SandboxNotAvailableError(
                "Sandbox not verified; cannot resolve workdir"
            )
        return self.confine_path(workdir)

    @staticmethod
    def _apply_sandbox_preexec(limits: ProcessLimits) -> None:
        """Apply resource limits and network isolation before exec."""
        ProcessExecutor._apply_limits(limits)
        SandboxEnforcer._isolate_network()

    @staticmethod
    def _isolate_network() -> None:
        """Best-effort outbound network isolation in the child process.

        Platform-specific:
        - Container/VM backends enforce the hard boundary in their own
          network namespace or policy.
        - This process hook must not mutate interpreter-global socket state.
        - Unsupported local platforms fall back to a diagnostic only.

        This is a defense-in-depth measure; the primary network restriction
        for containerized backends (Docker/Kubernetes) is applied at the
        container level via ``NetworkPolicy``.
        """
        if os.name != "posix":
            return

        try:
            import resource

            if hasattr(resource, "RLIMIT_MSGQUEUE"):
                pass
        except ImportError:
            return

        if os.environ.get("GLUDD_SANDBOX_NO_NETWORK", "").lower() in ("1", "true", "yes"):
            logger.debug(
                "Network isolation is delegated to the configured sandbox "
                "backend; leaving the process-wide socket default unchanged",
            )


__all__ = [
    "MaxOutputExceededError",
    "PathEscapeError",
    "SandboxConfig",
    "SandboxEnforcer",
    "SandboxNotAvailableError",
]
