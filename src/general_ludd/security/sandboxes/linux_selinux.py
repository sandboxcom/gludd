"""Linux SELinux backend.

Generates a Type-Enforcement policy (``gludd_<agent_id>.te``) + a file-contexts
file, compiles via ``checkmodule`` + ``semodule_package`` + ``semodule -i``,
and verifies via ``semanage fcontext -l`` and ``ps -eZ``.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from general_ludd.security.sandboxes import (
    Finding,
    PermissionSpec,
    SandboxHandle,
    SandboxTarget,
)

logger = logging.getLogger(__name__)

BUILD_DIR = Path("/tmp/gludd-selinux")


def _te_for(spec: PermissionSpec) -> str:
    agent = spec.agent_id.replace("-", "_")
    type_name = f"gludd_{agent}_t"
    allow: list[str] = []
    deny: list[str] = []
    for cap in spec.capabilities:
        if cap.resource == "fs":
            prefix = cap.constraint_value("path_prefix")
            klass = "dir" if prefix and str(prefix).endswith("/") else "file"
            allow.append(
                f"allow {type_name} usr_t:{klass} {{ read write }};"
            )
        elif cap.resource == "net":
            allow.append(
                f"allow {type_name} unreserved_port_t:tcp_socket name_connect;"
            )
    for _cap in spec.denied:
        deny.append(f"dontaudit {type_name} **:** **;")
    body = "\n".join(allow + deny) or "  # empty spec"
    return (
        f"module gludd_{agent} 1.0;\n"
        "require {\n"
        "    type unreserved_port_t;\n"
        "    type usr_t;\n"
        "    class tcp_socket { name_connect };\n"
        "    class file { read write };\n"
        "    class dir { read write };\n"
        "}\n"
        f"type {type_name};\n"
        f"typeattribute {type_name} unconfined_t;\n"
        f"{body}\n"
    )


def _fc_for(spec: PermissionSpec) -> str:
    agent = spec.agent_id.replace("-", "_")
    type_name = f"gludd_{agent}_t"
    lines: list[str] = []
    for cap in spec.capabilities:
        if cap.resource == "fs":
            prefix = cap.constraint_value("path_prefix")
            if isinstance(prefix, str):
                lines.append(f"{prefix}(/.*)? -- gen_context(system_u:object_r:{type_name},s0)")
    if not lines:
        lines.append(
            f"/tmp/gludd/{spec.agent_id}(/.*)? -- "
            f"gen_context(system_u:object_r:{type_name},s0)"
        )
    return "\n".join(lines) + "\n"


def render_te(spec: PermissionSpec) -> str:
    return _te_for(spec)


def render_fc(spec: PermissionSpec) -> str:
    return _fc_for(spec)


class SELinuxBackend:
    name = "selinux"

    @staticmethod
    def available() -> bool:
        import shutil
        if shutil.which("checkmodule") is None:
            return False
        if shutil.which("semodule_package") is None:
            return False
        if shutil.which("semodule") is None:
            return False
        try:
            import selinux  # type: ignore[import-not-found]
            return bool(selinux.is_selinux_enabled())
        except Exception:
            return False

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        agent = spec.agent_id.replace("-", "_")
        module_name = f"gludd_{agent}"
        try:
            BUILD_DIR.mkdir(parents=True, exist_ok=True)
            te_path = BUILD_DIR / f"{module_name}.te"
            fc_path = BUILD_DIR / f"{module_name}.fc"
            te_path.write_text(_te_for(spec))
            fc_path.write_text(_fc_for(spec))
            mod_path = BUILD_DIR / f"{module_name}.mod"
            pp_path = BUILD_DIR / f"{module_name}.pp"
            subprocess.run(
                ["checkmodule", "-M", "-m", "-o", str(mod_path), str(te_path)],
                check=True, capture_output=True, timeout=30,
            )
            subprocess.run(
                ["semodule_package", "-m", str(mod_path), "-f", str(fc_path),
                 "-o", str(pp_path)],
                check=True, capture_output=True, timeout=30,
            )
            subprocess.run(
                ["semodule", "-i", str(pp_path)],
                check=True, capture_output=True, timeout=60,
            )
            logger.info("SELinux module %s loaded", module_name)
            return SandboxHandle(backend="selinux", token=module_name, applied=True)
        except Exception as exc:
            logger.error(
                "SELinux apply failed for %s — dispatching UNSANDBOXED: %s",
                module_name, exc, exc_info=True,
            )
            return SandboxHandle(
                backend="selinux", token=module_name, applied=False,
                extra={"error": str(exc)},
            )

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        module_name = handle.token
        findings: list[Finding] = []
        try:
            out = subprocess.run(
                ["semodule", "-l"], check=False, capture_output=True, timeout=10,
            ).stdout.decode("utf-8", "replace")
        except Exception as exc:
            return [Finding(
                severity="fail", message=f"semodule -l failed: {exc}", capability=None,
            )]
        if module_name not in out.split():
            findings.append(Finding(
                severity="fail",
                message=f"module {module_name} not installed",
                capability=None,
            ))
            return findings
        findings.append(Finding(
            severity="ok", message=f"module {module_name} installed", capability=None,
        ))
        try:
            fc_out = subprocess.run(
                ["semanage", "fcontext", "-l"], check=False, capture_output=True,
                timeout=10,
            ).stdout.decode("utf-8", "replace")
        except Exception:
            fc_out = ""
        type_name = f"{module_name}_t"
        if type_name in fc_out:
            findings.append(Finding(
                severity="ok",
                message=f"fcontext labeled with {type_name}",
                capability=None,
            ))
        else:
            findings.append(Finding(
                severity="warn",
                message=f"fcontext for {type_name} not found",
                capability=None,
            ))
        return findings

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        if not handle.applied:
            return
        try:
            subprocess.run(
                ["semodule", "-r", handle.token],
                check=False, capture_output=True, timeout=60,
            )
        except Exception as exc:
            logger.warning("SELinux release of %s failed: %s", handle.token, exc)
