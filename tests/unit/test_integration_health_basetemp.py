"""Regression contract for short, isolated integration-test temp paths."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _target_body(name: str) -> str:
    content = MAKEFILE.read_text(encoding="utf-8")
    start = content.index(f"{name}:")
    return content[start:].split("\n\n", 1)[0]


def test_integration_health_uses_short_project_scoped_basetemp() -> None:
    body = _target_body("integration-health")

    assert "resource_arbiter.py namespace" in body
    assert "PROJECT_KEY" in body
    assert 'BT="/tmp/gi-$$PROJECT_KEY-$$$$"' in body
    assert "PYTEST_ADDOPTS" in body
    assert "--basetemp=$$BT" in body
    assert 'rm -rf "$$BT"' in body


def test_integration_health_has_agent_facing_contract() -> None:
    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    entries = {entry["name"]: entry for entry in payload["targets"]}

    assert entries["integration-health"] == {
        "name": "integration-health",
        "make_variables": [],
        "behavior": "make integration-health",
    }
