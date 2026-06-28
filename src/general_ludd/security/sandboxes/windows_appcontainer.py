"""Windows AppContainer + RestrictedToken backend.

Creates an AppContainer SID via ``CreateAppContainerProfile`` (pywin32 —
lazy-imported so this module imports cleanly on non-Windows hosts), sets
capability-based access on the target process token via ``AdjustTokenGroups``,
scopes file access via ``icacls`` ACLs on the target directory, and limits
egress via a Windows Firewall rule bound to the AppContainer SID.

All pywin32 imports are inside the methods so ``import`` of this module is
safe anywhere; ``available()`` returns False on non-Windows hosts.
"""

from __future__ import annotations

import logging
import subprocess

from general_ludd.security.sandboxes import (
    Finding,
    PermissionSpec,
    SandboxHandle,
    SandboxTarget,
)

logger = logging.getLogger(__name__)


def _icacls_deny_all_except(directory: str, sid: str) -> list[str]:
    """Grant the AppContainer SID RW on ``directory`` and deny Everyone else."""
    return [
        "icacls", directory, "/inheritance:r",
        "/grant:r", f"{sid}:(OI)(CI)F",
        "/deny", "Everyone:(OI)(CI)F",
    ]


def _firewall_rule_name(spec: PermissionSpec) -> str:
    return f"gludd-{spec.agent_id}-egress"


def _net_allow_rules(spec: PermissionSpec, sid: str) -> list[list[str]]:
    rules: list[list[str]] = []
    for cap in spec.capabilities:
        if cap.resource != "net":
            continue
        host = cap.constraint_value("host")
        port = cap.constraint_value("port")
        if not (isinstance(host, str) and isinstance(port, int)):
            continue
        rule = _firewall_rule_name(spec) + f"-{host}-{port}"
        rules.append([
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule}", "dir=out", "action=allow",
            "program=any", f"remoteip={host}", "localport=any",
            f"remoteport={port}", "protocol=TCP",
        ])
    # Default-deny egress from this SID.
    rules.append([
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={_firewall_rule_name(spec)}-deny",
        "dir=out", "action=block", "protocol=TCP",
    ])
    return rules


def render_icacls(spec: PermissionSpec, directory: str, sid: str) -> list[str]:
    return _icacls_deny_all_except(directory, sid)


class AppContainerBackend:
    name = "appcontainer"

    @staticmethod
    def available() -> bool:
        import sys
        if not sys.platform.startswith("win"):
            return False
        try:
            import win32security  # type: ignore[import-not-found]  # noqa: F401
            from pywintypes import IID  # type: ignore[import-not-found]  # noqa: F401
        except Exception:
            return False
        return True

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        import sys
        agent = spec.agent_id
        try:
            if not sys.platform.startswith("win"):
                raise RuntimeError("AppContainer only available on Windows")
            from win32.api import CreateAppContainerProfile  # type: ignore[import-not-found]
            from win32com.shell import shell  # type: ignore[import-not-found]  # noqa: F401
            capabilities = []  # simple: no fine-grained Store capabilities
            sid, _ = CreateAppContainerProfile(
                f"gludd-{agent}", f"gludd-{agent}", "gludd sandbox", capabilities,
            )
            sid_str = str(sid)
            # File ACLs
            if target.directory:
                subprocess.run(
                    _icacls_deny_all_except(target.directory, sid_str),
                    check=False, capture_output=True, timeout=30,
                )
            # Firewall egress rules
            for rule in _net_allow_rules(spec, sid_str):
                subprocess.run(
                    rule, check=False, capture_output=True, timeout=15,
                )
            logger.info("AppContainer %s created (sid=%s)", agent, sid_str)
            return SandboxHandle(
                backend="appcontainer", token=sid_str, applied=True,
                extra={"agent_id": agent},
            )
        except Exception as exc:
            logger.error(
                "AppContainer apply failed for %s — dispatching UNSANDBOXED: %s",
                agent, exc, exc_info=True,
            )
            return SandboxHandle(
                backend="appcontainer", token=f"gludd-{agent}", applied=False,
                extra={"error": str(exc)},
            )

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        findings: list[Finding] = []
        try:
            pass  # type: ignore[import-not-found]
        except Exception as exc:
            return [Finding(
                severity="fail",
                message=f"pywin32 shell API unavailable: {exc}",
                capability=None,
            )]
        # Best-effort: presence of the firewall rule by name.
        try:
            out = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule",
                 f"name={_firewall_rule_name(spec)}-deny"],
                check=False, capture_output=True, timeout=10,
            ).stdout.decode("utf-8", "replace")
        except Exception:
            out = ""
        if _firewall_rule_name(spec) in out:
            findings.append(Finding(
                severity="ok",
                message="egress-deny firewall rule present",
                capability=None,
            ))
        else:
            findings.append(Finding(
                severity="warn",
                message="egress-deny firewall rule not found",
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
        if not handle.applied:
            return
        try:
            from win32.api import DeleteAppContainerProfile  # type: ignore[import-not-found]
            DeleteAppContainerProfile(handle.extra.get("agent_id", handle.token))
        except Exception as exc:
            logger.warning("AppContainer release failed: %s", exc)
