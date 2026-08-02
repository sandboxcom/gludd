"""Unikernel sandbox contracts — ImageConfig, BootConfig, UnikernelBackend.

Formalises the configuration surface for VM-based sandbox backends (Firecracker,
gVisor). ``ImageConfig`` describes what goes into the rootfs image; ``BootConfig``
describes how to boot the VM; ``UnikernelBackend`` is the Protocol every VM-level
backend implements, extending ``SandboxBackend`` from ``sandbox/contracts`` with
image and boot configuration hooks.

See ``docs/specs/FEATURE_UNIKERNEL_SANDBOX.md`` for the full design.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from general_ludd.sandbox.contracts import (
    IsolationLevel,
    SandboxConfig,
    SandboxResult,
)
from general_ludd.security.sandboxes.vm.image_builder import (
    CACHE_DIR,
    ImageManifest,
)

_VALID_ARCHITECTURES = frozenset({"x86_64", "aarch64"})
_VALID_IMAGE_TYPES = frozenset({"firecracker", "gvisor"})


# ---------------------------------------------------------------------------
# ImageConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageConfig:
    """Immutable configuration for a VM rootfs image.

    Describes what goes into the rootfs: packages, architecture, kernel version,
    custom files, and image type. Serializable to/from :class:`ImageManifest`
    for the image builder cache layer.

    ``extra`` is excluded from the content hash (cache-key stable) and from
    equality comparisons — it carries metadata that does not affect the image
    contents.
    """

    name: str
    packages: tuple[str, ...] = ()
    architecture: str = "x86_64"
    kernel_version: str = "5.10"
    custom_files: tuple[tuple[str, bytes], ...] = ()
    image_type: str = "firecracker"
    kernel_path: str = "/var/lib/gludd/vmlinux"
    rootfs_path: str = "/var/lib/gludd/rootfs.ext4"
    extra: dict[str, object] = field(default_factory=dict, compare=False, hash=False)

    @staticmethod
    def _sha256_hex(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def content_hash(self) -> str:
        """Stable SHA-256 hex digest of the image contents (excludes *extra*)."""
        return self._sha256_hex(self._hash_payload())

    def cache_path(self) -> Path:
        return CACHE_DIR / self.content_hash()

    def to_manifest(self) -> ImageManifest:
        return ImageManifest(
            name=self.name,
            packages=self.packages,
            architecture=self.architecture,
            kernel_version=self.kernel_version,
            custom_files=self.custom_files,
            extra=dict(self.extra),
        )

    @classmethod
    def from_manifest(
        cls,
        manifest: ImageManifest,
        kernel_path: str = "/var/lib/gludd/vmlinux",
        rootfs_path: str = "/var/lib/gludd/rootfs.ext4",
        image_type: str = "firecracker",
    ) -> ImageConfig:
        return cls(
            name=manifest.name,
            packages=manifest.packages,
            architecture=manifest.architecture,
            kernel_version=manifest.kernel_version,
            custom_files=manifest.custom_files,
            image_type=image_type,
            kernel_path=kernel_path,
            rootfs_path=rootfs_path,
            extra=dict(manifest.extra),
        )

    def _hash_payload(self) -> bytes:
        return json.dumps(
            {
                "name": self.name,
                "packages": sorted(self.packages),
                "architecture": self.architecture,
                "kernel_version": self.kernel_version,
                "custom_files": sorted((p, self._sha256_hex(c)) for p, c in self.custom_files),
            },
            sort_keys=True,
        ).encode()


def validate_image_config(config: ImageConfig) -> list[str]:
    errors: list[str] = []
    if not config.name:
        errors.append("name must be non-empty")
    if config.architecture not in _VALID_ARCHITECTURES:
        errors.append(f"architecture {config.architecture!r} not in {sorted(_VALID_ARCHITECTURES)}")
    if config.image_type not in _VALID_IMAGE_TYPES:
        errors.append(f"image_type {config.image_type!r} not in {sorted(_VALID_IMAGE_TYPES)}")
    return errors


# ---------------------------------------------------------------------------
# BootConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootConfig:
    """Immutable VM boot-time parameters.

    Describes how to boot a VM: vCPU count, memory, kernel boot args, guest CID,
    timeout, and feature flags (network, vsock, read-only rootfs).

    Provides typed helpers that produce the REST bodies Firecracker expects
    (``PUT /machine-config``, ``PUT /boot-source``, ``PUT /drives/<id>``,
    ``PUT /vsock``) so callers never construct ad-hoc dicts.
    """

    vcpu_count: int = 1
    mem_size_mib: int = 128
    boot_args: str = "console=ttyS0 reboot=k panic=1 pci=off"
    guest_cid: int = 3
    timeout_seconds: int = 30
    network_enabled: bool = False
    vsock_enabled: bool = True
    read_only_rootfs: bool = False

    def to_firecracker_machine_config(self) -> dict[str, int]:
        return {"vcpu_count": self.vcpu_count, "mem_size_mib": self.mem_size_mib}

    def to_firecracker_boot_source(self, kernel_path: str) -> dict[str, str]:
        return {
            "kernel_image_path": kernel_path,
            "boot_args": self.boot_args,
        }

    def to_firecracker_drive_config(self, rootfs_path: str) -> dict[str, object]:
        return {
            "drive_id": "rootfs",
            "path_on_host": rootfs_path,
            "is_root_device": True,
            "is_read_only": self.read_only_rootfs,
        }

    def to_firecracker_vsock_config(self, uds_path: str) -> dict[str, object] | None:
        if not self.vsock_enabled:
            return None
        return {"guest_cid": self.guest_cid, "uds_path": uds_path}

    def to_sandbox_config(self) -> SandboxConfig:
        return SandboxConfig(
            backend="firecracker",
            isolation=IsolationLevel.VM_HARDWARE,
            memory_mb=self.mem_size_mib,
            timeout=self.timeout_seconds,
            cpu_seconds=self.timeout_seconds,
        )


def validate_boot_config(config: BootConfig) -> list[str]:
    errors: list[str] = []
    if config.vcpu_count < 1:
        errors.append(f"vcpu_count must be >= 1, got {config.vcpu_count}")
    if config.mem_size_mib < 1:
        errors.append(f"mem_size_mib must be >= 1, got {config.mem_size_mib}")
    if config.timeout_seconds < 0:
        errors.append(f"timeout_seconds must be >= 0, got {config.timeout_seconds}")
    return errors


# ---------------------------------------------------------------------------
# UnikernelBackend Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class UnikernelBackend(Protocol):
    """Interface every VM-level sandbox backend implements.

    Extends :class:`~general_ludd.sandbox.contracts.SandboxBackend` (which
    provides ``name``, ``available``, ``execute``, and ``cleanup``) with two
    additional hooks for VM-specific configuration:

    * ``configure_image`` — accept an :class:`ImageConfig` describing the rootfs
    * ``configure_boot`` — accept a :class:`BootConfig` describing VM boot params

    Concrete backends: ``FirecrackerBackend``, ``GvisorBackend``.
    """

    name: str

    def __init__(self, config: SandboxConfig) -> None: ...

    def available(self) -> bool: ...

    def configure_image(self, image: ImageConfig) -> None: ...

    def configure_boot(self, boot: BootConfig) -> None: ...

    def execute(self, command: str, **kwargs: Any) -> SandboxResult: ...

    def cleanup(self) -> None: ...


# ---------------------------------------------------------------------------
# Preset configurations
# ---------------------------------------------------------------------------


DEFAULT_IMAGE_CONFIG = ImageConfig(
    name="gludd-sandbox-default",
    packages=("python3", "ansible", "git"),
    architecture="x86_64",
    image_type="firecracker",
)
"""Default rootfs image configuration — Alpine x86_64 with Python, Ansible, Git."""

DEFAULT_BOOT_CONFIG = BootConfig()
"""Default VM boot parameters — 1 vCPU, 128 MiB, standard boot args, vsock on."""


__all__ = [
    "DEFAULT_BOOT_CONFIG",
    "DEFAULT_IMAGE_CONFIG",
    "BootConfig",
    "ImageConfig",
    "UnikernelBackend",
    "validate_boot_config",
    "validate_image_config",
]
