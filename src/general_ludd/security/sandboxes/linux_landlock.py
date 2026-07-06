"""Linux Landlock backend — per-process, unprivileged filesystem + network sandbox.

Landlock is a Linux Security Module (LSM) introduced in kernel 5.13 (filesystem)
and extended in 6.7 (network). Unlike AppArmor / SELinux (which are system-wide
and require root to load policy), Landlock is:

  * **Per-process** — each task can self-restrict; policy does not persist and
    does not need a system-wide profile.
  * **Unprivileged** — any process can apply Landlock rules after setting
    ``PR_SET_NO_NEW_PRIVS``; no root, no capabilities required.
  * **Stackable** — a child inherits the parent's Landlock rules and may add
    stricter ones (never looser).

These properties make Landlock the BEST fit for per-agent sandboxing on modern
Linux. AppArmor / SELinux remain useful as host-level defense-in-depth but are
the wrong tool for per-task work (system-wide, requires root, profile store is
shared state).

IRREVERSIBILITY (important design property)
------------------------------------------

Landlock restrictions are IRREVERSIBLE for the process that applied them: once
``landlock_restrict_self()`` is called, the ruleset cannot be relaxed or removed
for the lifetime of that process. This is **by design** — it is the same model
Chrome, Firefox, and OpenSSH use for sandboxing (a sandbox you could escape from
in-process would not be a sandbox).

The practical consequence: the daemon MUST fork before applying Landlock if it
wants the agent to run under a different ruleset than the daemon itself. The
``release()`` method here is therefore a no-op for the current process — it
exists for API symmetry with the other backends and for audit. Recovery from a
Landlock-restricted state is "fork a new process without the restriction".

Net support
-----------

Landlock ABI v6 (kernel 6.7+) adds ``handled_access_net`` for ``BIND_TCP`` and
``CONNECT_TCP``. UDP was added in ABI v10 (kernel 6.14+). Hostname-based
filtering (``allowed_hosts``) is NOT enforceable via Landlock alone — Landlock
operates on ports, not DNS names. Callers that need host-level filtering MUST
pair Landlock with seccomp or an eBPF filter; this backend logs a loud warning
for any ``allowed_hosts`` constraint it cannot enforce.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

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

# Landlock ABI versions of interest.
#   v1 (kernel 5.13) — filesystem
#   v6 (kernel 6.7)  — network TCP bind/connect
#   v10 (kernel 6.14) — network UDP  (future; recorded for completeness)
ABI_NET_TCP = 6
ABI_NET_UDP = 10

# Sentinel used by verify() to document that restrictions are irreversible.
LANDLOCK_RESTRICTIONS_ARE_IRREVERSIBLE = (
    "Landlock restrictions are IRREVERSIBLE within the process that applied "
    "them; release() is a no-op for the current process. Recovery is process-fork."
)


def _is_file_family(cap: Capability) -> bool:
    return cap.resource.startswith("file:")


def _is_net_family(cap: Capability) -> bool:
    return cap.resource.startswith("net:")


def _file_access_flags(landlock: Any) -> tuple[Any, Any]:
    """Return ``(read_flags, write_flags)`` for the installed pylandlock version."""
    AccessFS = landlock.AccessFs
    read_flags = (
        AccessFS.READ_FILE | AccessFS.READ_DIR
    )
    write_flags = (
        AccessFS.WRITE_FILE
        | AccessFS.MAKE_REG
        | AccessFS.REMOVE_FILE
        | AccessFS.MAKE_DIR
    )
    exec_flags = AccessFS.EXECUTE
    return read_flags, write_flags | exec_flags


def _kernel_supports_net(abi: int) -> bool:
    """True iff the running Landlock ABI supports net rules (>= v6, kernel 6.7+)."""
    return abi >= ABI_NET_TCP


class LandlockBackend:
    name = "landlock"

    @staticmethod
    def available() -> bool:
        """True iff pylandlock is importable + the kernel ABI is non-zero."""
        try:
            import importlib

            importlib.import_module("landlock")  # pylandlock: Linux-only, guarded by try/except
        except Exception:
            return False
        try:
            import importlib

            _landlock = importlib.import_module("landlock")  # pylandlock: Linux-only, guarded by try/except
            rs = _landlock.Ruleset()
            abi = rs.abi
        except Exception:
            return False
        return abi > 0

    @staticmethod
    def _kernel_supports_net(abi: int) -> bool:
        # kept for back-compat; module-level _kernel_supports_net is preferred.
        return _kernel_supports_net(abi)

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        ruleset_name = f"gludd-{spec.agent_type}"
        try:
            import importlib

            landlock = importlib.import_module("landlock")  # pylandlock: Linux-only, guarded by try/except
            Ruleset = landlock.Ruleset
        except Exception as exc:
            logger.warning(
                "Landlock apply skipped (%s): pylandlock not importable — UNSANDBOXED",
                exc,
            )
            return SandboxHandle(
                backend="landlock", token=ruleset_name, applied=False,
                extra={"reason": f"pylandlock import failed: {exc}"},
            )
        try:
            rs = Ruleset()
            abi = rs.abi
            if abi <= 0:
                logger.warning(
                    "Landlock kernel ABI is %d (disabled) — UNSANDBOXED", abi,
                )
                return SandboxHandle(
                    backend="landlock", token=ruleset_name, applied=False,
                    extra={"reason": f"kernel abi={abi} (Landlock disabled)"},
                )

            read_flags, write_flags = _file_access_flags(landlock)
            # Configure handled_access_fs with the superset we care about.
            handled_fs = read_flags | write_flags
            if _kernel_supports_net(abi):
                AccessNet = landlock.AccessNet
                handled_net = AccessNet.CONNECT_TCP | AccessNet.BIND_TCP
                try:
                    rs = Ruleset(handled_access_fs=handled_fs, handled_access_net=handled_net)
                except TypeError:
                    rs = Ruleset(handled_access_fs=handled_fs)
            else:
                rs = Ruleset(handled_access_fs=handled_fs)

            added_paths: list[str] = []
            added_ports: list[int] = []
            seen_net_hosts: list[str] = []

            for cap in spec.capabilities:
                if _is_file_family(cap):
                    prefix = path_prefix(cap)
                    if not prefix:
                        continue
                    if "write" in cap.actions:
                        access = write_flags
                    elif "execute" in cap.actions:
                        access = read_flags | landlock.AccessFs.EXECUTE
                    else:
                        access = read_flags
                    try:
                        rs.allow(prefix, access)
                        added_paths.append(prefix)
                    except Exception as exc:
                        logger.warning(
                            "Landlock could not allow %s (%s) — skipping rule",
                            prefix, exc,
                        )
                elif _is_net_family(cap):
                    hosts = allowed_hosts(cap)
                    ports = allowed_ports(cap)
                    if hosts and not ports:
                        # Landlock filters by port, not hostname. A spec that
                        # has hosts but no ports cannot be enforced by Landlock
                        # alone — surface a loud warning so the operator pairs
                        # it with seccomp / eBPF.
                        logger.warning(
                            "Landlock cannot enforce hostname allow-list %r "
                            "(Landlock operates on ports, not DNS names). "
                            "Pair Landlock with seccomp or an eBPF filter for "
                            "host-level net egress control.", hosts,
                        )
                        seen_net_hosts.extend(hosts)
                        continue
                    if _kernel_supports_net(abi):
                        AccessNet = landlock.AccessNet
                        # Landlock bind/connect applies to ports, not hosts.
                        # We allow CONNECT_TCP to all hosts but constrain ports.
                        # Hostname filtering MUST be done elsewhere (seccomp/eBPF).
                        for port in ports:
                            try:
                                rs.allow_net(
                                    port=port,
                                    access=AccessNet.CONNECT_TCP,
                                )
                                added_ports.append(port)
                            except Exception as exc:
                                logger.warning(
                                    "Landlock could not allow port %d (%s) — skipping",
                                    port, exc,
                                )
                        if hosts:
                            logger.warning(
                                "Landlock net rules are port-scoped; hostname "
                                "constraints %r are NOT enforced here.", hosts,
                            )
                            seen_net_hosts.extend(hosts)
                    else:
                        logger.warning(
                            "Landlock ABI %d does not support net rules "
                            "(needs >= %d) — net capabilities unenforced",
                            abi, ABI_NET_TCP,
                        )

            # PR_SET_NO_NEW_PRIVS is required before restrict_self so a setuid
            # binary cannot later escalate out of the sandbox.
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            PR_SET_NO_NEW_PRIVS = 38
            rc = libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
            if rc != 0:
                err = ctypes.get_errno()
                logger.error(
                    "Landlock prctl(PR_SET_NO_NEW_PRIVS) failed (errno=%d) — UNSANDBOXED",
                    err,
                )
                return SandboxHandle(
                    backend="landlock", token=ruleset_name, applied=False,
                    extra={"reason": f"prctl PR_SET_NO_NEW_PRIVS errno={err}"},
                )

            rs.apply()
            # apply() calls landlock_restrict_self() internally; we keep the
            # ruleset fd for audit and release it after restrict (restrict
            # keeps the restrictions in effect even after the fd closes).
            try:
                rs_fd = int(rs.ruleset_fd) if hasattr(rs, "ruleset_fd") else -1
            except Exception:
                rs_fd = -1
            logger.info(
                "Landlock ruleset %s applied (abi=%d, paths=%d, ports=%d) — "
                "restrictions are IRREVERSIBLE for this process",
                ruleset_name, abi, len(added_paths), len(added_ports),
            )
            return SandboxHandle(
                backend="landlock", token=ruleset_name, applied=True,
                extra={
                    "abi": abi,
                    "allowed_paths": added_paths,
                    "allowed_ports": added_ports,
                    "unhandled_net_hosts": seen_net_hosts,
                    "ruleset_fd": rs_fd,
                    "irreversible": True,
                },
            )
        except Exception as exc:
            logger.error(
                "Landlock apply failed for %s — dispatching UNSANDBOXED: %s",
                ruleset_name, exc, exc_info=True,
            )
            return SandboxHandle(
                backend="landlock", token=ruleset_name, applied=False,
                extra={"reason": str(exc)},
            )

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        findings: list[Finding] = []
        if not handle.applied:
            findings.append(Finding(
                severity="warn",
                message=(
                    f"apply() reported applied=False (reason="
                    f"{handle.extra.get('reason', 'unknown')}); Landlock is advisory"
                ),
                capability=None,
            ))
            return findings
        # Probe: try to open something outside the allowed paths.
        # Landlock is process-local — we cannot re-probe from this process
        # without forking. Instead we trust the ruleset_fd and abi recorded at
        # apply() time and surface a structural ok + an irreversibility note.
        abi = handle.extra.get("abi")
        findings.append(Finding(
            severity="ok",
            message=(
                f"Landlock ruleset applied (abi={abi}); "
                f"{LANDLOCK_RESTRICTIONS_ARE_IRREVERSIBLE}"
            ),
            capability=None,
        ))
        unhandled = handle.extra.get("unhandled_net_hosts") or []
        if unhandled:
            findings.append(Finding(
                severity="warn",
                message=(
                    f"net hostname constraints {unhandled!r} were NOT enforced "
                    f"by Landlock (port-only LSM); pair with seccomp/eBPF"
                ),
                capability=None,
            ))
        return findings

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        # IRREVERSIBLE: closing the ruleset fd does NOT lift restrictions.
        # We close it for tidiness (the kernel holds the restrictions
        # independently of the fd once landlock_restrict_self has run).
        rs_fd = handle.extra.get("ruleset_fd", -1)
        if isinstance(rs_fd, int) and rs_fd > 0:
            with contextlib.suppress(OSError):
                os.close(rs_fd)
        logger.debug(
            "Landlock release(%s): restrictions remain in effect for this "
            "process (irreversible); fd closed for tidiness", handle.token,
        )


__all__ = ["LandlockBackend"]
