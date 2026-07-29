"""Regression contract for the beta.3 gludd_agent_run Molecule failures.

Run 30489932257 failed these scenarios when ``gludd_agent_run`` imported the
controller application inside AnsiballZ. Their configs also emitted empty
inventory and missing lifecycle-playbook warnings. Keep the repaired harness
explicit so future Molecule upgrades cannot silently reintroduce either class.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SCENARIO_ROOT = _ROOT / "molecule" / "playbooks"
_SHARED_ROOT = _ROOT / "molecule" / "shared"

_FAILED_SCENARIOS = (
    "role_agent_task",
    "role_audit_dependencies",
    "role_audit_security",
    "role_debug_failure",
    "role_dependency_update",
    "role_document_change",
    "role_implement_change",
    "role_refactor_code",
    "role_triage_issue",
    "role_write_tests",
    "test_gludd_agent_run",
)
_LIFECYCLE_PHASES = ("create", "side_effect", "cleanup", "destroy")


def _config(scenario: str) -> dict:
    with (_SCENARIO_ROOT / scenario / "molecule.yml").open() as stream:
        loaded = yaml.safe_load(stream)
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.parametrize("scenario", _FAILED_SCENARIOS)
def test_failed_agent_run_scenario_has_explicit_localhost_inventory(
    scenario: str,
) -> None:
    config = _config(scenario)
    platforms = config.get("platforms")
    assert isinstance(platforms, list) and platforms
    assert any(
        platform.get("name") == "localhost"
        and platform.get("connection") == "local"
        for platform in platforms
    )

    localhost = (
        config["provisioner"]["inventory"]["hosts"]["all"]["hosts"]["localhost"]
    )
    assert localhost["ansible_connection"] == "local"
    assert localhost["ansible_python_interpreter"] == (
        "{{ ansible_playbook_python }}"
    )


@pytest.mark.parametrize("scenario", _FAILED_SCENARIOS)
def test_failed_agent_run_scenario_maps_every_lifecycle_playbook(
    scenario: str,
) -> None:
    config = _config(scenario)
    sequence = config["scenario"]["test_sequence"]
    playbooks = config["provisioner"]["playbooks"]

    for phase in _LIFECYCLE_PHASES:
        assert phase in sequence
        assert playbooks[phase] == (
            "${MOLECULE_PROJECT_DIRECTORY}/molecule/shared/"
            f"{phase}.yml"
        )
        assert (_SHARED_ROOT / f"{phase}.yml").is_file()

    assert sequence.count("cleanup") == 2
    assert sequence[-2:] == ["cleanup", "destroy"]


@pytest.mark.parametrize("scenario", _FAILED_SCENARIOS)
def test_failed_agent_run_scenario_declares_empty_dependencies(
    scenario: str,
) -> None:
    scenario_root = _SCENARIO_ROOT / scenario
    assert yaml.safe_load(
        (scenario_root / "requirements.yml").read_text()
    ) == []
    assert yaml.safe_load(
        (scenario_root / "collections.yml").read_text()
    ) == {"collections": []}


def test_shared_cleanup_is_namespaced_and_fail_closed() -> None:
    cleanup = (_SHARED_ROOT / "cleanup.yml").read_text()
    assert "MOLECULE_PROJECT_DIRECTORY" in cleanup
    assert "GLUDD_MOCK_PORT" in cleanup
    assert "_gludd_mock_owned" in cleanup
    assert "server.py" in cleanup
    assert "ansible.builtin.command" in cleanup


def test_analysis_only_dependency_scenario_does_not_regenerate_mcp_docs() -> None:
    converge = (
        _SCENARIO_ROOT
        / "role_dependency_update"
        / "default"
        / "converge.yml"
    ).read_text()
    assert "apply_updates: false" in converge
    assert "mcp_sync_enabled: false" in converge
