"""Behavioral structure for chemistry Ansible service roles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "collections/ansible_collections/general_ludd/chemistry/roles"
ROLE_TASKS = {
    "analytical_validate": "analytical",
    "cheminformatics": "compute",
    "chemistry_promote": "process",
    "chemistry_refresh": "research",
    "chemistry_research": "research",
    "electrochemistry": "electrochemistry",
    "molecular_simulation": "compute",
    "process_scaleup": "process",
    "property_lookup": "property",
    "protocol_draft": "protocol",
    "quantum_workflow": "compute",
    "spectra_analyze": "spectra",
    "thermo_kinetics": "compute",
    "tool_discover": "research",
}


def _load(path: Path) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def test_daemon_service_roles_invoke_the_shared_typed_operation_role() -> None:
    for role, task_name in ROLE_TASKS.items():
        source = (ROLES / role / "tasks/main.yml").read_text(encoding="utf-8")
        tasks = _load(ROLES / role / "tasks/main.yml")
        assert len(tasks) == 1
        assert "ansible.builtin.debug" not in source
        assert tasks[0]["ansible.builtin.include_role"]["name"] == (
            "general_ludd.chemistry.service_request"
        )
        assert tasks[0]["vars"] == {
            "chemistry_service_role": role,
            "chemistry_service_task": task_name,
        }


def test_inventory_role_invokes_collection_native_lot_admission() -> None:
    source = (ROLES / "inventory_check/tasks/main.yml").read_text(encoding="utf-8")
    tasks = _load(ROLES / "inventory_check/tasks/main.yml")

    assert len(tasks) == 2
    assert "general_ludd.chemistry.service_request" not in source
    assert "daemon_url" not in source
    assert "general_ludd.chemistry.chemical_lot_admission" in tasks[0]
    assert tasks[0].get("ignore_errors") is None
    assert tasks[1]["ansible.builtin.set_fact"] == {
        "inventory_check_result": "{{ _inventory_check_admission.result }}"
    }
    assert tasks[1]["changed_when"] is False


def test_shared_role_uses_existing_typed_module_and_publishes_check_plan() -> None:
    tasks = _load(ROLES / "service_request/tasks/main.yml")
    request = next(task for task in tasks if task["name"] == "Resolve chemistry request")
    publish = next(task for task in tasks if task["name"] == "Publish chemistry result")

    assert request["general_ludd.chemistry.chemistry_operation"]["operation"] == "route"
    assert request.get("ignore_errors") is None
    assert "ansible_check_mode" in publish["ansible.builtin.set_fact"][
        "chemistry_service_result"
    ]
