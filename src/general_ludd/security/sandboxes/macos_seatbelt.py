"""macOS sandbox-exec (Seatbelt / ``sandbox.d``) backend.

Writes a Seatbelt profile with ``(allow file-read* (subpath ...))`` for file
caps and ``(allow network-outbound (to ...))`` for net caps, then dry-runs
the target under ``sandbox-exec -f <profile>``. ``verify`` re-runs the
dry-run to confirm the profile still compiles.

DEPRECATED — Apple removed ``sandbox-exec`` from shipping macOS in 15.4+.
On those hosts :func:`available` returns False and :func:`apply` returns a
handle with ``applied=False`` and a LOUD log warning. There is NO supported
replacement for arbitrary sandbox profiles; the only third-party option is
the Endpoint Security entitlement, which Apple gates to AV/EDR vendors.

Migration path
--------------

Operators on macOS 15.4+ who need to run untrusted agent work MUST run it in
a Linux VM. Supported paths:

  * **Tart** (https://github.com/cirruslabs/tart) — Apple Silicon native,
    uses the Virtualization.framework; fastest cold-start. Launch a Linux
    guest and let the agent dispatch into the Linux-side daemon where
    Landlock + bubblewrap enforce the spec.
  * **Lima** (https://github.com/lima-vm/lima) — cross-arch (qemu or vz),
    good for Intel Macs; same Linux-side enforcement model.
  * **UTM** — GUI-first alternative.

The macOS host daemon in this configuration becomes a thin scheduler that
SSHes agent work into the Linux guest; it must NOT run agent subprocesses
directly because there is no enforcement layer left on the host.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from pathlib import Path

from general_ludd.security.sandboxes import (
    Capability,
    Finding,
    PermissionSpec,
    SandboxHandle,
    SandboxTarget,
    allowed_hosts,
    allowed_ports,
    path_prefix,
)

logger = logging.getLogger(__name__)

PROFILE_DIR = Path("/tmp/gludd-seatbelt")

# macOS version where sandbox-exec was removed. 15.4 → (15, 4, 0).
DEPRECATED_SINCE = (15, 4, 0)


def _macos_version_tuple() -> tuple[int, ...]:
    """Return the macOS version as an int tuple (major, minor, patch)."""
    try:
        raw = platform.mac_ver()[0]
        if not raw:
            return ()
        return tuple(int(p) for p in raw.split(".") if p.isdigit())
    except Exception:
        return ()


def _is_deprecated_host() -> bool:
    """True iff the host is macOS 15.4+ (sandbox-exec removed)."""
    ver = _macos_version_tuple()
    if not ver:
        return False
    # Pad to 3-tuple for safe comparison.
    padded = ver + (0,) * (3 - len(ver))
    return padded >= DEPRECATED_SINCE


def _is_file_family(cap: Capability) -> bool:
    return cap.resource.startswith("file:")


def _is_net_family(cap: Capability) -> bool:
    return cap.resource.startswith("net:")


def _file_clause(cap: Capability) -> str:
    prefix = path_prefix(cap) or "/tmp/gludd/"
    verbs: list[str] = []
    if "read" in cap.actions:
        verbs.append("file-read*")
    if "write" in cap.actions:
        verbs.append("file-write*")
    if not verbs:
        verbs.append("file-read*")
    clauses = [f'(allow {v} (subpath "{prefix}"))' for v in verbs]
    return "\n  ".join(clauses)


def _net_clause(cap: Capability) -> str:
    hosts = allowed_hosts(cap)
    ports = allowed_ports(cap)
    if hosts:
        clauses: list[str] = []
        for host in hosts:
            port_s = str(ports[0]) if ports else "443"
            clauses.append(
                f'(allow network-outbound (to (remote tcp "{host}:{port_s}")))'
            )
        return "\n  ".join(clauses)
    return "(allow network-outbound)"


def _deny_clause(cap: Capability) -> str:
    if _is_file_family(cap):
        prefix = path_prefix(cap) or "/"
        return f'(deny file-read* (subpath "{prefix}"))'
    if _is_net_family(cap):
        return "(deny network-outbound)"
    return f'(deny {cap.resource})'


def render_profile(spec: PermissionSpec) -> str:
    """Render a Seatbelt ``sandbox.d`` profile text for ``spec``."""
    allow_clauses: list[str] = []
    for cap in spec.capabilities:
        if _is_file_family(cap):
            allow_clauses.append(_file_clause(cap))
        elif _is_net_family(cap):
            allow_clauses.append(_net_clause(cap))
    deny_clauses: list[str] = [_deny_clause(cap) for cap in spec.denied]
    body = "\n  ".join(allow_clauses + deny_clauses) or "  ;; empty spec — default deny"
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow process-fork)\n"
        "(allow signal (target self))\n"
        f"{body}\n"
    )


def _profile_path(spec: PermissionSpec) -> Path:
    return PROFILE_DIR / f"gludd-{spec.agent_type}.sb"


class SeatbeltBackend:
    name = "seatbelt"

    @staticmethod
    def available() -> bool:
        import shutil
        if shutil.which("sandbox-exec") is None:
            logger.warning(
                "sandbox-exec missing — likely macOS %s (removed in 15.4+)",
                platform.mac_ver()[0],
            )
            return False
        if _is_deprecated_host():
            logger.warning(
                "sandbox-exec is DEPRECATED on macOS %s (>= 15.4). Apple has "
                "marked it for removal; the binary may exist but enforcement "
                "is unreliable. Backend is NOT considered available.",
                platform.mac_ver()[0],
            )
            return False
        # Below 15.4 — binary present, but the path is on Apple's deprecation
        # track. Log loudly so the operator knows.
        logger.warning(
            "sandbox-exec is on Apple's deprecation track (macOS %s < 15.4 "
            "still ships it). Migrate to a Linux VM (Tart/Lima) before the "
            "15.4 cutoff.",
            platform.mac_ver()[0],
        )
        return True

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        profile_name = f"gludd-{spec.agent_type}"
        # LOUD deprecation gate: on macOS 15.4+ we refuse to claim enforcement.
        if _is_deprecated_host():
            logger.error(
                "REFUSING to apply seatbelt on macOS %s — sandbox-exec is "
                "DEPRECATED (>= 15.4) and may not enforce. Dispatching "
                "UNSANDBOXED. Run untrusted agent work in a Linux VM "
                "(Tart/Lima) on this host; see module docstring.",
                platform.mac_ver()[0],
            )
            return SandboxHandle(
                backend="seatbelt", token=profile_name, applied=False,
                extra={
                    "reason": (
                        f"sandbox-exec deprecated on macOS {platform.mac_ver()[0]} "
                        f"(>= 15.4); no enforcement"
                    ),
                    "deprecated": True,
                },
            )
        try:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            path = _profile_path(spec)
            path.write_text(render_profile(spec))
            rc = subprocess.run(
                ["sandbox-exec", "-f", str(path), "/bin/true"],
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
                severity="fail", message=f"sandbox-exec dry-run failed: {exc}", capability=None,
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
