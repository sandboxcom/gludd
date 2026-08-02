"""Capability router for sandbox execution backends.

Resolves a :class:`SandboxConfig` to the correct backend instance and
routes ``execute()`` calls through it. Supports ``"auto"`` backend
selection (best available for the given isolation level) and explicit
backend selection (``"process"``, ``"container"``, ``"firecracker"``).
"""

from __future__ import annotations

import logging
from typing import ClassVar

from general_ludd.sandbox.backends.container_backend import ContainerBackend
from general_ludd.sandbox.backends.firecracker_backend import FirecrackerBackend
from general_ludd.sandbox.backends.process_backend import ProcessBackend
from general_ludd.sandbox.backends.unikernel_backend import UnikernelBackend
from general_ludd.sandbox.contracts import (
    IsolationLevel,
    SandboxBackend,
    SandboxConfig,
    SandboxResult,
)

logger = logging.getLogger(__name__)


class SandboxCapabilityRouter:
    """Routes sandbox execution requests to the correct backend.

    :param config: sandbox configuration — backend + isolation level.

    Backend resolution order for ``"auto"``:

    1. ``UnikernelBackend`` when isolation >= ``VM_HARDWARE`` and a VM runtime
       (firecracker or runsc) is present.
    2. ``ContainerBackend`` when isolation >= ``CONTAINER`` and container runtime present.
    3. ``ProcessBackend`` always (available on every host).

    Explicit backend names (``"process"``, ``"container"``, ``"firecracker"``,
    ``"unikernel"``) bypass the auto-detection chain.
    """

    _BACKENDS: ClassVar[dict[str, type[SandboxBackend]]] = {
        "process": ProcessBackend,
        "container": ContainerBackend,
        "firecracker": FirecrackerBackend,
        "unikernel": UnikernelBackend,
    }

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self._backend: SandboxBackend | None = None

    @property
    def backend(self) -> SandboxBackend:
        if self._backend is None:
            self._backend = self._resolve_backend()
        return self._backend

    @property
    def backend_name(self) -> str:
        return self.backend.name

    def _resolve_backend(self) -> SandboxBackend:
        """Resolve the config to a concrete backend instance."""
        backend_key = self.config.backend.lower()

        if backend_key == "auto":
            return self._auto_detect()

        backend_cls = self._BACKENDS.get(backend_key)
        if backend_cls is None:
            logger.warning(
                "Unknown backend %r requested, falling back to process",
                backend_key,
            )
            return ProcessBackend(self.config)

        return backend_cls(self.config)

    def _auto_detect(self) -> SandboxBackend:
        """Select the best available backend for the configured isolation level."""
        isolation = self.config.isolation

        from general_ludd.sandbox.contracts import isolation_rank

        target_rank = isolation_rank(isolation)

        vm_rank = isolation_rank(IsolationLevel.VM_HARDWARE)
        if target_rank >= vm_rank:
            uk = UnikernelBackend(self.config)
            if uk.available():
                return uk

        container_rank = isolation_rank(IsolationLevel.CONTAINER)
        if target_rank >= container_rank:
            cb = ContainerBackend(self.config)
            if cb.available():
                return cb

        return ProcessBackend(self.config)

    def execute(
        self,
        command: str,
        *,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        return self.backend.execute(command, workdir=workdir, env=env)

    def available(self) -> bool:
        return self.backend.available()

    def cleanup(self) -> None:
        if self._backend is not None:
            self._backend.cleanup()


__all__ = ["SandboxCapabilityRouter"]
