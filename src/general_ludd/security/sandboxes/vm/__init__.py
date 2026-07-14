"""VM-level sandbox backends — Firecracker microVMs and gVisor application kernels.

Phase P1 stubs: ``apply`` / ``verify`` / ``release`` return placeholder
:class:`SandboxHandle` objects so the detection chain can resolve. Real
boot/kill cycles, virtio-vsock dispatch, and image building arrive in P2-P3.

See ``docs/specs/FEATURE_UNIKERNEL_SANDBOX.md`` for the full design.
"""

from __future__ import annotations

from general_ludd.security.sandboxes.vm.agent_executor import AgentExecutor
from general_ludd.security.sandboxes.vm.firecracker_backend import FirecrackerBackend
from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend
from general_ludd.security.sandboxes.vm.image_builder import build_rootfs, verify_image

__all__ = [
    "AgentExecutor",
    "FirecrackerBackend",
    "GvisorBackend",
    "build_rootfs",
    "verify_image",
]
