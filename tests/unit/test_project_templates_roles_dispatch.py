"""Tests for per-project templates and roles wiring in EventLoop dispatch."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop, _resolve_prompt_text_static
from general_ludd.projects.workspace import ProjectWorkspace
from general_ludd.schemas.todo import Todo, TodoStatus


def _make_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.flush = AsyncMock()
    http_client = AsyncMock()
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()
    defaults = dict(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=session,
        http_client=http_client,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "http_client": http_client,
        "todo_repo": todo_repo,
        "task_return_repo": task_return_repo,
    }


class TestResolvePromptTextStaticWithProjectTemplates:
    def test_reads_project_template_file(self, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        template_file = templates_dir / "custom_prompt.md.j2"
        template_file.write_text("Project prompt for {{ todo_title }}")

        result = _resolve_prompt_text_static(
            prompt_registry=None,
            prompt_profile="custom_prompt.md.j2",
            project_templates_dir=str(templates_dir),
            todo_title="my task",
        )
        assert result == "Project prompt for my task"

    def test_falls_back_to_global_registry_when_project_template_missing(self):
        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Global prompt"
        import tempfile
        empty_dir = tempfile.mkdtemp()

        result = _resolve_prompt_text_static(
            prompt_registry=prompt_registry,
            prompt_profile="nonexistent.md.j2",
            project_templates_dir=empty_dir,
            todo_title="my task",
        )
        assert result == "Global prompt"
        prompt_registry.render.assert_called_once()

    def test_uses_global_registry_when_no_project_templates_dir(self):
        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Global prompt"

        result = _resolve_prompt_text_static(
            prompt_registry=prompt_registry,
            prompt_profile="some_profile.md.j2",
            project_templates_dir=None,
            todo_title="my task",
        )
        assert result == "Global prompt"

    def test_project_template_takes_priority_over_global(self):
        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Global prompt"
        tmp_dir = Path("/tmp/test-project-templates-priority")
        tmp_dir.mkdir(exist_ok=True)
        templates_dir = tmp_dir / "templates"
        templates_dir.mkdir(exist_ok=True)
        template_file = templates_dir / "override.md.j2"
        template_file.write_text("Project override for {{ todo_title }}")

        result = _resolve_prompt_text_static(
            prompt_registry=prompt_registry,
            prompt_profile="override.md.j2",
            project_templates_dir=str(templates_dir),
            todo_title="priority task",
        )
        assert result == "Project override for priority task"
        prompt_registry.render.assert_not_called()

    def test_returns_none_when_no_profile(self):
        result = _resolve_prompt_text_static(
            prompt_registry=MagicMock(),
            prompt_profile=None,
            project_templates_dir="/some/dir",
        )
        assert result is None


class TestDispatchExecuteJobRolesInjection:
    @pytest.mark.asyncio
    async def test_injects_ansible_roles_path_from_project_workspace(self, tmp_path):
        ws = ProjectWorkspace(
            project_id="proj-1",
            workspace_path=str(tmp_path / "proj-1"),
        )
        ws.ensure_dirs()
        roles_dir = ws.roles_dir
        assert roles_dir.is_dir()

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": str(tmp_path / "runner")}
        runner.write_vars.return_value = str(tmp_path / "vars")
        runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        loop, _ = _make_loop(
            runner=runner,
            project_workspace={"proj-1": ws},
        )
        todo = Todo(
            title="deploy thing",
            todo_id="TODO-ROLES-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            project_id="proj-1",
        )
        await loop._dispatch_execute_job(todo)

        runner.run_playbook.assert_called_once()
        call_kwargs = runner.run_playbook.call_args
        assert "env" in call_kwargs.kwargs
        env = call_kwargs.kwargs["env"]
        assert "ANSIBLE_ROLES_PATH" in env
        assert env["ANSIBLE_ROLES_PATH"] == str(roles_dir)

    @pytest.mark.asyncio
    async def test_no_roles_path_when_no_project_workspace(self):
        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/runner"}
        runner.write_vars.return_value = "/tmp/vars"
        runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        loop, _ = _make_loop(runner=runner)
        todo = Todo(
            title="generic task",
            todo_id="TODO-NO-WS-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        await loop._dispatch_execute_job(todo)

        runner.run_playbook.assert_called_once()
        call_kwargs = runner.run_playbook.call_args
        env = call_kwargs.kwargs.get("env", {})
        assert env.get("ANSIBLE_ROLES_PATH") is None

    @pytest.mark.asyncio
    async def test_no_roles_path_when_workspace_has_no_roles_dir(self, tmp_path):
        ws = ProjectWorkspace(
            project_id="proj-2",
            workspace_path=str(tmp_path / "proj-2"),
        )
        ws.ensure_dirs()
        roles_dir = ws.roles_dir
        import shutil
        shutil.rmtree(str(roles_dir), ignore_errors=True)

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": str(tmp_path / "runner")}
        runner.write_vars.return_value = str(tmp_path / "vars")
        runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        loop, _ = _make_loop(
            runner=runner,
            project_workspace={"proj-2": ws},
        )
        todo = Todo(
            title="task without roles",
            todo_id="TODO-NO-ROLES-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            project_id="proj-2",
        )
        await loop._dispatch_execute_job(todo)

        runner.run_playbook.assert_called_once()
        call_kwargs = runner.run_playbook.call_args
        env = call_kwargs.kwargs.get("env", {})
        assert env.get("ANSIBLE_ROLES_PATH") is None


class TestDispatchUsesProjectTemplatesForPrompt:
    @pytest.mark.asyncio
    async def test_dispatch_resolves_prompt_from_project_templates(self, tmp_path):
        ws = ProjectWorkspace(
            project_id="proj-tmpl",
            workspace_path=str(tmp_path / "proj-tmpl"),
        )
        ws.ensure_dirs()
        templates_dir = ws.templates_dir
        template_file = templates_dir / "custom_prompt.md.j2"
        template_file.write_text("Project-specific: {{ todo_title }}")

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": str(tmp_path / "runner")}
        runner.write_vars.return_value = str(tmp_path / "vars")
        runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        loop, _ = _make_loop(
            runner=runner,
            project_workspace={"proj-tmpl": ws},
        )
        todo = Todo(
            title="use project template",
            todo_id="TODO-TMPL-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            project_id="proj-tmpl",
            prompt_profile="custom_prompt.md.j2",
        )
        await loop._dispatch_execute_job(todo)

        runner.write_vars.assert_called_once()
        written_vars = runner.write_vars.call_args
        job_vars = written_vars.kwargs["job_vars"]
        assert "prompt_text" in job_vars
        assert job_vars["prompt_text"] == "Project-specific: use project template"
