"""Integration tests for the ``general_ludd.agent.task_splitter`` role.

Verifies the full flow: invoke role -> set_fact-based splitting logic -> writes
artifact -> parsed JSON has correct ``should_split``, ``subtasks``, and metadata.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from general_ludd.ansible.runner import AnsibleRunnerAdapter


def _has_ansible() -> bool:
    return shutil.which("ansible-playbook") is not None


pytestmark = pytest.mark.skipif(
    not _has_ansible(), reason="ansible-playbook not installed"
)


_PLAYBOOK_YML = """---
- name: Test task_splitter role
  hosts: localhost
  connection: local
  gather_facts: false
  roles:
    - role: general_ludd.agent.task_splitter
"""


def _run_role(
    tmp_path: Path,
    task_description: str,
    task_context: str = "",
    max_subtasks: int = 7,
    min_cost_benefit_ratio: float = 2.0,
) -> dict:
    playbook = tmp_path / "task_splitter_test.yml"
    playbook.write_text(_PLAYBOOK_YML)
    adapter = AnsibleRunnerAdapter(project_root=str(tmp_path))
    name = "task_splitter_test.yml"
    adapter.register_playbook(name, str(playbook))
    return adapter.run_playbook(
        name,
        extravars={
            "task_description": task_description,
            "task_context": task_context,
            "max_subtasks": max_subtasks,
            "min_cost_benefit_ratio": min_cost_benefit_ratio,
            "artifact_dir": str(tmp_path / "artifacts"),
        },
    )


def _read_artifact(tmp_path: Path) -> dict:
    return json.loads(
        (tmp_path / "artifacts" / "task_splitter_result.json").read_text()
    )


class TestTaskSplitterRole:
    def test_role_runs_and_writes_artifact(self, tmp_path: Path) -> None:
        result = _run_role(
            tmp_path,
            task_description="A" * 130,
        )
        assert result.get("rc", 1) == 0, result
        assert (tmp_path / "artifacts" / "task_splitter_result.json").is_file()

    def test_long_task_triggers_should_split_true(self, tmp_path: Path) -> None:
        result = _run_role(tmp_path, task_description="X" * 130)
        assert result.get("rc", 1) == 0, result
        data = _read_artifact(tmp_path)
        assert data["should_split"] is True
        assert len(data["subtasks"]) == 3
        assert "complexity threshold" in data["reasoning"]
        assert data["cost_benefit_ratio"] == 3.5

    def test_short_task_does_not_split(self, tmp_path: Path) -> None:
        result = _run_role(tmp_path, task_description="Install linter")
        assert result.get("rc", 1) == 0, result
        data = _read_artifact(tmp_path)
        assert data["should_split"] is False
        assert data["subtasks"] == []
        assert "simple enough" in data["reasoning"]
        assert data["cost_benefit_ratio"] == 1.0

    def test_artifact_contains_all_required_fields(self, tmp_path: Path) -> None:
        result = _run_role(tmp_path, task_description="Test task for field completeness")
        assert result.get("rc", 1) == 0, result
        data = _read_artifact(tmp_path)
        required = [
            "task_description",
            "task_context",
            "max_subtasks",
            "min_cost_benefit_ratio",
            "should_split",
            "reasoning",
            "cost_benefit_ratio",
            "subtasks",
            "generated_at",
            "role_version",
        ]
        for field in required:
            assert field in data, f"Missing required field: {field}"

    def test_subtasks_have_correct_structure(self, tmp_path: Path) -> None:
        result = _run_role(tmp_path, task_description="Y" * 130)
        assert result.get("rc", 1) == 0, result
        data = _read_artifact(tmp_path)
        for subtask in data["subtasks"]:
            assert "title" in subtask
            assert "description" in subtask
            assert "expected_duration" in subtask

    def test_generated_at_is_iso8601_string(self, tmp_path: Path) -> None:
        result = _run_role(tmp_path, task_description="Check timestamp format")
        assert result.get("rc", 1) == 0, result
        data = _read_artifact(tmp_path)
        ts = data["generated_at"]
        assert isinstance(ts, str) and len(ts) > 0
        assert "T" in ts

    def test_role_version_is_expected(self, tmp_path: Path) -> None:
        result = _run_role(tmp_path, task_description="Check role version")
        assert result.get("rc", 1) == 0, result
        data = _read_artifact(tmp_path)
        assert data["role_version"] == "1.0.0"

    def test_task_context_is_included(self, tmp_path: Path) -> None:
        result = _run_role(
            tmp_path,
            task_description="Test context inclusion",
            task_context="Background: existing tests pass",
        )
        assert result.get("rc", 1) == 0, result
        data = _read_artifact(tmp_path)
        assert data["task_context"] == "Background: existing tests pass"

    def test_max_subtasks_is_included(self, tmp_path: Path) -> None:
        result = _run_role(
            tmp_path,
            task_description="Test max_subtasks passthrough",
            max_subtasks=3,
        )
        assert result.get("rc", 1) == 0, result
        data = _read_artifact(tmp_path)
        assert data["max_subtasks"] == 3
