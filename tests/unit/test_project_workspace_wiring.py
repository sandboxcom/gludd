"""Test ProjectWorkspace wired into EventLoop dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestProjectWorkspaceWiredIntoEventLoop:
    @pytest.mark.asyncio
    async def test_dispatch_uses_workspace_private_data_dir_when_project_matches(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.projects.workspace import ProjectWorkspace
        from general_ludd.schemas.todo import Todo, TodoStatus

        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.flush = AsyncMock()

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/global-job"}

        ws = ProjectWorkspace(project_id="proj-wired", base_dir="/tmp/gludd-test-ws")
        ws.ensure_dirs()

        loop = EventLoop(
            session=session,
            runner=runner,
            project_workspace={"proj-wired": ws},
            config={
                "default_playbook": "noop.yml",
                "model_profiles": [],
                "rules": [],
            },
        )
        todo = Todo(
            title="workspace test",
            todo_id="TODO-WS-001",
            status=TodoStatus.ACTIVE,
            work_type="unknown",
            project_id="proj-wired",
        )

        await loop._dispatch_execute_job(todo)

        run_playbook_call = runner.run_playbook.call_args
        pdd = str(run_playbook_call[1].get("private_data_dir", "")) if run_playbook_call else ""
        assert str(ws.private_data_dir) in pdd, (
            f"Expected workspace private_data_dir '{ws.private_data_dir}' in '{pdd}'"
        )

    @pytest.mark.asyncio
    async def test_dispatch_uses_global_dir_when_no_project_workspace(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.todo import Todo, TodoStatus

        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.flush = AsyncMock()

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/global-job"}

        loop = EventLoop(
            session=session,
            runner=runner,
            config={
                "default_playbook": "noop.yml",
                "model_profiles": [],
                "rules": [],
            },
        )
        todo = Todo(
            title="no workspace",
            todo_id="TODO-NOWS-001",
            status=TodoStatus.ACTIVE,
            work_type="unknown",
        )

        await loop._dispatch_execute_job(todo)

        write_vars_call = runner.write_vars.call_args
        job_vars = write_vars_call[1]["job_vars"]
        assert job_vars["playbook"] == "noop.yml"

    @pytest.mark.asyncio
    async def test_dispatch_uses_global_dir_when_project_mismatch(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.projects.workspace import ProjectWorkspace
        from general_ludd.schemas.todo import Todo, TodoStatus

        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.flush = AsyncMock()

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/global-job"}

        ws = ProjectWorkspace(project_id="proj-a", base_dir="/tmp/gludd-test-ws-a")
        ws.ensure_dirs()

        loop = EventLoop(
            session=session,
            runner=runner,
            project_workspace={"proj-a": ws},
            config={
                "default_playbook": "noop.yml",
                "model_profiles": [],
                "rules": [],
            },
        )
        todo = Todo(
            title="wrong project",
            todo_id="TODO-WP-001",
            status=TodoStatus.ACTIVE,
            work_type="unknown",
            project_id="proj-b",
        )

        await loop._dispatch_execute_job(todo)

        run_playbook_call = runner.run_playbook.call_args
        pdd = str(run_playbook_call[1].get("private_data_dir", "")) if run_playbook_call else ""
        assert str(ws.private_data_dir) not in pdd, (
            f"Should NOT use workspace dir for mismatched project, got '{pdd}'"
        )

    def test_playbook_for_work_type_returns_project_override(self):
        import tempfile
        from pathlib import Path

        from general_ludd.event_loop.loop import _playbook_for_work_type

        with tempfile.TemporaryDirectory() as tmpdir:
            pb_dir = Path(tmpdir) / "playbooks"
            pb_dir.mkdir()
            custom_pb = pb_dir / "unknown.yml"
            custom_pb.write_text("---\n- debug:\n    msg: hi\n")

            class FakeWS:
                playbooks_dir = str(pb_dir)

            result = _playbook_for_work_type(
                "unknown", "noop.yml", project_id="p1", workspaces={"p1": FakeWS()},
            )
            assert result == str(custom_pb)

    def test_playbook_for_work_type_returns_project_override_real_workspace(self):
        import tempfile
        from pathlib import Path

        from general_ludd.event_loop.loop import _playbook_for_work_type
        from general_ludd.projects.workspace import ProjectWorkspace

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_root = Path(tmpdir) / "proj-pb2"
            ws = ProjectWorkspace(project_id="proj-pb2", workspace_path=str(ws_root))
            ws.ensure_dirs()
            custom_pb = Path(ws.playbooks_dir) / "unknown.yml"
            custom_pb.write_text("---\n- debug:\n    msg: hi\n")

            result = _playbook_for_work_type(
                "unknown", "noop.yml", project_id="proj-pb2",
                workspaces={"proj-pb2": ws},
            )
            assert result == str(custom_pb)

    def test_playbook_for_work_type_returns_default_when_no_override(self):
        from general_ludd.event_loop.loop import _playbook_for_work_type

        result = _playbook_for_work_type(
            "misc", "noop.yml", project_id="proj-x", workspaces={},
        )
        assert result == "noop.yml"

    def test_playbook_for_work_type_returns_default_when_no_workspaces(self):
        from general_ludd.event_loop.loop import _playbook_for_work_type

        result = _playbook_for_work_type("misc", "noop.yml")
        assert result == "noop.yml"

    @pytest.mark.asyncio
    async def test_runner_path_uses_workspace_when_project_matches(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.projects.workspace import ProjectWorkspace
        from general_ludd.schemas.todo import Todo, TodoStatus

        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.flush = AsyncMock()

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/global-job"}

        ws = ProjectWorkspace(project_id="proj-run", base_dir="/tmp/gludd-test-run")
        ws.ensure_dirs()

        loop = EventLoop(
            session=session,
            runner=runner,
            project_workspace={"proj-run": ws},
            config={
                "default_playbook": "noop.yml",
                "model_profiles": [],
                "rules": [],
            },
        )
        todo = Todo(
            title="runner test",
            todo_id="TODO-RUN-001",
            status=TodoStatus.ACTIVE,
            work_type="unknown",
            project_id="proj-run",
        )

        await loop._dispatch_execute_job(todo)

        run_pb_call = runner.run_playbook.call_args
        pdd = str(run_pb_call[1].get("private_data_dir", "")) if run_pb_call else ""
        assert str(ws.private_data_dir) in pdd, (
            f"Expected workspace dir in '{pdd}'"
        )
