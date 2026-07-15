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
from general_ludd.security.sandboxes.vm.image_builder import (
    BuiltImage,
    ImageManifest,
    build_firecracker_image,
    build_gvisor_image,
    build_rootfs,
    cleanup_cache,
    get_image_path,
    image_exists,
    list_cached_images,
    verify_image,
)

__all__ = [
    "AgentExecutor",
    "BuiltImage",
    "FirecrackerBackend",
    "GvisorBackend",
    "ImageManifest",
    "build_firecracker_image",
    "build_gvisor_image",
    "build_rootfs",
    "cleanup_cache",
    "get_image_path",
    "image_exists",
    "list_cached_images",
    "verify_image",
]
