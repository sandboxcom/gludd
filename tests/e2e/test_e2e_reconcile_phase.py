"""E2E tests for the reconcile phase of the event loop.

Covers the reconcile gap identified in E2E_AUDIT_2026-07-06:
verifies that completed task decisions are reconciled (status transitioned)
through the full pipeline: create todo → dispatch (tick) → review decision →
reconcile (tick) → verify COMPLETE status.

All tests mock external dependencies (ModelGateway, AnsibleRunnerAdapter)
to keep runtime < 10s.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TaskDecisionModel, TaskReturnModel
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import EventLoop
from general_ludd.review.reviewer import ReturnReviewer
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_return import TaskReturn

_PROJECT_ID = "proj-reconcile-e2e"


def _project_manager_stub() -> SimpleNamespace:
    project = SimpleNamespace(project_id=_PROJECT_ID)
    return SimpleNamespace(
        select_project=lambda: project,
        list_active=lambda: [project],
    )


async def _create_test_infra():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    import general_ludd.daemon as daemon_mod
    from general_ludd.routers.todos import register as reg_todos
    if daemon_mod._daemon_state is None:
        daemon_mod._daemon_state = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
    daemon_mod._daemon_state["todos"] = []

    app = FastAPI()
    app.state._session_factory = factory
    app.state._config_dir = None
    app.state._startup_config = {}
    app.state.log_level = "info"
    app.state.tick_interval = 1.0
    app.state.event_loop = None
    app.state._templates_dir = None
    app.state._playbooks_dir = None
    app.state._project_manager = _project_manager_stub()
    reg_todos(app, daemon_mod._daemon_state)

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestReconcilePhaseE2E:
    @pytest.mark.asyncio
    async def test_reconcile_completes_todo_after_review(self):
        engine, factory, client, _app = await _create_test_infra()

        resp = await client.post(
            "/api/todos",
            json={
                "title": "Reconcile e2e: write hello",
                "queue": "core",
                "priority": "high",
                "work_type": "code",
                "project_id": _PROJECT_ID,
            },
        )
        assert resp.status_code == 201
        todo = resp.json()
        todo_id = todo["todo_id"]

        async with factory() as session:
            repo = TodoRepository(session)
            db_todo = await repo.get_by_id(todo_id)
            assert db_todo is not None

        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content="FILE: src/e2e_hello.py\nprint('reconcile e2e')\n"
        ))

        mock_review_gateway = MagicMock()
        mock_review_gateway.call_model = MagicMock(return_value=MagicMock(
            content=json.dumps({
                "decision": "complete",
                "confidence": 0.95,
                "evidence_refs": ["artifact:src/e2e_hello.py"],
            })
        ))

        mock_registry = MagicMock()
        mock_registry.render = MagicMock(return_value="Review this task")

        reviewer = ReturnReviewer(
            gateway=mock_review_gateway, prompt_registry=mock_registry,
        )

        from general_ludd.execution.engine import ExecutionEngine

        with tempfile.TemporaryDirectory() as ws:
            engine_exec = ExecutionEngine(
                model_gateway=mock_gateway, workspace_path=ws,
            )

            loop = EventLoop(
                session=factory,
                daemon_state={},
                config={"repo_root": ws},
                project_manager=_project_manager_stub(),
            )

            async def patched_dispatch(todo_item, **_kwargs):
                task_return_repo = (
                    _kwargs.get("_task_return_repo_override")
                    or loop._task_return_repo
                )
                job = JobSpec(
                    job_id=f"EXEC-{todo_item.todo_id}",
                    todo_id=todo_item.todo_id,
                    playbook="code",
                    queue="core",
                    work_type="code",
                    prompt_text=todo_item.title,
                )
                result = engine_exec.execute(job)
                if task_return_repo is not None:
                    await task_return_repo.create(data={
                        "return_id": result.return_id,
                        "project_id": _PROJECT_ID,
                        "todo_id": result.todo_id,
                        "job_id": result.job_id,
                        "playbook": result.playbook,
                        "queue": result.queue,
                        "exit_code": result.exit_code,
                        "result_summary": result.result_summary,
                    })
                    job_session = _kwargs.get("_session_override")
                    todo_repo = (
                        TodoRepository(job_session) if job_session is not None
                        else loop._todo_repo
                    )
                    if todo_repo is not None:
                        await todo_repo.update(
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
                    select(TaskReturnModel).where(
                        TaskReturnModel.todo_id == todo_id
                    )
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
                    project_id=_PROJECT_ID,
                    matched_todo_id=decision.matched_todo_id,
                    decision=decision.decision,
                    confidence=decision.confidence,
                    evidence_refs=json.dumps(decision.evidence_refs),
                )
                session.add(dm)
                await session.commit()

            await loop.tick()

            async with factory() as session:
                repo = TodoRepository(session)
                final_todo = await repo.get_by_id(todo_id)
                assert final_todo is not None
                assert final_todo.status == "complete"

            hello_path = Path(ws) / "src" / "e2e_hello.py"
            assert hello_path.exists()
            assert "reconcile e2e" in hello_path.read_text()

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_reconcile_needs_more_work_transition(self):
        engine, factory, client, _app = await _create_test_infra()

        resp = await client.post(
            "/api/todos",
            json={
                "title": "Reconcile e2e: needs more work",
                "queue": "core",
                "priority": "high",
                "work_type": "code",
                "project_id": _PROJECT_ID,
            },
        )
        assert resp.status_code == 201
        todo = resp.json()
        todo_id = todo["todo_id"]

        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content="FILE: src/incomplete.py\n# TODO\n"
        ))

        mock_review_gateway = MagicMock()
        mock_review_gateway.call_model = MagicMock(return_value=MagicMock(
            content=json.dumps({
                "decision": "needs_more_work",
                "confidence": 0.6,
                "evidence_refs": [],
            })
        ))

        mock_registry = MagicMock()
        mock_registry.render = MagicMock(return_value="Review this")

        reviewer = ReturnReviewer(
            gateway=mock_review_gateway, prompt_registry=mock_registry,
        )

        from general_ludd.execution.engine import ExecutionEngine

        with tempfile.TemporaryDirectory() as ws:
            engine_exec = ExecutionEngine(
                model_gateway=mock_gateway, workspace_path=ws,
            )

            loop = EventLoop(
                session=factory,
                daemon_state={},
                config={"repo_root": ws},
                project_manager=_project_manager_stub(),
            )

            async def patched_dispatch(todo_item, **_kwargs):
                task_return_repo = (
                    _kwargs.get("_task_return_repo_override")
                    or loop._task_return_repo
                )
                job = JobSpec(
                    job_id=f"EXEC-{todo_item.todo_id}",
                    todo_id=todo_item.todo_id,
                    playbook="code",
                    queue="core",
                    work_type="code",
                    prompt_text=todo_item.title,
                )
                result = engine_exec.execute(job)
                if task_return_repo is not None:
                    await task_return_repo.create(data={
                        "return_id": result.return_id,
                        "project_id": _PROJECT_ID,
                        "todo_id": result.todo_id,
                        "job_id": result.job_id,
                        "playbook": result.playbook,
                        "queue": result.queue,
                        "exit_code": result.exit_code,
                        "result_summary": result.result_summary,
                    })
                    job_session = _kwargs.get("_session_override")
                    todo_repo = (
                        TodoRepository(job_session) if job_session is not None
                        else loop._todo_repo
                    )
                    if todo_repo is not None:
                        await todo_repo.update(
                            todo_item.todo_id,
                            {"status": "reviewing_return"},
                            todo_item.version,
                        )

            loop._dispatch_execute_job = patched_dispatch

            await loop.tick()

            async with factory() as session:
                result = await session.execute(
                    select(TaskReturnModel).where(
                        TaskReturnModel.todo_id == todo_id
                    )
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
                assert decision.decision == "needs_more_work"

                dm = TaskDecisionModel(
                    return_id=decision.return_id,
                    project_id=_PROJECT_ID,
                    matched_todo_id=decision.matched_todo_id,
                    decision=decision.decision,
                    confidence=decision.confidence,
                    evidence_refs=json.dumps(decision.evidence_refs),
                )
                session.add(dm)
                await session.commit()

            await loop.tick()

            async with factory() as session:
                repo = TodoRepository(session)
                final_todo = await repo.get_by_id(todo_id)
                assert final_todo is not None
                assert final_todo.status == "needs_more_work"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_reconcile_skips_already_applied_decision(self):
        engine, factory, client, _app = await _create_test_infra()

        resp = await client.post(
            "/api/todos",
            json={
                "title": "Reconcile e2e: idempotent",
                "queue": "core",
                "priority": "high",
                "work_type": "code",
                "project_id": _PROJECT_ID,
            },
        )
        assert resp.status_code == 201
        todo = resp.json()
        todo_id = todo["todo_id"]

        mock_gateway = MagicMock()
        mock_gateway.call_model = MagicMock(return_value=MagicMock(
            content="FILE: src/idempotent.py\nprint('done')\n"
        ))

        mock_review_gateway = MagicMock()
        mock_review_gateway.call_model = MagicMock(return_value=MagicMock(
            content=json.dumps({
                "decision": "complete",
                "confidence": 0.95,
                "evidence_refs": ["artifact:src/idempotent.py"],
            })
        ))

        mock_registry = MagicMock()
        mock_registry.render = MagicMock(return_value="Review this")

        reviewer = ReturnReviewer(
            gateway=mock_review_gateway, prompt_registry=mock_registry,
        )

        from general_ludd.execution.engine import ExecutionEngine

        with tempfile.TemporaryDirectory() as ws:
            engine_exec = ExecutionEngine(
                model_gateway=mock_gateway, workspace_path=ws,
            )

            loop = EventLoop(
                session=factory,
                daemon_state={},
                config={"repo_root": ws},
                project_manager=_project_manager_stub(),
            )

            async def patched_dispatch(todo_item, **_kwargs):
                task_return_repo = (
                    _kwargs.get("_task_return_repo_override")
                    or loop._task_return_repo
                )
                job = JobSpec(
                    job_id=f"EXEC-{todo_item.todo_id}",
                    todo_id=todo_item.todo_id,
                    playbook="code",
                    queue="core",
                    work_type="code",
                    prompt_text=todo_item.title,
                )
                result = engine_exec.execute(job)
                if task_return_repo is not None:
                    await task_return_repo.create(data={
                        "return_id": result.return_id,
                        "project_id": _PROJECT_ID,
                        "todo_id": result.todo_id,
                        "job_id": result.job_id,
                        "playbook": result.playbook,
                        "queue": result.queue,
                        "exit_code": result.exit_code,
                        "result_summary": result.result_summary,
                    })
                    job_session = _kwargs.get("_session_override")
                    todo_repo = (
                        TodoRepository(job_session) if job_session is not None
                        else loop._todo_repo
                    )
                    if todo_repo is not None:
                        await todo_repo.update(
                            todo_item.todo_id,
                            {"status": "reviewing_return"},
                            todo_item.version,
                        )

            loop._dispatch_execute_job = patched_dispatch

            await loop.tick()

            async with factory() as session:
                result = await session.execute(
                    select(TaskReturnModel).where(
                        TaskReturnModel.todo_id == todo_id
                    )
                )
                returns = list(result.scalars().all())
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
                    project_id=_PROJECT_ID,
                    matched_todo_id=decision.matched_todo_id,
                    decision=decision.decision,
                    confidence=decision.confidence,
                    evidence_refs=json.dumps(decision.evidence_refs),
                )
                session.add(dm)
                await session.commit()

            await loop.tick()
            await loop.tick()

            async with factory() as session:
                repo = TodoRepository(session)
                final_todo = await repo.get_by_id(todo_id)
                assert final_todo is not None
                assert final_todo.status == "complete"

        await engine.dispose()
