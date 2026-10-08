"""Integration proof for project-isolated persisted tool output."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base, ProjectModel
from general_ludd.db.repository import VariableNamespaceRepository
from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import ResourceProfile, Todo, TodoStatus, WorkType


def _runner() -> MagicMock:
    runner = MagicMock()
    runner.prepare_job_dirs.return_value = {
        "root": "/tmp/EXEC-S36",
        "env": "/tmp/EXEC-S36/env",
    }
    runner.write_vars.return_value = "/tmp/EXEC-S36/env/extravars"
    runner.run_playbook.return_value = {"rc": 0, "output": "", "events": []}
    return runner


async def _run_inline(function: Any, *args: Any, **kwargs: Any) -> Any:
    return function(*args, **kwargs)


@pytest.mark.asyncio
async def test_generated_tool_output_is_visible_only_to_owning_project(
    tmp_path: Path,
) -> None:
    """Persist under project A while retaining safe global config inheritance."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 's36.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                ProjectModel(project_id="proj-a", name="Project A"),
                ProjectModel(project_id="proj-b", name="Project B"),
            ]
        )
        await session.flush()
        repository = VariableNamespaceRepository(session)
        await repository.set_var("config", "SHARED_SETTING", "enabled")
        await repository.set_var(
            "tool_results", "tool_result:legacy", "legacy-global-output"
        )

        dispatcher = MagicMock()
        dispatcher.dispatch_all = AsyncMock(
            return_value=[
                DispatchResult(
                    ok=True,
                    kind="mcp",
                    name="fs/write_file",
                    output="project-a-output",
                )
            ]
        )
        event_loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=_runner(),
            model_gateway=MagicMock(),
            dispatcher=dispatcher,
            variable_repo=repository,
        )
        todo = Todo(
            title="Generate project artifact",
            todo_id="TODO-S36",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type=WorkType.CODE,
            resource_profile=ResourceProfile.LOW_RESOURCE,
            project_id="proj-a",
        )
        structured_call = [
            {
                "id": "call-s36",
                "type": "function",
                "function": {
                    "name": "fs/write_file",
                    "arguments": '{"path": "artifact.txt", "content": "ok"}',
                },
            }
        ]

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _run_inline),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("generated", structured_call),
            ),
        ):
            await event_loop._dispatch_execute_job(todo)

        project_a = await repository.load_vars_for_project("proj-a")
        project_b = await repository.load_vars_for_project("proj-b")

        assert project_a == {
            "SHARED_SETTING": "enabled",
            "tool_result:fs/write_file": "project-a-output",
        }
        assert project_b == {"SHARED_SETTING": "enabled"}

    await engine.dispose()
