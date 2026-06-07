"""Ansible Galaxy search/install and builtin module listing."""

from __future__ import annotations

import re
import subprocess
from typing import Any

BUILTIN_MODULES = [
    "ansible.builtin.copy",
    "ansible.builtin.file",
    "ansible.builtin.template",
    "ansible.builtin.command",
    "ansible.builtin.shell",
    "ansible.builtin.service",
    "ansible.builtin.package",
    "ansible.builtin.user",
    "ansible.builtin.group",
    "ansible.builtin.git",
    "ansible.builtin.uri",
    "ansible.builtin.get_url",
    "ansible.builtin.debug",
    "ansible.builtin.assert",
    "ansible.builtin.set_fact",
    "ansible.builtin.include_role",
    "ansible.builtin.import_role",
    "ansible.builtin.cron",
    "ansible.builtin.systemd",
    "ansible.builtin.lineinfile",
    "ansible.builtin.blockinfile",
    "ansible.builtin.stat",
    "ansible.builtin.find",
    "ansible.builtin.replace",
    "ansible.builtin.wait_for",
    "ansible.builtin.pause",
    "ansible.builtin.fail",
    "ansible.builtin.fetch",
    "ansible.builtin.unarchive",
    "ansible.builtin.archive",
    "ansible.builtin.pip",
    "ansible.builtin.synchronize",
    "ansible.builtin.script",
    "ansible.builtin.raw",
    "ansible.builtin.meta",
    "ansible.builtin.add_host",
    "ansible.builtin.include_vars",
    "ansible.builtin.include_tasks",
    "ansible.builtin.import_tasks",
    "ansible.builtin.import_playbook",
]


def get_builtin_modules() -> list[str]:
    return sorted(BUILTIN_MODULES)


def parse_galaxy_search_output(output: str) -> list[dict[str, Any]]:
    if not output.strip():
        return []
    results: list[dict[str, Any]] = []
    for line in output.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("Found") or stripped.startswith("----"):
            continue
        if stripped.startswith("Name") and "Description" in stripped:
            continue
        match = re.match(r"^(\S+)\s+(.*)", stripped)
        if match:
            results.append({"name": match.group(1), "description": match.group(2).strip()})
    return results


def search_galaxy(query: str, galaxy_type: str = "role") -> list[dict[str, Any]]:
    cmd = ["ansible-galaxy", galaxy_type, "search", query, "--platforms", "all"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return parse_galaxy_search_output(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return [{"name": "error", "description": str(exc)}]


def install_galaxy(name: str, galaxy_type: str = "role") -> dict[str, Any]:
    cmd = ["ansible-galaxy", galaxy_type, "install", name]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        success = result.returncode == 0
        return {
            "name": name,
            "type": galaxy_type,
            "success": success,
            "output": result.stdout[:500] if success else result.stderr[:500],
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"name": name, "type": galaxy_type, "success": False, "output": str(exc)}
