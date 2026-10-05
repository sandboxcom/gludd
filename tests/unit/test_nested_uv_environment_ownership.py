"""Contracts for nested checks that consume the active test environment."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> str:
    """Return one target recipe from the repository Makefile."""
    content = MAKEFILE.read_text()
    return content.split(f"{target}:\n", 1)[1].split("\n\n", 1)[0]


def test_healthcheck_never_resyncs_the_active_test_environment() -> None:
    """Nested health checks must not replace the running workers' venv."""
    assert _recipe("healthcheck").count("$(UV) run --no-sync python") == 3


def test_ansible_syntax_never_resyncs_the_active_test_environment() -> None:
    """Nested syntax checks must consume, not mutate, the active venv."""
    assert "$(UV) run --no-sync ansible-playbook" in _recipe("ansible-syntax")
