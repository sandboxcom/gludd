"""gVisor application-kernel sandbox backend.

Phase P4: ``apply`` now spawns a real ``runsc run`` subprocess against an OCI
bundle (built via :mod:`image_builder`). The handle's ``extra`` carries the
``popen``, ``pid``, ``sandbox_id``, and ``bundle_path`` so ``verify`` can poll
liveness and ``release`` can terminate cleanly.

When ``runsc`` is absent the backend fails open exactly as before — the P1
fallback path is preserved so the auto-detection chain still resolves.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import (
    Finding,
    SandboxHandle,
    SandboxTarget,
)
from general_ludd.security.sandboxes.vm.image_builder import (
    ImageManifest,
    build_gvisor_image,
)

logger = logging.getLogger(__name__)


_RUNSC_TERMINATE_GRACE_S = 2.0


def _spawn_runsc(
    spec: PermissionSpec,
    target: SandboxTarget,
) -> SandboxHandle:
    """Build an OCI bundle and spawn ``runsc run`` in a fresh sandbox id.

    Returns a handle whose ``extra`` carries ``popen``, ``pid``,
    ``sandbox_id``, and ``bundle_path``. On spawn failure the handle
    fails open with the error in ``extra['reason']``.
    """
    token = f"gludd-{spec.agent_type}"
    sandbox_id = f"gludd-sb-{uuid.uuid4().hex[:12]}"

    try:
        manifest = ImageManifest(
            name=sandbox_id,
            packages=("python3",),
            custom_files=(
                (
                    "usr/bin/agent_executor",
                    b"#!/usr/bin/env python3\n# gVisor sandbox entrypoint\n",
                ),
            ),
        )
        bundle = build_gvisor_image(manifest)
        bundle_path = bundle.path
    except Exception as exc:
        logger.error(
            "GvisorBackend.apply: OCI bundle build failed for %s — %s",
            token,
            exc,
        )
        return SandboxHandle(
            backend="gvisor",
            token=token,
            applied=False,
            extra={"reason": f"image build failed: {exc}"},
        )

    bundle_root = str(bundle_path)
    try:
        popen = subprocess.Popen(
            [
                "runsc",
                "--root=/tmp/gludd-runsc",
                "run",
                "--bundle=" + bundle_root,
                sandbox_id,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.error(
            "GvisorBackend.apply: runsc spawn failed for %s — %s",
            token,
            exc,
        )
        return SandboxHandle(
            backend="gvisor",
            token=token,
            applied=False,
            extra={"reason": f"runsc spawn failed: {exc}"},
        )

    logger.info(
        "GvisorBackend.apply spawned runsc pid=%d sandbox=%s bundle=%s",
        popen.pid,
        sandbox_id,
        bundle_root,
    )
    return SandboxHandle(
        backend="gvisor",
        token=token,
        applied=True,
        extra={
            "popen": popen,
            "pid": popen.pid,
            "sandbox_id": sandbox_id,
            "bundle_path": bundle_root,
            "started_at": time.time(),
        },
    )


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
                backend="gvisor",
                token=token,
                applied=False,
                extra={"reason": "runsc binary absent"},
            )
        return _spawn_runsc(spec, target)

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        findings: list[Finding] = []
        if not handle.applied:
            findings.append(
                Finding(
                    severity="fail",
                    message=(
                        f"gVisor handle not applied (reason="
                        f"{handle.extra.get('reason', 'unknown')})"
                    ),
                    capability=None,
                )
            )
            return findings

        popen = handle.extra.get("popen")
        if not isinstance(popen, subprocess.Popen):
            findings.append(
                Finding(
                    severity="fail",
                    message=(
                        "gVisor handle has no live popen — sandbox process "
                        "tracking lost (legacy stub handle?)"
                    ),
                    capability=None,
                )
            )
            return findings

        returncode: int | None = None
        try:
            returncode = popen.poll()
        except Exception as exc:
            findings.append(
                Finding(
                    severity="warn",
                    message=f"gVisor popen poll raised {type(exc).__name__}: {exc}",
                    capability=None,
                )
            )
            return findings

        pid = handle.extra.get("pid")
        sandbox_id = handle.extra.get("sandbox_id")
        if returncode is None:
            findings.append(
                Finding(
                    severity="ok",
                    message=(
                        f"gVisor sandbox {sandbox_id} alive (pid={pid})"
                    ),
                    capability=None,
                )
            )
        else:
            findings.append(
                Finding(
                    severity="fail",
                    message=(
                        f"gVisor sandbox {sandbox_id} dead "
                        f"(pid={pid}, returncode={returncode})"
                    ),
                    capability=None,
                )
            )
        return findings

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        popen = handle.extra.get("popen")
        if not isinstance(popen, subprocess.Popen):
            logger.debug(
                "GvisorBackend.release: no popen on handle %s — no-op",
                handle.token,
            )
            return

        try:
            if popen.poll() is not None:
                logger.debug(
                    "GvisorBackend.release: pid=%d already dead — no-op",
                    handle.extra.get("pid"),
                )
                return
        except Exception:
            pass

        try:
            popen.terminate()
        except Exception as exc:
            logger.warning(
                "GvisorBackend.release: terminate raised %s for pid=%s",
                type(exc).__name__,
                handle.extra.get("pid"),
            )
            return

        try:
            popen.wait(timeout=_RUNSC_TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            logger.warning(
                "GvisorBackend.release: pid=%s did not exit in %.1fs — killing",
                handle.extra.get("pid"),
                _RUNSC_TERMINATE_GRACE_S,
            )
            try:
                popen.kill()
            except Exception as exc:
                logger.warning(
                    "GvisorBackend.release: kill raised %s for pid=%s",
                    type(exc).__name__,
                    handle.extra.get("pid"),
                )


__all__ = ["GvisorBackend"]
