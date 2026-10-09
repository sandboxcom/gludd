"""Behavioral structure for every materials Ansible role."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "collections/ansible_collections/general_ludd/materials/roles"
ROLE_NAMES = (
    "requirements_capture",
    "material_select",
    "polymer_process_plan",
    "metal_forming_plan",
    "strength_assess",
    "joining_plan",
    "welding_plan",
    "machining_plan",
    "additive_plan",
    "textile_plan",
    "molding_plan",
    "multiphysics_model",
    "tolerance_model",
    "failure_analyze",
    "manufacturing_plan",
    "inspection_plan",
)


def _load(path: Path) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def test_materials_roles_use_their_owned_execution_boundary() -> None:
    for role in ROLE_NAMES:
        source = (ROLES / role / "tasks/main.yml").read_text(encoding="utf-8")
        tasks = _load(ROLES / role / "tasks/main.yml")
        if role == "tolerance_model":
            native = next(
                task
                for task in tasks
                if "general_ludd.materials.tolerance_model" in task
            )
            assert native.get("ignore_errors") is None
            assert "service_request" not in source
            assert "ansible.builtin.uri" not in source
            continue
        include = next(task for task in tasks if "ansible.builtin.include_role" in task)
        assert include["ansible.builtin.include_role"]["name"] == (
            "general_ludd.materials.service_request"
        )
        assert include["vars"]["materials_service_operation"] == role
        assert "ansible.builtin.debug" not in source
        assert "module_util" not in source


def test_shared_role_executes_module_and_never_swallows_failure() -> None:
    tasks = _load(ROLES / "service_request/tasks/main.yml")
    request = next(task for task in tasks if task["name"] == "Execute materials operation")

    assert "general_ludd.materials.materials_operation" in request
    assert request.get("ignore_errors") is None
    assert request.get("failed_when") is None
