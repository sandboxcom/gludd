"""Ansible playbook manifest generator."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from general_ludd.ansible.action_policy import ActionManifest

_FQCN_PATTERN = re.compile(r"^[a-z][a-z0-9_]+\.[a-z][a-z0-9_]+\.[a-z][a-z0-9_]+$")
_MODULE_TASK_KEYS = {
    "name",
    "when",
    "loop",
    "with_items",
    "with_dict",
    "register",
    "become",
    "become_user",
    "delegate_to",
    "ignore_errors",
    "notify",
    "tags",
    "vars",
    "block",
    "rescue",
    "always",
}


def _extract_modules_from_task(task: dict[str, Any]) -> list[str]:
    modules: list[str] = []
    for key in task:
        if key in _MODULE_TASK_KEYS:
            continue
        if ("." in key and _FQCN_PATTERN.match(key)) or key in (
            "ansible.builtin.shell",
            "ansible.builtin.command",
        ):
            modules.append(key)
    return modules


def generate_manifest(playbook_path: str) -> ActionManifest:
    path = Path(playbook_path)
    with open(path) as f:
        plays = yaml.safe_load(f) or []

    roles: list[str] = []
    collections: list[str] = []
    modules: list[str] = []
    tags: list[str] = []

    for play in plays:
        if not isinstance(play, dict):
            continue

        for role_entry in play.get("roles", []):
            if isinstance(role_entry, str):
                roles.append(role_entry)
            elif isinstance(role_entry, dict) and "role" in role_entry:
                roles.append(role_entry["role"])

        for tag in play.get("tags", []):
            if tag not in tags:
                tags.append(tag)

        for task in play.get("tasks", []):
            if not isinstance(task, dict):
                continue
            for mod in _extract_modules_from_task(task):
                if mod not in modules:
                    modules.append(mod)
            for tag in task.get("tags", []):
                if tag not in tags:
                    tags.append(tag)

        for collection in play.get("collections", []):
            if collection not in collections:
                collections.append(collection)

    return ActionManifest(
        playbook=path.name,
        roles=roles,
        collections=collections,
        modules=modules,
        tags=tags,
    )
