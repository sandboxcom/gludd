"""Detect the local QEMU environment — platform, arch, binary, acceleration."""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class QemuConfig:
    """Describe the host capabilities used to select a QEMU invocation."""

    platform: Literal["darwin", "linux", "unknown"]
    arch: Literal["arm64", "amd64", "unknown"]
    binary_path: str | None
    acceleration: Literal["hvf", "kvm", "none"]


def _detect_platform() -> Literal["darwin", "linux", "unknown"]:
    system = platform.system()
    if system == "Darwin":
        return "darwin"
    if system == "Linux":
        return "linux"
    return "unknown"


def _detect_arch() -> Literal["arm64", "amd64", "unknown"]:
    machine = platform.machine()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "amd64"
    return "unknown"


def _find_qemu_binary(arch: Literal["arm64", "amd64", "unknown"]) -> str | None:
    if arch == "arm64":
        return shutil.which("qemu-system-aarch64")
    if arch == "amd64":
        return shutil.which("qemu-system-x86_64")
    return None


def _detect_acceleration(plat: Literal["darwin", "linux", "unknown"]) -> Literal["hvf", "kvm", "none"]:
    if plat == "darwin":
        return "hvf"
    if plat == "linux" and shutil.which("kvm-ok") is not None:
        return "kvm"
    return "none"


def detect() -> QemuConfig:
    """Detect QEMU availability and acceleration with fail-closed defaults."""
    plat = _detect_platform()
    arch = _detect_arch()
    binary_path = _find_qemu_binary(arch)
    acceleration = _detect_acceleration(plat)
    return QemuConfig(
        platform=plat,
        arch=arch,
        binary_path=binary_path,
        acceleration=acceleration,
    )


__all__ = ["QemuConfig", "detect"]
