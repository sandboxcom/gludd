"""Unit tests for config/task_loader."""

from __future__ import annotations

from general_ludd.config.task_loader import discover_task_definitions, load_task_definitions
from general_ludd.schemas.task_definition import TaskDefinition


class TestLoadTaskDefinitions:
    def test_non_existent_file_returns_empty(self):
        result = load_task_definitions("/nonexistent/path/tasks.yml")
        assert result == []

    def test_empty_yaml_returns_empty(self, tmp_path):
        path = tmp_path / "tasks.yml"
        path.write_text("")

        result = load_task_definitions(str(path))
        assert result == []

    def test_no_tasks_key_returns_empty(self, tmp_path):
        path = tmp_path / "tasks.yml"
        path.write_text("other_key: value\n")

        result = load_task_definitions(str(path))
        assert result == []

    def test_empty_tasks_list_returns_empty(self, tmp_path):
        path = tmp_path / "tasks.yml"
        path.write_text("tasks: []\n")

        result = load_task_definitions(str(path))
        assert result == []

    def test_parses_task_definitions(self, tmp_path):
        path = tmp_path / "tasks.yml"
        path.write_text(
            "tasks:\n"
            "  - name: Test Task\n"
            "    description: A test\n"
            "    target_agent: build\n"
            "    priority: 10\n"
        )

        result = load_task_definitions(str(path))
        assert len(result) == 1
        assert result[0].name == "Test Task"
        assert result[0].priority == 10

    def test_parses_multiple_definitions(self, tmp_path):
        path = tmp_path / "tasks.yml"
        path.write_text(
            "tasks:\n"
            "  - name: First\n"
            "    description: First task\n"
            "    priority: 10\n"
            "  - name: Second\n"
            "    description: Second task\n"
            "    priority: 20\n"
        )

        result = load_task_definitions(str(path))
        assert len(result) == 2
        assert result[0].name == "First"
        assert result[1].name == "Second"

    def test_returns_list_of_task_definitions(self, tmp_path):
        path = tmp_path / "tasks.yml"
        path.write_text(
            "tasks:\n"
            "  - name: T\n"
            "    description: D\n"
            "    priority: 5\n"
        )

        result = load_task_definitions(str(path))
        assert isinstance(result, list)
        assert all(isinstance(t, TaskDefinition) for t in result)


class TestDiscoverTaskDefinitions:
    def test_non_existent_directory_returns_empty(self):
        result = discover_task_definitions("/nonexistent/dir")
        assert result == []

    def test_empty_directory_returns_empty(self, tmp_path):
        result = discover_task_definitions(tmp_path)
        assert result == []

    def test_no_matching_files_returns_empty(self, tmp_path):
        (tmp_path / "other_file.yml").write_text("")
        result = discover_task_definitions(tmp_path)
        assert result == []

    def test_finds_task_files(self, tmp_path):
        (tmp_path / "task_backup.yml").write_text(
            "tasks:\n"
            "  - name: B\n"
            "    description: D\n"
            "    priority: 5\n"
        )

        result = discover_task_definitions(tmp_path)
        assert len(result) == 1
        assert result[0].name == "B"

    def test_finds_multiple_task_files(self, tmp_path):
        (tmp_path / "task_a.yml").write_text(
            "tasks:\n"
            "  - name: A\n"
            "    description: D\n"
            "    priority: 5\n"
        )
        (tmp_path / "task_b.yml").write_text(
            "tasks:\n"
            "  - name: B\n"
            "    description: D\n"
            "    priority: 10\n"
        )

        result = discover_task_definitions(tmp_path)
        assert len(result) == 2
        assert {t.name for t in result} == {"A", "B"}

    def test_skips_non_matching_files(self, tmp_path):
        (tmp_path / "task_backup.yml").write_text(
            "tasks:\n"
            "  - name: B\n"
            "    description: D\n"
            "    priority: 5\n"
        )
        (tmp_path / "config.yml").write_text("some: data\n")
        (tmp_path / "notes.md").write_text("some notes\n")

        result = discover_task_definitions(tmp_path)
        assert len(result) == 1

    def test_multiple_search_paths(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        (dir_a / "task_a.yml").write_text(
            "tasks:\n"
            "  - name: A\n"
            "    description: D\n"
            "    priority: 5\n"
        )

        dir_b = tmp_path / "b"
        dir_b.mkdir()
        (dir_b / "task_b.yml").write_text(
            "tasks:\n"
            "  - name: B\n"
            "    description: D\n"
            "    priority: 10\n"
        )

        result = discover_task_definitions(dir_a, dir_b)
        assert len(result) == 2
        assert {t.name for t in result} == {"A", "B"}
