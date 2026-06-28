"""Linux AppArmor backend.

Translates a :class:`PermissionSpec` into an AppArmor profile under
``/etc/apparmor.d/gludd-<agent_id>``, loads it via ``apparmor_parser -r``, and
verifies via ``aa-status --json``. Denies (``spec.denied``) become ``deny``
rules that win over any ``allow``; file-path constraints become
``path.prefix=`` rules and net constraints become ``network ... peer=`` rules.
"""

from __future__ import annotations

import json
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

PROFILE_DIR = Path("/etc/apparmor.d")


def _deny_rule_for(cap: Capability) -> str:
    if cap.resource == "fs":
        prefix = cap.constraint_value("path_prefix")
        target = prefix if isinstance(prefix, str) else "/**"
        return f"  deny {target} rwklx,"
    if cap.resource == "net":
        host = cap.constraint_value("host")
        peer = f' peer="{host}"' if isinstance(host, str) else ""
        return f"  deny network inet stream,{peer}"
    return f"  deny {cap.resource} **,"


def _allow_rule_for(cap: Capability) -> str:
    if cap.resource == "fs":
        prefix = cap.constraint_value("path_prefix")
        target = prefix if isinstance(prefix, str) else "/**"
        perms = "".join(sorted(cap.actions)) or "r"
        return f"  {target} {perms},"
    if cap.resource == "net":
        host = cap.constraint_value("host")
        port = cap.constraint_value("port")
        peer = f' peer="{host}"' if isinstance(host, str) else ""
        port_clause = f" port={port}" if isinstance(port, int) else ""
        return f"  network inet stream,{peer}{port_clause}"
    return f"  {cap.resource},"


def render_profile(spec: PermissionSpec, target: SandboxTarget) -> str:
    """Render an AppArmor profile text for ``spec``."""
    profile_name = f"gludd-{spec.agent_id}"
    attach = ""
    if target.pid is not None:
        attach = f"\n  audit deny ptrace,\n  signal peer=@{profile_name},"
    rules: list[str] = []
    for cap in spec.denied:
        rules.append(_deny_rule_for(cap))
    for cap in spec.capabilities:
        rules.append(_allow_rule_for(cap))
    body = "\n".join(rules) if rules else "  # empty spec — default deny"
    return (
        "#include <tunables/global>\n"
        f"profile {profile_name} flags=(attach_disconnected) {{"
        f"{attach}\n"
        f"{body}\n"
        "}\n"
    )


class AppArmorBackend:
    name = "apparmor"

    @staticmethod
    def available() -> bool:
        import shutil
        if shutil.which("apparmor_parser") is None:
            return False
        if shutil.which("aa-status") is None:
            return False
        try:
            rc = subprocess.run(
                ["aa-status"], check=False, capture_output=True, timeout=2,
            ).returncode
        except Exception:
            return False
        return rc == 0

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        profile_name = f"gludd-{spec.agent_id}"
        path = PROFILE_DIR / profile_name
        try:
            text = render_profile(spec, target)
            path.write_text(text)
            subprocess.run(
                ["apparmor_parser", "-r", str(path)],
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info("AppArmor profile %s loaded", profile_name)
            return SandboxHandle(backend="apparmor", token=profile_name, applied=True)
        except Exception as exc:
            logger.error(
                "AppArmor apply failed for %s — dispatching UNSANDBOXED: %s",
                profile_name, exc, exc_info=True,
            )
            return SandboxHandle(
                backend="apparmor", token=profile_name, applied=False,
                extra={"error": str(exc)},
            )

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        findings: list[Finding] = []
        profile_name = handle.token
        try:
            out = subprocess.run(
                ["aa-status", "--json"], check=False, capture_output=True, timeout=5,
            ).stdout.decode("utf-8", "replace")
            data = json.loads(out) if out.strip() else {}
        except Exception as exc:
            return [Finding(
                severity="fail",
                message=f"aa-status unreadable: {exc}",
                capability=None,
            )]
        loaded = set(data.get("profiles", {}))
        if profile_name not in loaded:
            findings.append(Finding(
                severity="fail",
                message=f"profile {profile_name} not loaded",
                capability=None,
            ))
            return findings
        findings.append(Finding(
            severity="ok",
            message=f"profile {profile_name} loaded",
            capability=None,
        ))
        text = (PROFILE_DIR / profile_name).read_text() if (PROFILE_DIR / profile_name).exists() else ""
        for cap in spec.denied:
            rule = _deny_rule_for(cap).strip()
            if rule not in text:
                findings.append(Finding(
                    severity="fail",
                    message=f"deny rule missing: {rule}",
                    capability=cap,
                ))
            else:
                findings.append(Finding(
                    severity="ok",
                    message=f"deny rule present: {rule}",
                    capability=cap,
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
            subprocess.run(
                ["apparmor_parser", "-R", str(PROFILE_DIR / handle.token)],
                check=False, capture_output=True, timeout=30,
            )
        except Exception as exc:
            logger.warning("AppArmor release of %s failed: %s", handle.token, exc)
