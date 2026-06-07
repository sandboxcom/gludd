"""Tests for gap fixes: WorkType→TaskType mapping, per-type templates, playbook dispatch, task context."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates" / "prompts"


class TestWorkTypeToTaskTypeMapping:
    def test_code_maps_to_bug_fix_or_feature(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("code")
        assert result in (TaskType.BUG_FIX, TaskType.FEATURE)

    def test_test_maps_to_test_write(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("test")
        assert result == TaskType.TEST_WRITE

    def test_refactor_maps_to_refactor(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("refactor")
        assert result == TaskType.REFACTOR

    def test_review_maps_to_code_review(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("review")
        assert result == TaskType.CODE_REVIEW

    def test_docs_maps_to_documentation(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("docs")
        assert result == TaskType.DOCUMENTATION

    def test_security_maps_to_security_fix(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("security")
        assert result == TaskType.SECURITY_FIX

    def test_dependency_maps_to_feature(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("dependency")
        assert result == TaskType.FEATURE

    def test_unknown_work_type_defaults_feature(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("nonexistent")
        assert result == TaskType.FEATURE


class TestPlaybookDispatch:
    def test_playbook_for_work_type_code(self):
        from general_ludd.event_loop.loop import _playbook_for_work_type

        assert _playbook_for_work_type("code", "noop.yml") in ("noop.yml", "validate_task.yml")

    def test_playbook_for_work_type_test(self):
        from general_ludd.event_loop.loop import _playbook_for_work_type

        pb = _playbook_for_work_type("test", "noop.yml")
        assert pb in ("molecule_test.yml", "validate_task.yml", "noop.yml")

    def test_playbook_for_work_type_gap_analysis(self):
        from general_ludd.event_loop.loop import _playbook_for_work_type

        pb = _playbook_for_work_type("analysis", "noop.yml")
        assert pb in ("gap_analysis.yml", "noop.yml")

    def test_playbook_unknown_type_falls_back(self):
        from general_ludd.event_loop.loop import _playbook_for_work_type

        assert _playbook_for_work_type("unknown", "noop.yml") == "noop.yml"

    @pytest.mark.asyncio
    async def test_event_loop_dispatches_correct_playbook(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.todo import Todo, TodoStatus

        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.flush = AsyncMock()
        session.delete = AsyncMock()

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/job"}

        loop = EventLoop(
            session=session,
            runner=runner,
            config={
                "default_playbook": "noop.yml",
                "work_type_playbooks": {"analysis": "gap_analysis.yml"},
                "model_profiles": [],
                "rules": [],
            },
        )
        todo = Todo(
            title="gap analysis test",
            todo_id="TODO-GAP-001",
            status=TodoStatus.ACTIVE,
            work_type="analysis",
        )

        await loop._dispatch_execute_job(todo)

        write_vars_call = runner.write_vars.call_args
        job_vars = write_vars_call[1]["job_vars"]
        assert job_vars["playbook"] == "gap_analysis.yml"


class TestPerTypeTemplates:
    def test_template_files_exist_for_all_work_types(self):
        expected = {
            "code": "implementation.md.j2",
            "test": "test_creation.md.j2",
            "review": "code_review.md.j2",
            "docs": "documentation.md.j2",
            "analysis": "gap_analysis.md.j2",
            "audit": "log_audit.md.j2",
            "prompt": "prompt_eval.md.j2",
            "self_improvement": "self_improvement.md.j2",
            "dependency": "dependency_update.md.j2",
        }
        for work_type, filename in expected.items():
            path = TEMPLATES_DIR / filename
            assert path.exists(), f"Missing template: {filename} for work_type={work_type}"

    def test_implementation_template_has_required_sections(self):
        path = TEMPLATES_DIR / "implementation.md.j2"
        if not path.exists():
            return
        content = path.read_text()
        assert "todo_title" in content
        assert "task" in content.lower()

    def test_test_creation_template_has_tdd_reference(self):
        path = TEMPLATES_DIR / "test_creation.md.j2"
        if not path.exists():
            return
        content = path.read_text()
        assert "test" in content.lower()

    def test_gap_analysis_template_has_analysis_sections(self):
        path = TEMPLATES_DIR / "gap_analysis.md.j2"
        if not path.exists():
            return
        content = path.read_text()
        assert "gap" in content.lower() or "analysis" in content.lower()


class TestTemplateTaskContext:
    def test_resolve_prompt_text_passes_context(self):
        from general_ludd.event_loop.loop import _resolve_prompt_text_static

        registry = MagicMock()
        registry.render.return_value = "rendered with context"

        _resolve_prompt_text_static(
            registry,
            prompt_profile="implementation",
            todo_title="Fix bug",
            todo_description="The login is broken",
            work_type="code",
        )
        registry.render.assert_called_once()
        kwargs = registry.render.call_args[1]
        assert kwargs.get("todo_title") == "Fix bug"
        assert kwargs.get("work_type") == "code"


class TestPromptRegistryPerType:
    def test_registry_get_for_work_type(self):
        from general_ludd.prompts.registry import get_template_name_for_work_type

        assert get_template_name_for_work_type("code") in ("implementation.md.j2", "code.md.j2")
        assert get_template_name_for_work_type("test") in ("test_creation.md.j2", "test.md.j2")
        assert get_template_name_for_work_type("review") in ("code_review.md.j2", "review.md.j2")
        assert get_template_name_for_work_type("analysis") in ("gap_analysis.md.j2", "analysis.md.j2")

    def test_registry_get_for_unknown_type(self):
        from general_ludd.prompts.registry import get_template_name_for_work_type

        result = get_template_name_for_work_type("nonexistent")
        assert result is None or result == "code.md.j2"
