"""macOS sandbox-exec (Seatbelt / ``sandbox.d``) backend.

Writes a Seatbelt profile with ``(allow file-read* (subpath ...))`` for file
caps and ``(allow network-outbound (to ...))`` for net caps, then launches the
target under ``sandbox-exec -p <profile>``. ``verify`` runs ``sandbox-exec -p
<profile> -n`` (dry-run) to confirm the profile compiles.

Seatbelt is DEPRECATED. Apple removed ``sandbox-exec`` from shipping macOS in
15.4+; on those hosts :func:`available` returns False and the auto-detector
warns loudly. There is no supported replacement for arbitrary sandbox
profiles; deployments on 15.4+ MUST use a VM/container for isolation.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from general_ludd.security.sandboxes import (
    Capability,
    Finding,
    PermissionSpec,
    SandboxHandle,
    SandboxTarget,
)

logger = logging.getLogger(__name__)

PROFILE_DIR = Path("/tmp/gludd-seatbelt")


def _file_clause(cap: Capability) -> str:
    prefix = cap.constraint_value("path_prefix")
    path = prefix if isinstance(prefix, str) else "/tmp/gludd/"
    verbs = []
    if "read" in cap.actions:
        verbs.append("file-read*")
    if "write" in cap.actions:
        verbs.append("file-write*")
    if not verbs:
        verbs.append("file-read*")
    clauses = [f'(allow {v} (subpath "{path}"))' for v in verbs]
    return "\n  ".join(clauses)


def _net_clause(cap: Capability) -> str:
    host = cap.constraint_value("host")
    port = cap.constraint_value("port")
    if isinstance(host, str):
        port_s = str(port) if isinstance(port, int) else "443"
        return f'(allow network-outbound (to (remote tcp "{host}:{port_s}")))'
    return "(allow network-outbound)"


def render_profile(spec: PermissionSpec) -> str:
    """Render a Seatbelt ``sandbox.d`` profile text for ``spec``."""
    allow_clauses: list[str] = []
    for cap in spec.capabilities:
        if cap.resource == "fs":
            allow_clauses.append(_file_clause(cap))
        elif cap.resource == "net":
            allow_clauses.append(_net_clause(cap))
    deny_clauses: list[str] = []
    for cap in spec.denied:
        if cap.resource == "fs":
            deny_clauses.append(f'(deny file-read* (subpath "{cap.constraint_value("path_prefix") or "/"}"))')
        elif cap.resource == "net":
            deny_clauses.append("(deny network-outbound)")
    body = "\n  ".join(allow_clauses + deny_clauses) or "  ;; empty spec — default deny"
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow process-fork)\n"
        "(allow signal (target self))\n"
        f"{body}\n"
    )


def _profile_path(spec: PermissionSpec) -> Path:
    return PROFILE_DIR / f"gludd-{spec.agent_id}.sb"


class SeatbeltBackend:
    name = "seatbelt"

    @staticmethod
    def available() -> bool:
        import shutil
        if shutil.which("sandbox-exec") is None:
            import platform
            logger.warning(
                "sandbox-exec missing — likely macOS %s (removed in 15.4+)",
                platform.mac_ver()[0],
            )
            return False
        return True

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        profile_name = f"gludd-{spec.agent_id}"
        try:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            path = _profile_path(spec)
            path.write_text(render_profile(spec))
            # Dry-run compile check first — fail-open with applied=False if it
            # does not compile.
            rc = subprocess.run(
                ["sandbox-exec", "-p", str(path), "-n", "/bin/true"],
                check=False, capture_output=True, timeout=10,
            ).returncode
            if rc != 0:
                logger.error(
                    "sandbox-exec profile %s failed to compile (rc=%d) — UNSANDBOXED",
                    profile_name, rc,
                )
                return SandboxHandle(
                    backend="seatbelt", token=profile_name, applied=False,
                    extra={"path": str(path), "compile_rc": rc},
                )
            # If the caller passed a popen / pid, that is the target; otherwise
            # the profile is staged for a caller-driven sandbox-exec launch.
            logger.info("Seatbelt profile %s compiled (dry-run)", profile_name)
            return SandboxHandle(
                backend="seatbelt", token=profile_name, applied=True,
                extra={"path": str(path)},
            )
        except Exception as exc:
            logger.error(
                "seatbelt apply failed for %s — dispatching UNSANDBOXED: %s",
                profile_name, exc, exc_info=True,
            )
            return SandboxHandle(
                backend="seatbelt", token=profile_name, applied=False,
                extra={"error": str(exc)},
            )

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        path = Path(handle.extra.get("path", str(_profile_path(spec))))
        findings: list[Finding] = []
        if not path.exists():
            findings.append(Finding(
                severity="fail",
                message=f"profile file missing: {path}",
                capability=None,
            ))
            return findings
        try:
            rc = subprocess.run(
                ["sandbox-exec", "-f", str(path), "/bin/true"],
                check=False, capture_output=True, timeout=10,
            ).returncode
        except Exception as exc:
            return [Finding(
                severity="fail", message=f"sandbox-exec -n failed: {exc}", capability=None,
            )]
        if rc == 0:
            findings.append(Finding(
                severity="ok", message="profile compiles", capability=None,
            ))
        else:
            findings.append(Finding(
                severity="fail",
                message=f"profile does not compile (rc={rc})",
                capability=None,
            ))
        if not handle.applied:
            findings.append(Finding(
                severity="warn",
                message="apply() reported applied=False — spec is advisory only",
            ))
        return findings

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        path = Path(handle.extra.get("path", ""))
        if path.exists():
            try:
                path.unlink()
            except Exception as exc:
                logger.warning("seatbelt release of %s failed: %s", handle.token, exc)
