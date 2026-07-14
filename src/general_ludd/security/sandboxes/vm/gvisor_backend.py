"""gVisor application-kernel sandbox backend.

Phase P1 stub — ``apply`` / ``verify`` / ``release`` return placeholder handles
so the auto-detection chain can resolve. Real ``runsc run`` with an OCI bundle
lands in P2-P3.

Requires: ``runsc`` binary on PATH (gVisor release, Docker-managed, or
containerd-shim-runsc-v1).
"""

from __future__ import annotations

import logging
import shutil

from general_ludd.security.sandboxes import (
    Finding,
    PermissionSpec,
    SandboxHandle,
    SandboxTarget,
)

logger = logging.getLogger(__name__)


class GvisorBackend:
    name = "gvisor"

    @staticmethod
    def available() -> bool:
        ok = shutil.which("runsc") is not None
        if not ok:
            logger.debug("gVisor unavailable: runsc binary not on PATH")
        return ok

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        token = f"gludd-{spec.agent_type}"
        if not GvisorBackend.available():
            logger.warning(
                "gVisor apply skipped: runsc binary absent — UNSANDBOXED"
            )
            return SandboxHandle(
                backend="gvisor", token=token, applied=False,
                extra={"reason": "runsc binary absent"},
            )
        logger.info(
            "GvisorBackend.apply stub for %s — real runsc run in P2", token,
        )
        return SandboxHandle(
            backend="gvisor", token=token, applied=True,
            extra={"stub": True},
        )

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        findings: list[Finding] = []
        if not handle.applied:
            findings.append(Finding(
                severity="fail",
                message=(
                    f"gVisor handle not applied (reason="
                    f"{handle.extra.get('reason', 'unknown')})"
                ),
                capability=None,
            ))
            return findings
        if handle.extra.get("stub"):
            findings.append(Finding(
                severity="warn",
                message="gVisor verify is a Phase P1 stub — no runsc state inspected",
                capability=None,
            ))
        return findings

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        if handle.extra.get("stub"):
            logger.debug(
                "GvisorBackend.release stub for %s — real runsc delete in P2",
                handle.token,
            )


__all__ = ["GvisorBackend"]
