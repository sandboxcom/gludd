"""Firecracker microVM sandbox backend.

Phase P1 stub — ``apply`` / ``verify`` / ``release`` return placeholder handles
so the auto-detection chain can resolve. Real boot/kill via the Firecracker
REST API (``/machine-config``, ``/boot-source``, ``/drives``, ``/vsock``) and
virtio-vsock dispatch land in P2.

Requires: ``/dev/kvm`` readable + ``firecracker`` binary on PATH.
Overhead: <5 MiB memory, <125 ms cold boot.
"""

from __future__ import annotations

import logging
import os
import shutil

from general_ludd.security.sandboxes import (
    Finding,
    PermissionSpec,
    SandboxHandle,
    SandboxTarget,
)

logger = logging.getLogger(__name__)


class FirecrackerBackend:
    name = "firecracker"

    @staticmethod
    def available() -> bool:
        kvm_ok = os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)
        fc_ok = shutil.which("firecracker") is not None
        if not kvm_ok:
            logger.debug("Firecracker unavailable: /dev/kvm absent or not readable")
        if not fc_ok:
            logger.debug("Firecracker unavailable: firecracker binary not on PATH")
        return kvm_ok and fc_ok

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        token = f"gludd-{spec.agent_type}"
        if not FirecrackerBackend.available():
            logger.warning(
                "Firecracker apply skipped: /dev/kvm or firecracker binary "
                "absent — UNSANDBOXED"
            )
            return SandboxHandle(
                backend="firecracker", token=token, applied=False,
                extra={"reason": "firecracker or /dev/kvm absent"},
            )
        logger.info(
            "FirecrackerBackend.apply stub for %s — real boot/kill in P2", token,
        )
        return SandboxHandle(
            backend="firecracker", token=token, applied=True,
            extra={"stub": True},
        )

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        findings: list[Finding] = []
        if not handle.applied:
            findings.append(Finding(
                severity="fail",
                message=(
                    f"Firecracker handle not applied (reason="
                    f"{handle.extra.get('reason', 'unknown')})"
                ),
                capability=None,
            ))
            return findings
        if handle.extra.get("stub"):
            findings.append(Finding(
                severity="warn",
                message="Firecracker verify is a Phase P1 stub — no microVM state inspected",
                capability=None,
            ))
        return findings

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        if handle.extra.get("stub"):
            logger.debug(
                "FirecrackerBackend.release stub for %s — real shutdown in P2",
                handle.token,
            )


__all__ = ["FirecrackerBackend"]
