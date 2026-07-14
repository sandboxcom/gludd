"""Tests for planning.artifact: PlanArtifact model."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from general_ludd.planning.artifact import PlanArtifact


def _fixed_now() -> datetime:
    return datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)


class TestPlanArtifactConstruction:
    def test_minimal_construction(self):
        pa = PlanArtifact(todo_id="todo-1")
        assert pa.todo_id == "todo-1"
        assert pa.title == ""
        assert pa.description == ""
        assert pa.target_files == []
        assert pa.contracts == []
        assert pa.dependencies == []
        assert pa.notes == ""
        assert pa.content == ""
        assert pa.created_at is not None

    def test_full_construction(self):
        now = _fixed_now()
        pa = PlanArtifact(
            todo_id="todo-1",
            title="My Plan",
            description="A test plan",
            target_files=["src/a.py"],
            contracts=["contract-1"],
            dependencies=["dep-1"],
            notes="important",
            content="# Content",
            created_at=now,
        )
        assert pa.todo_id == "todo-1"
        assert pa.title == "My Plan"
        assert pa.target_files == ["src/a.py"]
        assert pa.contracts == ["contract-1"]
        assert pa.dependencies == ["dep-1"]
        assert pa.content == "# Content"
        assert pa.created_at == now

    def test_todo_id_empty_raises(self):
        with pytest.raises(ValidationError):
            PlanArtifact(todo_id="")

    def test_todo_id_whitespace_raises(self):
        with pytest.raises(ValidationError):
            PlanArtifact(todo_id="   ")

    def test_todo_id_strips_whitespace(self):
        pa = PlanArtifact(todo_id="  todo-1  ")
        assert pa.todo_id == "todo-1"


class TestToMarkdown:
    def test_minimal_markdown(self):
        pa = PlanArtifact(todo_id="todo-1")
        md = pa.to_markdown()
        assert "## Plan: todo-1" in md
        assert "**Todo ID:** todo-1" in md

    def test_markdown_with_title(self):
        pa = PlanArtifact(todo_id="todo-1", title="Great Plan")
        md = pa.to_markdown()
        assert "## Plan: Great Plan" in md

    def test_markdown_with_description(self):
        pa = PlanArtifact(todo_id="t1", description="desc")
        md = pa.to_markdown()
        assert "**Description:** desc" in md

    def test_markdown_with_target_files(self):
        pa = PlanArtifact(todo_id="t1", target_files=["a.py", "b.py"])
        md = pa.to_markdown()
        assert "### Target Files" in md
        assert "- `a.py`" in md
        assert "- `b.py`" in md

    def test_markdown_with_contracts(self):
        pa = PlanArtifact(todo_id="t1", contracts=["c1"])
        md = pa.to_markdown()
        assert "### Contracts" in md
        assert "- `c1`" in md

    def test_markdown_with_dependencies(self):
        pa = PlanArtifact(todo_id="t1", dependencies=["d1"])
        md = pa.to_markdown()
        assert "### Dependencies" in md
        assert "- d1" in md

    def test_markdown_with_notes(self):
        pa = PlanArtifact(todo_id="t1", notes="note")
        md = pa.to_markdown()
        assert "**Notes:** note" in md

    def test_markdown_with_content(self):
        pa = PlanArtifact(todo_id="t1", content="raw content")
        md = pa.to_markdown()
        assert "raw content" in md

    def test_markdown_no_extra_sections_when_empty(self):
        pa = PlanArtifact(todo_id="t1")
        md = pa.to_markdown()
        assert "### Target Files" not in md
        assert "### Contracts" not in md
        assert "### Dependencies" not in md


class TestToDict:
    def test_to_dict_returns_json_serializable(self):
        pa = PlanArtifact(todo_id="t1", created_at=_fixed_now())
        d = pa.to_dict()
        assert d["todo_id"] == "t1"
        assert isinstance(d["created_at"], str)

    def test_to_dict_includes_all_fields(self):
        pa = PlanArtifact(todo_id="t1", title="T")
        d = pa.to_dict()
        for key in ("todo_id", "title", "description", "target_files", "contracts",
                     "dependencies", "notes", "content", "created_at"):
            assert key in d


class TestFromDict:
    def test_from_dict_roundtrip(self):
        original = PlanArtifact(todo_id="t1", title="Test", description="desc")
        d = original.to_dict()
        restored = PlanArtifact.from_dict(d)
        assert restored.todo_id == original.todo_id
        assert restored.title == original.title

    def test_from_dict_parses_created_at_string(self):
        d = {
            "todo_id": "t1",
            "title": "T",
            "created_at": "2025-01-15T12:00:00+00:00",
        }
        pa = PlanArtifact.from_dict(d)
        assert pa.created_at == datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)


class TestFromTodo:
    def test_from_todo_basic(self):
        from types import SimpleNamespace

        todo = SimpleNamespace()
        todo.todo_id = "todo-123"
        todo.title = "Test Todo"
        todo.description = "A description"
        todo.tags = ["bug", "urgent"]
        todo.test_commands = ["pytest -x"]
        pa = PlanArtifact.from_todo(todo)
        assert pa.todo_id == "todo-123"
        assert pa.title == "Test Todo"
        assert "Tags: bug, urgent" in pa.notes
        assert "Test commands: pytest -x" in pa.notes

    def test_from_todo_without_tags_or_commands(self):
        from types import SimpleNamespace

        todo = SimpleNamespace()
        todo.todo_id = "t1"
        todo.title = "T"
        todo.description = "D"
        pa = PlanArtifact.from_todo(todo)
        assert pa.notes == ""
