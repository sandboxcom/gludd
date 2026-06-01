from __future__ import annotations

from datetime import datetime

from general_ludd.planning.artifact import PlanArtifact
from general_ludd.schemas.todo import Todo


class TestPlanArtifactCreate:
    def test_plan_artifact_create_with_minimal_fields(self):
        artifact = PlanArtifact(todo_id="TODO-001", content="Plan content here")
        assert artifact.todo_id == "TODO-001"
        assert artifact.content == "Plan content here"
        assert isinstance(artifact.created_at, datetime)
        assert artifact.created_at.tzinfo is not None

    def test_plan_artifact_create_with_all_fields(self):
        artifact = PlanArtifact(
            todo_id="TODO-002",
            title="Implement feature X",
            description="Detailed plan for feature X",
            target_files=["src/foo.py", "tests/test_foo.py"],
            contracts=["def foo() -> str", "class FooBar"],
            dependencies=["TODO-003", "TODO-004"],
            notes="Watch out for edge cases",
            content="Full plan content",
        )
        assert artifact.todo_id == "TODO-002"
        assert artifact.title == "Implement feature X"
        assert artifact.description == "Detailed plan for feature X"
        assert artifact.target_files == ["src/foo.py", "tests/test_foo.py"]
        assert artifact.contracts == ["def foo() -> str", "class FooBar"]
        assert artifact.dependencies == ["TODO-003", "TODO-004"]
        assert artifact.notes == "Watch out for edge cases"


class TestPlanArtifactToMarkdown:
    def test_renders_artifact_as_markdown_section(self):
        artifact = PlanArtifact(
            todo_id="TODO-001",
            title="Fix login bug",
            description="Login fails on empty password",
            target_files=["src/auth.py"],
            contracts=["def login(user, pw) -> bool"],
            dependencies=["TODO-010"],
            notes="Regression from PR #42",
            content="Step 1: Add validation\nStep 2: Fix handler",
        )
        md = artifact.to_markdown()
        assert "## Plan: Fix login bug" in md
        assert "**Todo ID:** TODO-001" in md
        assert "Login fails on empty password" in md
        assert "src/auth.py" in md
        assert "def login(user, pw) -> bool" in md
        assert "TODO-010" in md
        assert "Regression from PR #42" in md
        assert "Step 1: Add validation" in md

    def test_minimal_artifact_markdown(self):
        artifact = PlanArtifact(todo_id="TODO-005", content="Do the thing")
        md = artifact.to_markdown()
        assert "**Todo ID:** TODO-005" in md
        assert "Do the thing" in md


class TestPlanArtifactFromTodo:
    def test_from_todo_extracts_relevant_fields(self):
        todo = Todo(
            title="Add caching layer",
            description="Implement Redis caching",
            tags=["performance", "redis"],
            test_commands=["make test-unit"],
        )
        artifact = PlanArtifact.from_todo(todo)
        assert artifact.todo_id == todo.todo_id
        assert artifact.title == "Add caching layer"
        assert artifact.description == "Implement Redis caching"
        assert "performance" in artifact.notes or "redis" in artifact.notes
        assert isinstance(artifact.created_at, datetime)

    def test_from_todo_preserves_test_commands(self):
        todo = Todo(
            title="Fix tests",
            test_commands=["make test-unit", "make test-e2e"],
        )
        artifact = PlanArtifact.from_todo(todo)
        assert "make test-unit" in artifact.contracts or "make test-unit" in str(artifact.to_markdown())


class TestPlanArtifactSerializeDeserialize:
    def test_to_dict_roundtrip(self):
        artifact = PlanArtifact(
            todo_id="TODO-007",
            title="Refactor module",
            description="Clean up module structure",
            target_files=["src/mod.py"],
            contracts=["def process() -> None"],
            dependencies=["TODO-008"],
            notes="No breaking changes",
            content="Refactor plan",
        )
        data = artifact.to_dict()
        assert isinstance(data, dict)
        assert data["todo_id"] == "TODO-007"
        assert data["title"] == "Refactor module"

        restored = PlanArtifact.from_dict(data)
        assert restored.todo_id == artifact.todo_id
        assert restored.title == artifact.title
        assert restored.description == artifact.description
        assert restored.target_files == artifact.target_files
        assert restored.contracts == artifact.contracts
        assert restored.dependencies == artifact.dependencies
        assert restored.notes == artifact.notes
        assert restored.content == artifact.content

    def test_roundtrip_preserves_timestamp(self):
        artifact = PlanArtifact(todo_id="TODO-009", content="Test timestamp")
        data = artifact.to_dict()
        restored = PlanArtifact.from_dict(data)
        assert restored.created_at == artifact.created_at
