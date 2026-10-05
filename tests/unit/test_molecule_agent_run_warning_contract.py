"""Regression contract for the beta.3 gludd_agent_run Molecule failures.

Run 30489932257 failed these scenarios when ``gludd_agent_run`` imported the
controller application inside AnsiballZ. Their configs also emitted empty
inventory and missing lifecycle-playbook warnings. Keep the repaired harness
explicit so future Molecule upgrades cannot silently reintroduce either class.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SCENARIO_ROOT = _ROOT / "molecule" / "playbooks"
_SHARED_ROOT = _ROOT / "molecule" / "shared"
_IMPLEMENT_CHANGE_ROOT = (
    _ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "roles"
    / "implement_change"
)

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
_MOCK_DAEMON_SCENARIOS = frozenset(
    {"role_agent_task", "role_implement_change", "role_refactor_code"}
)


def _config(scenario: str) -> dict[str, Any]:
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

    lifecycle_files = {
        "create": "create.yml",
        "cleanup": (
            "mock_daemon_cleanup.yml"
            if scenario in _MOCK_DAEMON_SCENARIOS
            else "cleanup.yml"
        ),
        "destroy": (
            "mock_daemon_destroy.yml"
            if scenario in _MOCK_DAEMON_SCENARIOS
            else "destroy.yml"
        ),
    }
    if scenario not in _MOCK_DAEMON_SCENARIOS:
        lifecycle_files["side_effect"] = "side_effect.yml"

    for phase, filename in lifecycle_files.items():
        assert phase in sequence
        assert playbooks[phase] == (
            "${MOLECULE_PROJECT_DIRECTORY}/molecule/shared/"
            f"{filename}"
        )
        assert (_SHARED_ROOT / filename).is_file()

    if scenario in _MOCK_DAEMON_SCENARIOS:
        assert "side_effect" not in sequence
        assert "side_effect" not in playbooks

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


def test_implement_change_only_runs_available_mcp_authoring_helpers() -> None:
    """An arbitrary target repository must not emit false MCP failure warnings."""
    tasks = yaml.safe_load(
        (_IMPLEMENT_CHANGE_ROOT / "tasks" / "mcp_sync.yml").read_text()
    )
    by_name = {task["name"]: task for task in tasks}

    expected = {
        "MCP documentation checker": (
            "Inspect target worktree for MCP documentation checker",
            "Ensure newly-authored modules carry a DOCUMENTATION block "
            "(stub if missing)",
            "Warn if an authored resource is undocumented and stubbing is disabled",
            "scripts/mcp_docs_check.py",
            "_mcp_docs_script",
        ),
        "MCP tool generator": (
            "Inspect target worktree for MCP tool generator",
            "Regenerate MCP tool defs + topics for the newly-authored resource",
            "Warn if MCP regeneration failed (non-fatal)",
            "scripts/gen_mcp_tools.py",
            "_mcp_gen_script",
        ),
    }
    for label, (
        inspect_name,
        command_name,
        warning_name,
        relative_path,
        result_name,
    ) in expected.items():
        inspect_task = by_name[inspect_name]
        assert inspect_task["ansible.builtin.stat"]["path"].endswith(relative_path)
        assert inspect_task["register"] == result_name
        assert inspect_task["changed_when"] is False

        for task_name in (command_name, warning_name):
            conditions = " ".join(by_name[task_name]["when"])
            assert f"{result_name}.stat.exists" in conditions, label
            assert f"{result_name}.stat.isreg" in conditions, label

    readme = (_IMPLEMENT_CHANGE_ROOT / "README.md").read_text()
    assert "forum.ansible.com" in readme
    assert "ansible.builtin.stat" in readme
