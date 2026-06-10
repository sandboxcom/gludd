"""Tests for per-project Ansible roles injection in EventLoop dispatch."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
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


class TestDispatchExecuteJobHTTPRolesPath:
    @pytest.mark.asyncio
    async def test_http_dispatch_includes_ansible_roles_path(self, tmp_path):
        ws = ProjectWorkspace(
            project_id="proj-http-1",
            workspace_path=str(tmp_path / "proj-http-1"),
        )
        ws.ensure_dirs()
        roles_dir = ws.roles_dir

        http_client = AsyncMock()
        http_client.post.return_value = MagicMock(status_code=202)

        loop, _ = _make_loop(
            http_client=http_client,
            project_workspace={"proj-http-1": ws},
        )
        todo = Todo(
            title="http dispatch task",
            todo_id="TODO-HTTP-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            project_id="proj-http-1",
        )
        await loop._dispatch_execute_job(todo)

        http_client.post.assert_called_once()
        call_kwargs = http_client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload is not None
        assert "ansible_roles_path" in payload
        assert payload["ansible_roles_path"] == str(roles_dir)

    @pytest.mark.asyncio
    async def test_http_dispatch_includes_templates_dir(self, tmp_path):
        ws = ProjectWorkspace(
            project_id="proj-http-2",
            workspace_path=str(tmp_path / "proj-http-2"),
        )
        ws.ensure_dirs()

        http_client = AsyncMock()
        http_client.post.return_value = MagicMock(status_code=202)

        loop, _ = _make_loop(
            http_client=http_client,
            project_workspace={"proj-http-2": ws},
        )
        todo = Todo(
            title="http templates task",
            todo_id="TODO-HTTP-TMPL-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            project_id="proj-http-2",
        )
        await loop._dispatch_execute_job(todo)

        http_client.post.assert_called_once()
        call_kwargs = http_client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload is not None
        assert "templates_dir" in payload
        assert payload["templates_dir"] == str(ws.templates_dir)

    @pytest.mark.asyncio
    async def test_http_dispatch_no_roles_path_without_workspace(self):
        http_client = AsyncMock()
        http_client.post.return_value = MagicMock(status_code=202)

        loop, _ = _make_loop(http_client=http_client)
        todo = Todo(
            title="no ws task",
            todo_id="TODO-HTTP-NOWS-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        await loop._dispatch_execute_job(todo)

        http_client.post.assert_called_once()
        call_kwargs = http_client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload is not None
        assert payload.get("ansible_roles_path") is None


class TestDispatchExecuteJobRunnerRolesPath:
    @pytest.mark.asyncio
    async def test_runner_env_includes_ansible_roles_path(self, tmp_path):
        ws = ProjectWorkspace(
            project_id="proj-runner-1",
            workspace_path=str(tmp_path / "proj-runner-1"),
        )
        ws.ensure_dirs()
        roles_dir = ws.roles_dir

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": str(tmp_path / "runner")}
        runner.write_vars.return_value = str(tmp_path / "vars")
        runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        loop, _ = _make_loop(
            runner=runner,
            project_workspace={"proj-runner-1": ws},
        )
        todo = Todo(
            title="runner dispatch task",
            todo_id="TODO-RUNNER-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            project_id="proj-runner-1",
        )
        await loop._dispatch_execute_job(todo)

        runner.run_playbook.assert_called_once()
        call_kwargs = runner.run_playbook.call_args
        env = call_kwargs.kwargs.get("env", {})
        assert "ANSIBLE_ROLES_PATH" in env
        assert env["ANSIBLE_ROLES_PATH"] == str(roles_dir)

    @pytest.mark.asyncio
    async def test_runner_env_includes_templates_path(self, tmp_path):
        ws = ProjectWorkspace(
            project_id="proj-runner-2",
            workspace_path=str(tmp_path / "proj-runner-2"),
        )
        ws.ensure_dirs()

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": str(tmp_path / "runner")}
        runner.write_vars.return_value = str(tmp_path / "vars")
        runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        loop, _ = _make_loop(
            runner=runner,
            project_workspace={"proj-runner-2": ws},
        )
        todo = Todo(
            title="runner templates task",
            todo_id="TODO-RUNNER-TMPL-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            project_id="proj-runner-2",
        )
        await loop._dispatch_execute_job(todo)

        runner.run_playbook.assert_called_once()
        call_kwargs = runner.run_playbook.call_args
        env = call_kwargs.kwargs.get("env", {})
        assert "ANSIBLE_ROLES_PATH" in env
        assert "GLUDD_TEMPLATES_DIR" in env
        assert env["GLUDD_TEMPLATES_DIR"] == str(ws.templates_dir)
