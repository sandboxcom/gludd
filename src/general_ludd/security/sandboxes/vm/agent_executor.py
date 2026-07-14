"""Main entry point for inside-VM agent execution.

Phase P1 stub. P2 will: listen on virtio-vsock for ``SandboxTarget`` messages
from the host daemon, execute the command inside the microVM, and return a
``ProcessResult`` (exit code, stdout, stderr, wall time) over the vsock
channel.
"""

from __future__ import annotations

import logging

from general_ludd.security.sandboxes import SandboxTarget

logger = logging.getLogger(__name__)


class AgentExecutor:
    """Entry point that runs inside the microVM.

    P1: container class only. P2: spawns a virtio-vsock listener, receives
    ``SandboxTarget`` payloads, executes commands via ``subprocess.run``, and
    returns ``ProcessResult`` over the same vsock channel.
    """

    name = "agent_executor"

    @staticmethod
    def receive_and_execute(target: SandboxTarget) -> dict[str, object]:
        """Receive a task and execute it inside the VM. Phase P1 stub."""
        logger.info(
            "receive_and_execute stub for target=%s (P2 will use virtio-vsock)",
            target,
        )
        return {
            "exit_code": 0,
            "stdout": b"",
            "stderr": b"",
            "wall_time_s": 0.0,
            "stub": True,
        }


__all__ = ["AgentExecutor"]
