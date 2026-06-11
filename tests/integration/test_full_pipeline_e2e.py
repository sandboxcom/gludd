from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base, TaskDecisionModel, TaskReturnModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import EventLoop
from general_ludd.review.reviewer import ReturnReviewer
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_return import TaskReturn


def _init_git_repo(path: str) -> None:
    subprocess.run(["git", "init", path], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "agent@test"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Agent"],
        cwd=path, check=True, capture_output=True,
    )
    (Path(path) / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


async def _create_test_infra():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from general_ludd.daemon import _daemon_state
    from general_ludd.routers.todos import register as reg_todos
    _daemon_state["todos"] = []

    app = FastAPI()
    app.state._session_factory = factory
    app.state._config_dir = None
    app.state._startup_config = {}
    app.state.log_level = "info"
    app.state.tick_interval = 1.0
    app.state.event_loop = None
    app.state._templates_dir = None
    app.state._playbooks_dir = None
    reg_todos(app, _daemon_state)

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestFullPipelineE2E:
    @pytest.mark.asyncio
    async def test_todo_from_api_to_reconciled_status(self):
        engine, factory, client, _app = await _create_test_infra()

        resp = await client.post(
            "/api/todos",
            json={
                "title": "Add hello world",
                "queue": "core",
                "priority": "high",
                "work_type": "code",
            },
        )
        assert resp.status_code == 201
        todo = resp.json()
        todo_id = todo["todo_id"]

        async with factory() as session:
            repo = TodoRepository(session)
            db_todo = await repo.get_by_id(todo_id)
            assert db_todo is not None

        with tempfile.TemporaryDirectory() as ws:
            _init_git_repo(ws)

            mock_gateway = MagicMock()
            code = "print('hello from gludd')\n"
            mock_gateway.call_model = MagicMock(return_value=MagicMock(
                content=f"```\nFILE: src/hello.py\n{code}\n```"
            ))

            mock_review_gateway = MagicMock()
            mock_review_gateway.call_model = MagicMock(return_value=MagicMock(
                content='{"decision": "complete", "confidence": 0.95}'
            ))
            mock_registry = MagicMock()
            mock_registry.render = MagicMock(return_value="Review this")

            reviewer = ReturnReviewer(
                gateway=mock_review_gateway, prompt_registry=mock_registry,
            )

            from general_ludd.execution.engine import ExecutionEngine
            engine_exec = ExecutionEngine(
                model_gateway=mock_gateway, workspace_path=ws,
            )

            loop = EventLoop(session=factory, daemon_state={})

            async def patched_dispatch(todo_item):
                job = JobSpec(
                    job_id=f"EXEC-{todo_item.todo_id}",
                    todo_id=todo_item.todo_id,
                    playbook="code",
                    queue="core",
                    work_type="code",
                    prompt_text=todo_item.title,
                )
                result = engine_exec.execute(job)
                if loop._task_return_repo is not None:
                    await loop._task_return_repo.create(data={
                        "return_id": result.return_id,
                        "todo_id": result.todo_id,
                        "job_id": result.job_id,
                        "playbook": result.playbook,
                        "queue": result.queue,
                        "exit_code": result.exit_code,
                        "result_summary": result.result_summary,
                    })
                    if loop._todo_repo is not None:
                        await loop._todo_repo.update(
                            todo_item.todo_id,
                            {"status": "reviewing_return"},
                            todo_item.version,
                        )

            loop._dispatch_execute_job = patched_dispatch

            await loop.tick()

            async with factory() as session:
                claimed_todo = await TodoRepository(session).get_by_id(todo_id)
                assert claimed_todo is not None

                result = await session.execute(
                    select(TaskReturnModel).where(TaskReturnModel.todo_id == todo_id)
                )
                returns = list(result.scalars().all())
                assert len(returns) >= 1
                task_return = returns[0]

                tr = TaskReturn(
                    return_id=task_return.return_id,
                    todo_id=task_return.todo_id,
                    job_id=task_return.job_id,
                    playbook=task_return.playbook,
                    queue=task_return.queue,
                    exit_code=task_return.exit_code,
                    result_summary=task_return.result_summary,
                )
                decision = reviewer.review_return(tr, [], [])
                assert decision.decision == "complete"

                dm = TaskDecisionModel(
                    return_id=decision.return_id,
                    matched_todo_id=decision.matched_todo_id,
                    decision=decision.decision,
                    confidence=decision.confidence,
                )
                session.add(dm)
                await session.commit()

            await loop.tick()

            async with factory() as session:
                repo = TodoRepository(session)
                final_todo = await repo.get_by_id(todo_id)
                assert final_todo is not None
                assert final_todo.status == "complete"

            branches_result = subprocess.run(
                ["git", "branch"], cwd=ws, capture_output=True, text=True,
            )
            branch_list = [
                b.strip().lstrip("* ") for b in branches_result.stdout.splitlines()
            ]
            gludd_branches = [
                b for b in branch_list if b.startswith("gludd/")
            ]
            assert len(gludd_branches) >= 1

            hello_path = Path(ws) / "src" / "hello.py"
            assert hello_path.exists()
            assert "hello from gludd" in hello_path.read_text()

        await engine.dispose()
