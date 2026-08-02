"""Firecracker microVM sandbox backend stub implementing ``SandboxBackend``.

This backend requires KVM and the Firecracker binary to be available on the
host. When unavailable, ``available()`` returns ``False`` and ``execute()``
falls back to a fail-open error result.
"""

from __future__ import annotations

import shutil
from typing import Any

from general_ludd.sandbox.contracts import SandboxConfig, SandboxResult


class FirecrackerBackend:
    """Execute commands inside a Firecracker microVM.

    Implements the :class:`SandboxBackend` Protocol. Provides
    ``IsolationLevel.VM_HARDWARE`` isolation strength when KVM is available.
    This is a stub implementation — the full Firecracker lifecycle
    (image build, socket handshake, vsock communication) lives in
    ``general_ludd.security.sandboxes.vm.firecracker_backend``.
    """

    name: str = "firecracker"

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    def available(self) -> bool:
        return shutil.which("firecracker") is not None and self._kvm_available()

    @staticmethod
    def _kvm_available() -> bool:
        import platform

        if platform.system() != "Linux":
            return False
        import os

        return os.path.exists("/dev/kvm")

    def execute(
        self,
        command: str,
        *,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> SandboxResult:
        if not self.available():
            return SandboxResult(
                returncode=127,
                stdout="",
                stderr="firecracker backend not available: firecracker binary or /dev/kvm missing",
                was_killed=False,
            )

        return SandboxResult(
            returncode=127,
            stdout="",
            stderr="firecracker microVM execution not yet implemented — stub",
            was_killed=False,
        )

    def cleanup(self) -> None:
        pass
