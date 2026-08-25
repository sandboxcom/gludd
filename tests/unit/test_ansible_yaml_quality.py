"""Behavioral contracts for collection role indirection used by YAML lint."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
AGENT_NETWORKING_TASKS = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "roles"
    / "networking"
    / "tasks"
    / "main.yml"
)


def test_agent_networking_role_delegates_to_canonical_collection() -> None:
    """The compatibility role must not reference task files it does not ship."""
    tasks = yaml.safe_load(AGENT_NETWORKING_TASKS.read_text(encoding="utf-8"))

    assert tasks == [
        {
            "name": "Delegate networking operations to the canonical collection role",
            "ansible.builtin.include_role": {
                "name": "general_ludd.networking.networking",
            },
        }
    ]
