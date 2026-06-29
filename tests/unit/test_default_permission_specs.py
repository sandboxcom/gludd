from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.security.permissions import PermissionSpecParser

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "permissions"


_AGENT_FILES = {
    "build": "build",
    "primary": "primary",
    "subagent": "subagent",
    "task:implement_change": "task_implement_change",
}


@pytest.mark.parametrize("agent_type", list(_AGENT_FILES.keys()))
def test_default_spec_validates(agent_type):
    spec = PermissionSpecParser.parse_file(CONFIG_DIR / f"{_AGENT_FILES[agent_type]}.yml")
    errors = PermissionSpecParser.validate(spec)
    assert errors == [], f"{_AGENT_FILES[agent_type]}.yml validation errors: {errors}"
    assert spec.agent_type == agent_type
    assert spec.version == 1


def test_subagent_spec_denies_secret_openbao():
    spec = PermissionSpecParser.parse_file(CONFIG_DIR / "subagent.yml")
    denied_resources = {c.resource for c in spec.denied}
    assert "secret:openbao" in denied_resources


def test_primary_spec_narrows_subagent_via_subset():
    primary = PermissionSpecParser.parse_file(CONFIG_DIR / "primary.yml")
    subagent = PermissionSpecParser.parse_file(CONFIG_DIR / "subagent.yml")
    assert PermissionSpecParser.is_subset(subagent, primary) is True
