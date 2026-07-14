"""Security policy definitions for sandbox environments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SecurityPolicy:
    capabilities: list[str] = field(default_factory=list)
    read_only_root: bool = True
    privileged: bool = False
    seccomp_profile: str = ""
    apparmor_profile: str = ""
    no_new_privileges: bool = True
    allow_privilege_escalation: bool = False
    read_only_paths: list[str] = field(default_factory=list)
    writable_paths: list[str] = field(default_factory=list)
    hidden_paths: list[str] = field(default_factory=list)

    def to_docker_args(self) -> list[str]:
        args: list[str] = []
        if self.read_only_root:
            args.append("--read-only")
        if not self.privileged:
            args.extend(["--security-opt", "no-new-privileges:true"])
        else:
            args.append("--privileged")
        if self.seccomp_profile:
            args.extend(["--security-opt", f"seccomp={self.seccomp_profile}"])
        if self.apparmor_profile:
            args.extend(["--security-opt", f"apparmor={self.apparmor_profile}"])
        for cap in self.capabilities:
            args.extend(["--cap-add", cap])
        for path in self.read_only_paths:
            args.extend(["-v", f"{path}:{path}:ro"])
        for path in self.writable_paths:
            args.extend(["-v", f"{path}:{path}:rw"])
        return args

    def to_kubernetes_context(self) -> dict[str, Any]:
        sec_context: dict[str, Any] = {
            "allowPrivilegeEscalation": self.allow_privilege_escalation,
            "privileged": self.privileged,
            "readOnlyRootFilesystem": self.read_only_root,
        }
        if self.capabilities:
            sec_context["capabilities"] = {"add": self.capabilities}
        if self.seccomp_profile:
            sec_context["seccompProfile"] = {"type": "Localhost", "localhostProfile": self.seccomp_profile}
        return sec_context

    def is_restrictive(self) -> bool:
        return (
            not self.privileged
            and self.no_new_privileges
            and not self.allow_privilege_escalation
            and self.read_only_root
            and len(self.capabilities) == 0
        )

    @classmethod
    def minimal(cls) -> SecurityPolicy:
        return cls()

    @classmethod
    def default_docker(cls) -> SecurityPolicy:
        return cls(
            capabilities=[], read_only_root=True, privileged=False,
            no_new_privileges=True, allow_privilege_escalation=False,
        )
