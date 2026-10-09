"""Governance role executables must fail closed on module utility errors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = ROOT / "collections/ansible_collections/general_ludd/governance/roles"
EXECUTABLE_ROLES = (
    "civic_service_finder",
    "conflicts_treaties_lookup",
    "decision_maker_lookup",
    "info_classification_check",
    "lookup_governing_body",
    "navigate_borders",
    "tax_currency_info",
)


@pytest.mark.parametrize("role_name", EXECUTABLE_ROLES)
def test_module_utility_execution_propagates_failures(role_name: str) -> None:
    tasks: list[dict[str, Any]] = yaml.safe_load(
        (ROLE_ROOT / role_name / "tasks/main.yml").read_text(encoding="utf-8")
    )
    delegates = [task for task in tasks if "ansible.builtin.include_role" in task]

    assert len(delegates) == 1
    delegate = delegates[0]
    assert delegate["ansible.builtin.include_role"]["name"] == (
        "general_ludd.governance.module_util_lookup"
    )
    assert delegate["vars"] == {"governance_module_util_profile": role_name}
    assert all(task.get("failed_when") is not False for task in tasks)
    assert all(task.get("ignore_errors") is not True for task in tasks)


def test_shared_module_utility_role_is_bounded_and_check_mode_safe() -> None:
    tasks: list[dict[str, Any]] = yaml.safe_load(
        (ROLE_ROOT / "module_util_lookup/tasks/main.yml").read_text(encoding="utf-8")
    )
    executable = [task for task in tasks if task.get("name") == "Run governance module utility"]

    assert len(executable) == 1
    task = executable[0]
    assert task["ansible.builtin.command"] == {
        "argv": "{{ _governance_module_util_request.argv }}"
    }
    assert task.get("changed_when") is False
    assert task.get("failed_when") is not False
    assert task.get("ignore_errors") is not True
    assert task.get("when") == "not ansible_check_mode"

    install_task = next(
        item
        for item in tasks
        if item.get("name") == "Install governance module utility"
    )
    assert install_task["loop"] == (
        "{{ [_governance_module_util_request.filename] "
        "+ _governance_module_util_request.support_filenames }}"
    )
    assert "{{ item }}" in install_task["ansible.builtin.copy"]["src"]
    assert "{{ item }}" in install_task["ansible.builtin.copy"]["dest"]

    validation_task = next(
        item
        for item in tasks
        if item.get("name") == "Validate governance module utility request"
    )
    validation = validation_task["ansible.builtin.assert"]["that"]
    assert "_governance_module_util_request.argv | length <= 16" in validation
    check_plan = next(
        item
        for item in tasks
        if item.get("name") == "Publish governance module utility check plan"
    )
    assert check_plan["when"] == "ansible_check_mode"
    assert check_plan["ansible.builtin.set_fact"][
        "{{ _governance_module_util_request.result_fact }}"
    ]["planned"] is True
    build_task = next(
        item
        for item in tasks
        if item.get("name") == "Build governance module utility request"
    )
    expression = build_task["ansible.builtin.set_fact"][
        "_governance_module_util_request"
    ]
    assert "general_ludd.governance.governance_lookup_plan(" in expression
    assert "_governance_module_util_variables" in expression
    serialized = yaml.safe_dump(tasks)
    assert "governance_lookup_plan(vars)" not in serialized
    assert "vars[_governance_module_util_request.result_fact]" not in serialized
    assert "ansible.builtin.varnames" in serialized
    assert "ansible.builtin.vars" in serialized
    write_task = next(
        item
        for item in tasks
        if item.get("name") == "Write governance module utility result"
    )
    assert (
        "_governance_module_util_payload"
        in write_task["ansible.builtin.copy"]["content"]
    )
