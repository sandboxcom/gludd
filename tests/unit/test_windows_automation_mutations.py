"""Behavioral structure for Windows automation mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COLLECTION = ROOT / "collections/ansible_collections/general_ludd/os_expert"
ROLE = COLLECTION / "roles/windows_automation"


def _tasks() -> list[dict[str, Any]]:
    loaded = yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def _task(name: str) -> dict[str, Any]:
    return next(task for task in _tasks() if task["name"] == name)


def test_scheduled_task_management_uses_idempotent_collection_module() -> None:
    validation = _task("Validate scheduled task mutation inputs")
    mutation = _task("Manage configured Windows scheduled task")

    assert "ansible.builtin.assert" in validation
    assert mutation["community.windows.win_scheduled_task"] == {
        "name": "{{ scheduled_task_name }}",
        "path": "{{ scheduled_task_path }}",
        "actions": "{{ scheduled_task_actions }}",
        "triggers": "{{ scheduled_task_triggers }}",
        "username": "{{ scheduled_task_username }}",
        "logon_type": "{{ scheduled_task_logon_type }}",
        "run_level": "{{ scheduled_task_run_level }}",
        "enabled": "{{ scheduled_task_enabled }}",
        "state": "{{ scheduled_task_state }}",
    }
    assert mutation.get("ignore_errors") is None
    assert mutation["when"] == [
        "ansible_os_family == 'Windows'",
        "manage_schtasks | bool",
    ]


def test_unattended_install_management_uses_idempotent_template_module() -> None:
    validation = _task("Validate unattended install mutation inputs")
    mutation = _task("Render configured Windows unattended install answer file")

    assert "ansible.builtin.assert" in validation
    assert mutation["ansible.windows.win_template"] == {
        "src": "{{ unattended_install_template_src }}",
        "dest": "{{ unattended_install_dest }}",
        "backup": "{{ unattended_install_backup }}",
    }
    assert mutation["no_log"] is True
    assert mutation.get("ignore_errors") is None


def test_mutation_paths_do_not_report_placeholder_success_or_swallow_failures() -> None:
    source = (ROLE / "tasks/main.yml").read_text(encoding="utf-8")
    mutating = [task for task in _tasks() if task["name"].startswith("Manage ")]

    assert "placeholder" not in source.casefold()
    assert all("ansible.builtin.debug" not in task for task in mutating)
    assert all(task.get("ignore_errors") is not True for task in mutating)


def test_windows_module_dependencies_are_declared() -> None:
    galaxy = yaml.safe_load((COLLECTION / "galaxy.yml").read_text(encoding="utf-8"))

    assert "ansible.windows" in galaxy["dependencies"]
    assert "community.windows" in galaxy["dependencies"]
