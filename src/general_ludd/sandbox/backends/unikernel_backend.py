"""Unikernel sandbox backend — bridges ``SandboxBackend`` and ``UnikernelBackend``.

A single concrete backend class that implements both the
:class:`SandboxBackend` Protocol (from ``sandbox/contracts``) and the
:class:`UnikernelBackend` Protocol (from ``security/sandboxes/vm/contracts``).

The backend accepts :class:`ImageConfig` and :class:`BootConfig` via
``configure_image`` / ``configure_boot`` and delegates execution to the
best-available VM runtime (Firecracker > gVisor > stub).

When no VM runtime is available, ``available()`` returns ``False`` and
``execute()`` falls back to a fail-open error result — same pattern as
the existing ``FirecrackerBackend`` stub in this package.
"""

from __future__ import annotations

from typing import Any

from general_ludd.sandbox.contracts import SandboxConfig, SandboxResult
from general_ludd.security.sandboxes.vm.contracts import (
    BootConfig,
    ImageConfig,
)


def _detect_vm_runtime() -> str | None:
    import shutil

    if shutil.which("firecracker") is not None:
        return "firecracker"
    if shutil.which("runsc") is not None:
        return "gvisor"
    return None


class UnikernelBackend:
    """Execute commands inside a unikernel (Firecracker microVM or gVisor).

    Implements both :class:`~general_ludd.sandbox.contracts.SandboxBackend` and
    :class:`~general_ludd.security.sandboxes.vm.contracts.UnikernelBackend`
    contracts.

    **Lifecycle**::

        backend = UnikernelBackend(config)
        backend.configure_image(ImageConfig(name="my-sandbox", ...))
        backend.configure_boot(BootConfig(vcpu_count=2, mem_size_mib=512))
        result = backend.execute("echo hello")
        backend.cleanup()
    """

    name: str = "unikernel"

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self._image: ImageConfig | None = None
        self._boot: BootConfig | None = None
        self._vm_runtime: str | None = _detect_vm_runtime()

    def available(self) -> bool:
        return self._vm_runtime is not None

    def configure_image(self, image: ImageConfig) -> None:
        self._image = image

    def configure_boot(self, boot: BootConfig) -> None:
        self._boot = boot

    def execute(
        self,
        command: str,
        *,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> SandboxResult:
        del workdir, env

        if not self.available():
            return SandboxResult(
                returncode=127,
                stdout="",
                stderr="unikernel backend not available: no VM runtime (firecracker or runsc) on PATH",
                was_killed=False,
            )

        if self._image is None or self._boot is None:
            return SandboxResult(
                returncode=127,
                stdout="",
                stderr="unikernel backend requires configure_image() and configure_boot() before execute()",
                was_killed=False,
            )

        return SandboxResult(
            returncode=127,
            stdout="",
            stderr=f"unikernel execution via {self._vm_runtime!r} not yet implemented — stub",
            was_killed=False,
        )

    def cleanup(self) -> None:
        pass


__all__ = ["UnikernelBackend"]
