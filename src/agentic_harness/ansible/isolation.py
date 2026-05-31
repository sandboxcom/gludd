"""Ansible process isolation configuration."""

from __future__ import annotations

import shutil
from typing import Any

from pydantic import BaseModel, Field

_TOOL_PATH_MAP: dict[str, list[str]] = {
    "bash": ["/usr/bin/bash", "/bin/sh", "/bin/bash"],
    "python": ["/usr/bin/python", "/usr/bin/python3"],
    "git": [".git"],
    "docker": ["/var/run/docker.sock"],
    "network": [],
}

_SHELL_MODULES = {
    "ansible.builtin.shell",
    "ansible.builtin.command",
    "ansible.legacy.shell",
    "ansible.legacy.command",
    "shell",
    "command",
}

_WRITE_MODULES = {
    "ansible.builtin.copy",
    "ansible.builtin.template",
    "ansible.builtin.file",
    "ansible.builtin.assemble",
    "ansible.builtin.replace",
    "ansible.builtin.lineinfile",
    "ansible.builtin.blockinfile",
    "ansible.legacy.copy",
    "ansible.legacy.template",
    "ansible.legacy.file",
}


class ProcessIsolationConfig(BaseModel):
    enabled: bool = False
    executable: str = "podman"
    isolation_path: str | None = None
    hide_paths: list[str] = Field(default_factory=list)
    show_paths: list[str] = Field(default_factory=list)
    ro_paths: list[str] = Field(default_factory=list)
    block_local_tools: list[str] = Field(default_factory=list)

    def to_runner_kwargs(self) -> dict[str, Any]:
        resolved_hide: list[str] = list(self.hide_paths)
        for tool in self.block_local_tools:
            for p in self.resolve_tool_paths(tool):
                if p not in resolved_hide:
                    resolved_hide.append(p)
        return {
            "process_isolation": self.enabled,
            "process_isolation_executable": self.executable,
            "process_isolation_path": self.isolation_path,
            "process_isolation_hide_paths": resolved_hide,
            "process_isolation_show_paths": list(self.show_paths),
            "process_isolation_ro_paths": list(self.ro_paths),
        }

    def resolve_tool_paths(self, tool_name: str) -> list[str]:
        if tool_name in _TOOL_PATH_MAP:
            paths = list(_TOOL_PATH_MAP[tool_name])
            if tool_name == "python":
                resolved = shutil.which("python3")
                if resolved and resolved not in paths:
                    paths.append(resolved)
                resolved = shutil.which("python")
                if resolved and resolved not in paths:
                    paths.append(resolved)
            return paths
        if tool_name == "file_write":
            return ["/workspace", "/project"]
        return []

    def is_module_blocked(self, module_name: str) -> bool:
        if not self.enabled or not self.block_local_tools:
            return False
        return bool(
            ("bash" in self.block_local_tools and module_name in _SHELL_MODULES)
            or (
                "file_write" in self.block_local_tools
                and module_name in _WRITE_MODULES
            )
        )
