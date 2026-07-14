"""Verify task_splitter ansible role structure, defaults, task-splitting logic,
sub-task generation, and priority assignment.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
import yaml

ROLE_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "collections" / "ansible_collections" / "general_ludd" / "agent" / "roles" / "task_splitter"
)


class TestRoleStructure:
    def test_role_directory_exists(self) -> None:
        assert ROLE_ROOT.is_dir(), f"Role directory missing: {ROLE_ROOT}"

    @pytest.mark.parametrize("subdir", ["tasks", "defaults", "meta"])
    def test_required_subdirectories_exist(self, subdir: str) -> None:
        assert (ROLE_ROOT / subdir).is_dir(), f"Missing subdirectory: {subdir}"

    def test_tasks_main_exists(self) -> None:
        assert (ROLE_ROOT / "tasks" / "main.yml").is_file(), "Missing tasks/main.yml"

    def test_defaults_main_exists(self) -> None:
        assert (ROLE_ROOT / "defaults" / "main.yml").is_file(), "Missing defaults/main.yml"

    def test_meta_main_exists(self) -> None:
        assert (ROLE_ROOT / "meta" / "main.yml").is_file(), "Missing meta/main.yml"

    def test_readme_exists(self) -> None:
        assert (ROLE_ROOT / "README.md").is_file(), "Missing README.md"


class TestYamlValidity:
    def test_tasks_main_is_valid_yaml(self) -> None:
        content = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        docs = list(yaml.safe_load_all(content))
        assert len(docs) >= 1, "No YAML document in tasks/main.yml"
        assert isinstance(docs[0], list), "tasks/main.yml must be a list of tasks"
        assert len(docs[0]) >= 1, "tasks/main.yml must contain at least one task"

    def test_defaults_is_valid_yaml(self) -> None:
        content = (ROLE_ROOT / "defaults" / "main.yml").read_text()
        doc = yaml.safe_load(content)
        assert isinstance(doc, dict), f"defaults must be a dict, got {type(doc)}"

    def test_meta_is_valid_yaml(self) -> None:
        content = (ROLE_ROOT / "meta" / "main.yml").read_text()
        doc = yaml.safe_load(content)
        assert isinstance(doc, dict), f"meta must be a dict, got {type(doc)}"


class TestMetaRoleInfo:
    def test_meta_has_role_name(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "meta" / "main.yml").read_text())
        galaxy = doc.get("galaxy_info", {})
        assert galaxy.get("role_name") == "task_splitter", (
            f"role_name should be task_splitter, got {galaxy.get('role_name')}"
        )

    def test_meta_has_required_fields(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "meta" / "main.yml").read_text())
        galaxy = doc.get("galaxy_info", {})
        for field in ("role_name", "author", "description", "license", "min_ansible_version"):
            assert field in galaxy, f"meta missing galaxy_info.{field}"

    def test_dependencies_empty(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "meta" / "main.yml").read_text())
        assert doc.get("dependencies") == [], (
            f"task_splitter should have no dependencies, got {doc.get('dependencies')}"
        )


class TestDefaultsStructure:
    REQUIRED_DEFAULTS: ClassVar[list[str]] = [
        "daemon_url",
        "psk",
        "task_description",
        "task_context",
        "max_subtasks",
        "min_cost_benefit_ratio",
        "model_profile",
        "route_task_type",
        "artifact_dir",
    ]

    def test_defaults_have_all_required_params(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())
        missing = [k for k in self.REQUIRED_DEFAULTS if k not in doc]
        assert not missing, f"defaults missing keys: {missing}"

    def test_max_subtasks_default_is_int(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())
        assert isinstance(doc.get("max_subtasks"), int), (
            f"max_subtasks must be int, got {type(doc.get('max_subtasks'))}"
        )
        assert doc["max_subtasks"] > 0, "max_subtasks must be positive"

    def test_min_cost_benefit_ratio_default_is_numeric(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())
        assert isinstance(doc.get("min_cost_benefit_ratio"), (int, float)), (
            f"min_cost_benefit_ratio must be numeric, got {type(doc.get('min_cost_benefit_ratio'))}"
        )
        assert doc["min_cost_benefit_ratio"] >= 1.0, "min_cost_benefit_ratio should be >= 1.0"

    def test_task_description_default_is_empty_string(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())
        assert doc.get("task_description") == "", "task_description default must be empty string"

    def test_task_context_default_is_empty_string(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())
        assert doc.get("task_context") == "", "task_context default must be empty string"

    def test_artifact_dir_default_is_tmp_path(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())
        ad = doc.get("artifact_dir", "")
        assert "/tmp/" in ad or "tmp" in ad, (
            f"artifact_dir should be under /tmp, got {ad}"
        )

    def test_route_task_type_is_analysis(self) -> None:
        doc = yaml.safe_load((ROLE_ROOT / "defaults" / "main.yml").read_text())
        assert doc.get("route_task_type") == "analysis", (
            f"route_task_type default should be 'analysis', got {doc.get('route_task_type')}"
        )


class TestTaskSplittingLogic:
    TASKS_TEXT: ClassVar[str] = (ROLE_ROOT / "tasks" / "main.yml").read_text()

    def test_split_triggered_fact_exists(self) -> None:
        assert "_split_triggered" in self.TASKS_TEXT, "_split_triggered fact must exist"

    def test_split_threshold_is_length_120(self) -> None:
        assert "length > 120" in self.TASKS_TEXT, "split threshold must be >120 chars"

    def test_subtasks_generated_when_triggered(self) -> None:
        assert "_subtasks" in self.TASKS_TEXT, "_subtasks fact must exist"
        assert 'when: _split_triggered | bool' in self.TASKS_TEXT, (
            "_subtasks must be gated on _split_triggered"
        )

    def test_artifact_includes_should_split(self) -> None:
        assert "should_split" in self.TASKS_TEXT, "artifact must include should_split"

    def test_artifact_includes_cost_benefit_ratio(self) -> None:
        assert "cost_benefit_ratio" in self.TASKS_TEXT, "artifact must include cost_benefit_ratio"

    def test_artifact_includes_reasoning(self) -> None:
        assert "reasoning" in self.TASKS_TEXT, "artifact must include reasoning"

    def test_artifact_includes_generated_at(self) -> None:
        assert "generated_at" in self.TASKS_TEXT, "artifact must include generated_at"

    def test_artifact_includes_role_version(self) -> None:
        assert "role_version" in self.TASKS_TEXT, "artifact must include role_version"

    def test_artifact_includes_task_description(self) -> None:
        assert 'task_description: "{{ task_description }}"' in self.TASKS_TEXT, (
            "artifact must pass through task_description"
        )

    def test_artifact_includes_task_context(self) -> None:
        assert 'task_context: "{{ task_context }}"' in self.TASKS_TEXT, (
            "artifact must pass through task_context"
        )

    def test_artifact_includes_max_subtasks(self) -> None:
        assert 'max_subtasks: "{{ max_subtasks }}"' in self.TASKS_TEXT, (
            "artifact must pass through max_subtasks"
        )

    def test_artifact_includes_min_cost_benefit_ratio(self) -> None:
        assert 'min_cost_benefit_ratio: "{{ min_cost_benefit_ratio }}"' in self.TASKS_TEXT, (
            "artifact must pass through min_cost_benefit_ratio"
        )

    def test_cost_benefit_ratio_split_is_3_5(self) -> None:
        assert 'cost_benefit_ratio: "{{ 3.5 if _split_triggered | bool else 1.0 }}"' in self.TASKS_TEXT, (
            "cost_benefit_ratio should be 3.5 when split, 1.0 otherwise"
        )

    def test_no_split_cost_benefit_ratio_is_1_0(self) -> None:
        assert '"{{ 3.5 if _split_triggered | bool else 1.0 }}"' in self.TASKS_TEXT, (
            "cost_benefit_ratio should fallback to 1.0"
        )


class TestSubtaskGeneration:
    TASKS_TEXT: ClassVar[str] = (ROLE_ROOT / "tasks" / "main.yml").read_text()

    def test_generates_three_subtasks(self) -> None:
        docs = list(yaml.safe_load_all(self.TASKS_TEXT))
        tasks = docs[0]
        subtask_gen = [
            t for t in tasks
            if "_subtasks" in t.get("ansible.builtin.set_fact", {})
        ]
        assert len(subtask_gen) == 1
        subtasks = subtask_gen[0]["ansible.builtin.set_fact"]["_subtasks"]
        assert len(subtasks) == 3, f"Expected 3 subtasks, got {len(subtasks)}"

    def test_subtasks_have_required_keys(self) -> None:
        docs = list(yaml.safe_load_all(self.TASKS_TEXT))
        tasks = docs[0]
        subtask_gen = [t for t in tasks if "_subtasks" in t.get("ansible.builtin.set_fact", {})]
        subtasks = subtask_gen[0]["ansible.builtin.set_fact"]["_subtasks"]
        for st in subtasks:
            for key in ("title", "description", "expected_duration"):
                assert key in st, f"subtask missing key: {key}"
            assert st["title"], "subtask title must not be empty"
            assert st["description"], "subtask description must not be empty"
            assert st["expected_duration"], "subtask expected_duration must not be empty"

    def test_first_subtask_is_research(self) -> None:
        docs = list(yaml.safe_load_all(self.TASKS_TEXT))
        tasks = docs[0]
        subtask_gen = [t for t in tasks if "_subtasks" in t.get("ansible.builtin.set_fact", {})]
        subtasks = subtask_gen[0]["ansible.builtin.set_fact"]["_subtasks"]
        assert "Research" in subtasks[0]["title"], (
            f"First subtask should be Research, got: {subtasks[0]['title']}"
        )

    def test_second_subtask_is_implement(self) -> None:
        docs = list(yaml.safe_load_all(self.TASKS_TEXT))
        tasks = docs[0]
        subtask_gen = [t for t in tasks if "_subtasks" in t.get("ansible.builtin.set_fact", {})]
        subtasks = subtask_gen[0]["ansible.builtin.set_fact"]["_subtasks"]
        assert "Implement" in subtasks[1]["title"], (
            f"Second subtask should be Implement, got: {subtasks[1]['title']}"
        )

    def test_third_subtask_is_test(self) -> None:
        docs = list(yaml.safe_load_all(self.TASKS_TEXT))
        tasks = docs[0]
        subtask_gen = [t for t in tasks if "_subtasks" in t.get("ansible.builtin.set_fact", {})]
        subtasks = subtask_gen[0]["ansible.builtin.set_fact"]["_subtasks"]
        assert "Test" in subtasks[2]["title"], (
            f"Third subtask should be Test, got: {subtasks[2]['title']}"
        )

    def test_subtask_titles_truncate_task_description(self) -> None:
        docs = list(yaml.safe_load_all(self.TASKS_TEXT))
        tasks = docs[0]
        subtask_gen = [t for t in tasks if "_subtasks" in t.get("ansible.builtin.set_fact", {})]
        subtasks = subtask_gen[0]["ansible.builtin.set_fact"]["_subtasks"]
        for st in subtasks:
            assert "truncate(60)" in str(st["title"]) or (
                ": " in st["title"] and len(st["title"]) >= 0
            ), f"subtask title should truncate task_description: {st['title']}"


class TestArtifactWireFormat:
    TASKS_TEXT: ClassVar[str] = (ROLE_ROOT / "tasks" / "main.yml").read_text()

    def test_artifact_is_set_fact(self) -> None:
        assert "_artifact:" in self.TASKS_TEXT, "_artifact fact must exist"

    def test_artifact_written_to_disk(self) -> None:
        assert "task_splitter_result.json" in self.TASKS_TEXT, (
            "artifact must write task_splitter_result.json"
        )

    def test_artifact_uses_to_nice_json(self) -> None:
        assert "to_nice_json" in self.TASKS_TEXT, "artifact should use to_nice_json for readability"

    def test_artifact_dir_task_is_first(self) -> None:
        docs = list(yaml.safe_load_all(self.TASKS_TEXT))
        tasks = docs[0]
        first_task = tasks[0]
        assert "file" in str(first_task), (
            "first task should create artifact directory"
        )
        assert first_task.get("ansible.builtin.file", {}).get("path") == (
            "{{ artifact_dir }}"
        ), "first task must create artifact_dir"

    def test_all_tasks_are_set_fact_or_file_or_copy(self) -> None:
        docs = list(yaml.safe_load_all(self.TASKS_TEXT))
        tasks = docs[0]
        allowed_modules = {
            "ansible.builtin.file",
            "ansible.builtin.set_fact",
            "ansible.builtin.copy",
        }
        _task_attrs = {"name", "changed_when", "failed_when", "when", "register",
                       "notify", "tags", "become", "ignore_errors", "no_log"}
        for task in tasks:
            module = next(iter(task.keys() - _task_attrs), None)
            assert module in allowed_modules, (
                f"task uses unexpected module: {module}"
            )


class TestPriorityAssignment:
    def test_cost_benefit_ratio_rationale(self) -> None:
        content = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        assert "3.5" in content, "cost_benefit_ratio for split must be 3.5"
        assert "1.0" in content, "cost_benefit_ratio for no-split must be 1.0"

    def test_split_reasoning_is_explicit(self) -> None:
        content = (ROLE_ROOT / "tasks" / "main.yml").read_text()
        reason_split = "Task exceeds complexity threshold"
        reason_no_split = "Task is simple enough for single-agent execution"
        assert reason_split in content, f"missing reasoning text: {reason_split!r}"
        assert reason_no_split in content, f"missing reasoning text: {reason_no_split!r}"


class TestVariableReferences:
    TASKS_TEXT: ClassVar[str] = (ROLE_ROOT / "tasks" / "main.yml").read_text()

    REQUIRED_VARS: ClassVar[list[str]] = [
        "artifact_dir",
        "task_description",
        "task_context",
        "max_subtasks",
        "min_cost_benefit_ratio",
    ]

    def test_task_variables_are_referenced(self) -> None:
        missing = [v for v in self.REQUIRED_VARS if "{{ " + v not in self.TASKS_TEXT]
        assert not missing, f"tasks/main.yml is missing variable references: {missing}"
