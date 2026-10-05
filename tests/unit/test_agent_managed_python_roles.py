"""Managed-host interpreter boundary tests for agent roles."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROLES = ROOT / "collections/ansible_collections/general_ludd/agent/roles"
PYTHON_ROLES = (
    "agent_floor_check",
    "backlog_guard_audit",
    "ci_annotations_poll",
    "feature_evidence_audit",
    "generate_status_table",
    "model_benchmark",
    "model_quantize",
    "model_serve",
    "multitasking_backlog_check",
    "scan_conflict_markers",
    "service_login",
    "test_matrix",
    "token_window_monitor",
    "type_safety_audit",
    "ui_ux_analyst",
)


def test_managed_python_preflight_is_content_addressed_and_rollback_safe() -> None:
    tasks = (AGENT_ROLES / "managed_python_preflight/tasks/main.yml").read_text(
        encoding="utf-8"
    )

    assert "managed_python_preflight_interpreter" in tasks
    assert "managed_python_preflight_lock_sha256" in tasks
    assert "/opt/gludd/role-envs/" in tasks
    assert "managed_python_preflight_previous_interpreter" in tasks
    assert "ansible.builtin.stat" in tasks
    assert "ansible.builtin.assert" in tasks
    assert "rollback" in tasks.lower()
    assert "ansible.builtin.command" not in tasks
    assert "ansible.builtin.shell" not in tasks


@pytest.mark.parametrize("role_name", PYTHON_ROLES)
def test_python_role_uses_private_shared_preflight(role_name: str) -> None:
    tasks = (AGENT_ROLES / role_name / "tasks/main.yml").read_text(encoding="utf-8")

    assert "general_ludd.agent.managed_python_preflight" in tasks
    assert "tasks_from: main" in tasks
    assert "public: false" in tasks
    assert "{{ ansible_python_interpreter }}" in tasks
    assert not re.search(
        r"(?:^|[\s:'\"=])(?:/usr/bin/python3?|/usr/local/bin/python3?|python3?|py)(?:\s|$)",
        tasks,
        flags=re.MULTILINE,
    )


def test_git_automation_uses_authenticated_daemon_module() -> None:
    for task_name in ("ci_cancel.yml", "ci_verdict.yml"):
        tasks = (AGENT_ROLES / "git_automation/tasks" / task_name).read_text(
            encoding="utf-8"
        )
        assert "general_ludd.agent.gludd_git" in tasks
        assert "daemon_url:" in tasks
        assert "psk:" in tasks
        assert "ansible.builtin.command" not in tasks
        assert "from general_ludd" not in tasks


def test_model_serve_never_mutates_content_addressed_environment() -> None:
    tasks = (AGENT_ROLES / "model_serve/tasks/main.yml").read_text(encoding="utf-8")

    assert "ansible.builtin.pip" not in tasks
    assert "Install llama-cpp-python" not in tasks
    assert "Install vllm" not in tasks
    assert "importlib.util.find_spec" in tasks
    assert "Managed inference dependency is absent" in tasks
