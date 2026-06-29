"""FreeBSD jail(8) backend.

Builds a chroot-based jail with limited devfs rules and pf-anchor-scoped
egress. File constraints select the chroot path; net constraints become
``pf.conf`` anchor rules.
"""

from __future__ import annotations

import contextlib
import logging
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


def _is_file_family(cap: Capability) -> bool:
    return cap.resource.startswith("file:")


def _is_net_family(cap: Capability) -> bool:
    return cap.resource.startswith("net:")


def _jail_path(spec: PermissionSpec, target: SandboxTarget) -> str:
    if target.directory:
        return target.directory
    for cap in spec.capabilities:
        if _is_file_family(cap):
            prefix = path_prefix(cap)
            if prefix:
                return prefix.rstrip("/")
    return f"/tmp/gludd/{spec.agent_type}"


def _pf_rules(spec: PermissionSpec, anchor: str) -> str:
    """Render a pf anchor body limiting egress to granted hosts/ports."""
    lines = [f'# pf anchor {anchor}']
    has_net = False
    for cap in spec.capabilities:
        if not _is_net_family(cap):
            continue
        has_net = True
        hosts = allowed_hosts(cap)
        ports = allowed_ports(cap)
        if hosts and ports:
            for host in hosts:
                for port in ports:
                    lines.append(
                        f'pass out quick proto tcp to "{host}" port {port}'
                    )
        elif hosts:
            for host in hosts:
                lines.append(f'pass out quick proto tcp to "{host}"')
    if has_net:
        lines.append("block out quick proto tcp from any to any")
    return "\n".join(lines) + "\n"


def _devfs_rule_for(spec: PermissionSpec) -> str:
    """Render a devfs.rules entry limiting device visibility."""
    return (
        "[gludd_rules=10]\n"
        "add hide\n"
        "add path 'null' unhide\n"
        "add path 'random' unhide\n"
        "add path 'urandom' unhide\n"
        "add path 'zero' unhide\n"
    )


def render_jail_command(spec: PermissionSpec, target: SandboxTarget) -> list[str]:
    path = _jail_path(spec, target)
    return [
        "jail", "-c",
        f"path={path}",
        f"host.hostname=gludd-{spec.agent_type}",
        "ip4=inherit",
        "allow.mount",
        "devfs_ruleset=10",
        "persist",
    ]


def render_pf_rules(spec: PermissionSpec, anchor: str = "gludd") -> str:
    return _pf_rules(spec, anchor)


class JailBackend:
    name = "jail"

    @staticmethod
    def available() -> bool:
        import shutil
        return shutil.which("jail") is not None

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        jail_name = f"gludd-{spec.agent_type}"
        try:
            path = _jail_path(spec, target)
            Path(path).mkdir(parents=True, exist_ok=True)
            devfs_rules = Path("/etc/devfs.rules")
            try:
                with devfs_rules.open("a") as fh:
                    fh.write(_devfs_rule_for(spec))
            except PermissionError:
                logger.warning("cannot write /etc/devfs.rules — using default devfs")
            cmd = [*render_jail_command(spec, target), f"name={jail_name}"]
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            pf_rules = _pf_rules(spec, anchor=jail_name)
            anchor_path = Path(f"/var/db/gludd/pf-{jail_name}.conf")
            anchor_path.parent.mkdir(parents=True, exist_ok=True)
            anchor_path.write_text(pf_rules)
            with contextlib.suppress(FileNotFoundError):
                subprocess.run(
                    ["pfctl", "-a", jail_name, "-f", str(anchor_path)],
                    check=False, capture_output=True, timeout=10,
                )
            logger.info("FreeBSD jail %s created", jail_name)
            return SandboxHandle(backend="jail", token=jail_name, applied=True)
        except Exception as exc:
            logger.error(
                "jail apply failed for %s — dispatching UNSANDBOXED: %s",
                jail_name, exc, exc_info=True,
            )
            return SandboxHandle(
                backend="jail", token=jail_name, applied=False,
                extra={"error": str(exc)},
            )

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        findings: list[Finding] = []
        try:
            out = subprocess.run(
                ["jls", "-n", "name"], check=False, capture_output=True, timeout=5,
            ).stdout.decode("utf-8", "replace")
        except Exception as exc:
            return [Finding(
                severity="fail", message=f"jls failed: {exc}", capability=None,
            )]
        if handle.token not in out:
            findings.append(Finding(
                severity="fail",
                message=f"jail {handle.token} not present in jls",
                capability=None,
            ))
            return findings
        findings.append(Finding(
            severity="ok", message=f"jail {handle.token} running", capability=None,
        ))
        return findings

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        if not handle.applied:
            return
        try:
            subprocess.run(
                ["jail", "-r", handle.token],
                check=False, capture_output=True, timeout=15,
            )
        except Exception as exc:
            logger.warning("jail release of %s failed: %s", handle.token, exc)
