from __future__ import annotations

from pathlib import Path

import yaml

from general_ludd.schemas.todo import (
    ResourceProfile,
    RiskLevel,
    Todo,
    TodoStatus,
    WorkType,
)


class TestTaskDefinition:
    def test_create_with_defaults(self):
        from general_ludd.schemas.task_definition import TaskDefinition

        td = TaskDefinition(name="my_task")
        assert td.name == "my_task"
        assert td.description == ""
        assert td.target_agent == "build"
        assert td.queue == "core"
        assert td.work_type == "code"
        assert td.priority == 0
        assert td.tags == []
        assert td.dependencies == []
        assert td.acceptance_criteria == []
        assert td.test_commands == []
        assert td.model_profile is None
        assert td.prompt_profile is None
        assert td.resource_profile == "low_resource"
        assert td.risk_level == "low"
        assert td.vars == {}

    def test_create_with_all_fields(self):
        from general_ludd.schemas.task_definition import TaskDefinition

        td = TaskDefinition(
            name="full_task",
            description="Do everything",
            target_agent="test",
            queue="priority",
            work_type="test",
            priority=10,
            tags=["backend", "auth"],
            dependencies=["setup_db"],
            acceptance_criteria=["All tests pass", "No lint errors"],
            test_commands=["make test", "make lint"],
            model_profile="gpt4",
            prompt_profile="senior_dev",
            resource_profile="ai_heavy",
            risk_level="high",
            vars={"foo": "bar", "count": 42},
        )
        assert td.name == "full_task"
        assert td.description == "Do everything"
        assert td.target_agent == "test"
        assert td.queue == "priority"
        assert td.work_type == "test"
        assert td.priority == 10
        assert td.tags == ["backend", "auth"]
        assert td.dependencies == ["setup_db"]
        assert td.acceptance_criteria == ["All tests pass", "No lint errors"]
        assert td.test_commands == ["make test", "make lint"]
        assert td.model_profile == "gpt4"
        assert td.prompt_profile == "senior_dev"
        assert td.resource_profile == "ai_heavy"
        assert td.risk_level == "high"
        assert td.vars == {"foo": "bar", "count": 42}

    def test_to_todo_conversion(self):
        from general_ludd.schemas.task_definition import TaskDefinition

        td = TaskDefinition(
            name="implement_auth",
            description="Add JWT authentication",
            target_agent="build",
            queue="core",
            work_type="code",
            priority=5,
            tags=["auth", "backend"],
            risk_level="high",
            resource_profile="hybrid",
            acceptance_criteria=["Tests pass", "Code reviewed"],
            test_commands=["make test-unit"],
            dependencies=["setup_db"],
            model_profile="gpt4",
            prompt_profile="senior_dev",
        )
        todo = td.to_todo()

        assert isinstance(todo, Todo)
        assert todo.title == "implement_auth"
        assert todo.description == "Add JWT authentication"
        assert todo.assigned_agent == "build"
        assert todo.queue == "core"
        assert todo.work_type == WorkType.CODE
        assert todo.priority == 5
        assert todo.tags == ["auth", "backend"]
        assert todo.risk_level == RiskLevel.HIGH
        assert todo.resource_profile == ResourceProfile.HYBRID
        assert todo.acceptance_criteria == ["Tests pass", "Code reviewed"]
        assert todo.test_commands == ["make test-unit"]
        assert todo.dependencies == ["setup_db"]
        assert todo.model_profile == "gpt4"
        assert todo.prompt_profile == "senior_dev"
        assert todo.status == TodoStatus.BACKLOG

    def test_to_todo_minimal(self):
        from general_ludd.schemas.task_definition import TaskDefinition

        td = TaskDefinition(name="simple")
        todo = td.to_todo()

        assert isinstance(todo, Todo)
        assert todo.title == "simple"
        assert todo.description == ""
        assert todo.assigned_agent == "build"
        assert todo.work_type == WorkType.CODE
        assert todo.risk_level == RiskLevel.LOW
        assert todo.resource_profile == ResourceProfile.LOW_RESOURCE


class TestLoadTaskDefinitions:
    def test_load_from_yaml_file(self, tmp_path: Path):
        from general_ludd.config.task_loader import load_task_definitions

        yaml_content = {
            "tasks": [
                {
                    "name": "implement_feature",
                    "description": "Implement the feature",
                    "target_agent": "build",
                    "dependencies": [],
                    "acceptance_criteria": ["Tests pass", "Code reviewed"],
                },
                {
                    "name": "write_tests",
                    "description": "Write tests for feature",
                    "target_agent": "build",
                    "work_type": "test",
                    "dependencies": ["implement_feature"],
                },
            ]
        }
        f = tmp_path / "tasks.yml"
        f.write_text(yaml.dump(yaml_content))

        result = load_task_definitions(f)
        assert len(result) == 2
        assert result[0].name == "implement_feature"
        assert result[0].acceptance_criteria == ["Tests pass", "Code reviewed"]
        assert result[1].name == "write_tests"
        assert result[1].dependencies == ["implement_feature"]

    def test_load_empty_tasks(self, tmp_path: Path):
        from general_ludd.config.task_loader import load_task_definitions

        f = tmp_path / "empty.yml"
        f.write_text("tasks: []\n")

        result = load_task_definitions(f)
        assert result == []

    def test_load_missing_tasks_key(self, tmp_path: Path):
        from general_ludd.config.task_loader import load_task_definitions

        f = tmp_path / "no_tasks.yml"
        f.write_text("something_else: true\n")

        result = load_task_definitions(f)
        assert result == []

    def test_load_empty_file(self, tmp_path: Path):
        from general_ludd.config.task_loader import load_task_definitions

        f = tmp_path / "blank.yml"
        f.write_text("")

        result = load_task_definitions(f)
        assert result == []

    def test_load_nonexistent_file(self, tmp_path: Path):
        from general_ludd.config.task_loader import load_task_definitions

        result = load_task_definitions(tmp_path / "nonexistent.yml")
        assert result == []


class TestDiscoverTaskDefinitions:
    def test_discovers_task_yml_files(self, tmp_path: Path):
        from general_ludd.config.task_loader import discover_task_definitions

        content = {
            "tasks": [
                {"name": "task_a", "description": "A task"},
            ]
        }

        f1 = tmp_path / "tasks_build.yml"
        f1.write_text(yaml.dump(content))

        content2 = {
            "tasks": [
                {"name": "task_b", "description": "B task"},
            ]
        }
        f2 = tmp_path / "tasks_test.yml"
        f2.write_text(yaml.dump(content2))

        other = tmp_path / "not_a_task.yml"
        other.write_text("tasks:\n  - name: skip_me")

        result = discover_task_definitions(tmp_path)
        names = [td.name for td in result]
        assert "task_a" in names
        assert "task_b" in names
        assert len(result) == 2

    def test_discover_empty_dir(self, tmp_path: Path):
        from general_ludd.config.task_loader import discover_task_definitions

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = discover_task_definitions(empty_dir)
        assert result == []

    def test_discover_multiple_paths(self, tmp_path: Path):
        from general_ludd.config.task_loader import discover_task_definitions

        dir_a = tmp_path / "a"
        dir_a.mkdir()
        dir_b = tmp_path / "b"
        dir_b.mkdir()

        content_a = {"tasks": [{"name": "from_a"}]}
        content_b = {"tasks": [{"name": "from_b"}]}

        (dir_a / "tasks.yml").write_text(yaml.dump(content_a))
        (dir_b / "tasks_extra.yml").write_text(yaml.dump(content_b))

        result = discover_task_definitions(dir_a, dir_b)
        names = [td.name for td in result]
        assert "from_a" in names
        assert "from_b" in names
