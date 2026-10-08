"""Behavioral structure tests for git-release service roles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "collections/ansible_collections/general_ludd/git_release/roles"
ROLE_NAMES = (
    "artifact_build",
    "artifact_verify",
    "conflict_resolve",
    "deploy_orchestrate",
    "helper_build",
    "helper_discover",
    "helper_select",
    "pipeline_triage",
    "release_plan",
    "release_recover",
    "work_recover",
)


def _load(path: Path) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def test_every_service_role_invokes_shared_typed_operation_role() -> None:
    for role in ROLE_NAMES:
        source = (ROLES / role / "tasks/main.yml").read_text(encoding="utf-8")
        tasks = _load(ROLES / role / "tasks/main.yml")
        include = next(task for task in tasks if "ansible.builtin.include_role" in task)
        assert include["ansible.builtin.include_role"]["name"] == (
            "general_ludd.git_release.service_request"
        )
        assert include["vars"]["git_release_service_operation"] == role
        assert "ansible.builtin.debug" not in source


def test_shared_role_executes_module_and_never_swallows_failure() -> None:
    tasks = _load(ROLES / "service_request/tasks/main.yml")
    request = next(task for task in tasks if task["name"] == "Execute git-release operation")

    assert "general_ludd.git_release.git_release_operation" in request
    assert request.get("ignore_errors") is None
    assert request.get("failed_when") is None
