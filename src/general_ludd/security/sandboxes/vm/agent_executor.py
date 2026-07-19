"""Agent command executor — host-side entry point for VM sandbox dispatch.

Phase P4: ``receive_and_execute`` now runs a real ``subprocess.run`` when given
an :class:`AgentCommand`. Without a command, the original Phase P1 stub shape
is returned for backward compatibility with the lifecycle manager's pre-P4
call sites.

The long-term vision (P5+) is that this module runs INSIDE the microVM and
receives commands over virtio-vsock. P4 keeps it on the host but plumbs the
real ``subprocess.run`` path so callers can verify the full dispatch surface
end-to-end with a real process tree, exit codes, stdout/stderr capture, and
timeouts — without depending on /dev/kvm or runsc availability.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from general_ludd.security.sandboxes import SandboxTarget

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentCommand:
    """Command payload shipped to the agent executor.

    P4 fields:
      * ``command`` — argv list passed to ``subprocess.run``.
      * ``cwd`` — working directory (passed through as ``cwd=``).
      * ``env`` — full environment mapping; replaces the inherited env.
      * ``timeout_s`` — wall-clock budget; expired → ``timed_out=True``.
    """

    command: tuple[str, ...]
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("AgentCommand requires a non-empty command argv")


def _run_subprocess(command: AgentCommand) -> dict[str, Any]:
    """Execute the command via ``subprocess.run`` and return a ProcessResult dict.

    Returns a dict with: exit_code, stdout, stderr, wall_time_s, timed_out.
    Never raises — exceptions surface as exit_code=-1 with the message in stderr.
    """
    argv = list(command.command)
    start = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=command.cwd,
            env=command.env,
            capture_output=True,
            timeout=command.timeout_s,
            check=False,
        )
        elapsed = time.monotonic() - start
        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "wall_time_s": elapsed,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            "AgentExecutor command timed out after %.2fs: %s",
            command.timeout_s,
            argv,
        )
        return {
            "exit_code": -1,
            "stdout": exc.stdout or b"",
            "stderr": exc.stderr or b"",
            "wall_time_s": elapsed,
            "timed_out": True,
        }
    except OSError as exc:
        elapsed = time.monotonic() - start
        logger.error(
            "AgentExecutor command failed to spawn (%s): %s",
            type(exc).__name__,
            argv,
        )
        return {
            "exit_code": -1,
            "stdout": b"",
            "stderr": str(exc).encode(),
            "wall_time_s": elapsed,
            "timed_out": False,
        }


class AgentExecutor:
    """Entry point that runs a SandboxTarget's command payload.

    P4 behavior:
      * ``command=None`` — returns the legacy stub dict (backward-compat for
        callers that have not been upgraded to pass an ``AgentCommand``).
      * ``command=AgentCommand(...)`` — runs ``subprocess.run`` against the
        command and returns a ProcessResult-shaped dict.
    """

    name = "agent_executor"

    @staticmethod
    def receive_and_execute(
        target: SandboxTarget,
        command: AgentCommand | None = None,
    ) -> dict[str, object]:
        if command is None:
            logger.info(
                "receive_and_execute stub for target=%s (no command supplied)",
                target,
            )
            return {
                "exit_code": 0,
                "stdout": b"",
                "stderr": b"",
                "wall_time_s": 0.0,
                "stub": True,
            }
        logger.info(
            "receive_and_execute running command=%s (target=%s, timeout=%.1fs)",
            list(command.command),
            target,
            command.timeout_s,
        )
        return _run_subprocess(command)


__all__ = ["AgentCommand", "AgentExecutor"]
