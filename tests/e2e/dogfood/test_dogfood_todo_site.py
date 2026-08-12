"""Greenfield todo-site dogfood: seed, run, review, verify, and teardown."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from tests.e2e.dogfood._gateway import build_gateway
from tests.e2e.dogfood._secrets import load_llm_keys
from tests.e2e.dogfood._site import (
    OFFLINE_SCAFFOLD_APP,
    run_site_crud_tests,
    run_site_tests,
    write_offline_scaffold,
)

pytestmark = pytest.mark.e2e

if TYPE_CHECKING:
    from general_ludd.models.gateway import ModelGateway
    from general_ludd.review.reviewer import ReturnReviewer

_PROJECT_ID = "proj-dogfood-todo-site"
_MOCK_GENERATION = f"""```python
FILE: app/main.py
{OFFLINE_SCAFFOLD_APP}
```"""


def test_secrets_loader_returns_none_gracefully(tmp_path: Path) -> None:
    """Secrets loader returns None when no file and no env var."""
    # tmp_path has no .secrets/ subdir
    result = load_llm_keys(repo_root=tmp_path)
    assert result is None


def test_mock_gateway_offline_mode() -> None:
    """Offline: mock gateway returns deterministic response."""
    MOCK_SITE_CODE = "# mock FastAPI app\n"
    gw, mode = build_gateway(None, mock_response=MOCK_SITE_CODE)
    assert mode == "mock"
    resp = gw.call_model("any-profile", [{"role": "user", "content": "build a todo site"}])
    assert resp.content == MOCK_SITE_CODE


def test_site_crud_no_app(tmp_path: Path) -> None:
    """Site helper returns app_importable=False gracefully for empty workspace."""
    results = run_site_crud_tests(tmp_path)
    assert results.get("app_importable") is False


def test_offline_greenfield_artifact_passes_strict_crud_contract(
    tmp_path: Path,
) -> None:
    """CI proves the generated-site verifier without a live model credential."""
    write_offline_scaffold(tmp_path)

    run_site_tests(tmp_path)


def test_strict_site_runner_rejects_an_empty_workspace(tmp_path: Path) -> None:
    """A missing generated app is a failed dogfood result, never a soft pass."""
    with pytest.raises(AssertionError, match="app_importable"):
        run_site_tests(tmp_path)


def test_live_mode_routes_review_through_configured_gateway() -> None:
    """Live dogfood must exercise model review, not only model generation."""
    from general_ludd.models.gateway import ModelResponse
    from general_ludd.schemas.task_return import TaskReturn

    gateway = MagicMock()
    gateway.call_model.return_value = ModelResponse(
        content=json.dumps(
            {
                "decision": "complete",
                "confidence": 0.95,
                "evidence_refs": ["artifact:app/main.py"],
            }
        ),
        model_name="live-review",
    )
    reviewer = _build_reviewer(
        gateway,
        model_profile="zai-glm-e2e",
        live_review=True,
    )
    decision = reviewer.review_return(
        TaskReturn(
            return_id="ret-live-review",
            todo_id="todo-live-review",
            job_id="job-live-review",
            playbook="code",
            queue="core",
            exit_code=0,
            result_summary="generated app/main.py",
        ),
        [],
        ["artifact:app/main.py"],
    )

    assert decision.decision == "complete"
    assert decision.evidence_refs == ["artifact:app/main.py"]
    assert gateway.call_model.call_args.args[0] == "zai-glm-e2e"


def _build_reviewer(
    gateway: ModelGateway,
    *,
    model_profile: str,
    live_review: bool,
) -> ReturnReviewer:
    """Use the configured gateway live and a deterministic reviewer offline."""
    from general_ludd.models.gateway import ModelGateway, ModelResponse
    from general_ludd.review.reviewer import ReturnReviewer

    review_gateway = gateway
    if not live_review:
        review_gateway = MagicMock(spec=ModelGateway)
        review_gateway.call_model.return_value = ModelResponse(
            content=json.dumps(
                {
                    "decision": "complete",
                    "confidence": 0.95,
                    "evidence_refs": ["artifact:app/main.py"],
                }
            ),
            model_name="offline-review",
        )
    prompt_registry = MagicMock()
    prompt_registry.render.return_value = (
        "Review the generated FastAPI todo site. Return a TaskDecision JSON object "
        "and cite artifact:app/main.py only when the return successfully generated "
        "that artifact."
    )
    return ReturnReviewer(
        gateway=review_gateway,
        prompt_registry=prompt_registry,
        model_profile_id=model_profile,
    )


async def _run_greenfield_scenario(
    tmp_path: Path,
    gateway: ModelGateway,
    *,
    model_profile: str,
    live_review: bool,
) -> None:
    """Run API seed -> async engine -> review -> reconcile -> CRUD assertions."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from general_ludd.db.models import Base, TaskDecisionModel, TaskReturnModel
    from general_ludd.db.repository import TodoRepository
    from general_ludd.event_loop.loop import EventLoop
    from general_ludd.execution.engine import ExecutionEngine
    from general_ludd.git_automation.repo import GitAutomation
    from general_ludd.projects.manager import ProjectManager
    from general_ludd.routers.todos import register as register_todos
    from general_ludd.schemas.job import JobSpec
    from general_ludd.schemas.task_return import TaskReturn

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git = GitAutomation(str(workspace))
    git.init_repo()
    initial_commit = git.get_current_commit()

    db_engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with db_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    project_manager = ProjectManager()
    project = project_manager.add_project(
        name="todo-site-dogfood",
        weight=100.0,
        workspace_path=str(workspace),
        dispatch_mode="active",
    )
    project.project_id = _PROJECT_ID
    project_manager._projects = {_PROJECT_ID: project}

    daemon_state: dict[str, object] = {"todos": []}
    app = FastAPI()
    app.state._session_factory = factory
    app.state._config_dir = None
    app.state._startup_config = {}
    app.state.log_level = "info"
    app.state.tick_interval = 1.0
    app.state.event_loop = None
    app.state._templates_dir = None
    app.state._playbooks_dir = None
    app.state._project_manager = project_manager
    register_todos(app, daemon_state)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://dogfood.test",
    )

    execution_engine = ExecutionEngine(
        model_gateway=gateway,
        workspace_path=str(workspace),
    )
    loop = EventLoop(
        session=factory,
        daemon_state=daemon_state,
        config={"repo_root": str(workspace)},
        project_manager=project_manager,
    )

    reviewer = _build_reviewer(
        gateway,
        model_profile=model_profile,
        live_review=live_review,
    )

    try:
        response = await client.post(
            "/api/todos",
            json={
                "title": "Build a complete FastAPI todo website",
                "description": (
                    "Create app/main.py with GET / HTML and JSON CRUD at "
                    "GET/POST /api/todos and PUT/DELETE /api/todos/{id}. "
                    "Items contain id, title, and done."
                ),
                "queue": "core",
                "priority": "high",
                "work_type": "code",
                "project_id": _PROJECT_ID,
            },
        )
        assert response.status_code == 201, response.text
        todo_id = response.json()["todo_id"]

        async def patched_dispatch(todo_item: Any, **overrides: Any) -> None:
            return_repo = (
                overrides.get("_task_return_repo_override")
                or loop._task_return_repo
            )
            job = JobSpec(
                job_id=f"EXEC-{todo_item.todo_id}",
                todo_id=todo_item.todo_id,
                project_id=todo_item.project_id,
                playbook="code",
                queue="core",
                work_type="code",
                model_profile=model_profile,
                prompt_text=todo_item.title,
            )
            result = await execution_engine.execute_async(job)
            assert result.exit_code == 0, result.result_summary
            if return_repo is not None:
                await return_repo.create(
                    data={
                        "return_id": result.return_id,
                        "project_id": todo_item.project_id,
                        "todo_id": result.todo_id,
                        "job_id": result.job_id,
                        "playbook": result.playbook,
                        "queue": result.queue,
                        "exit_code": result.exit_code,
                        "result_summary": result.result_summary,
                    }
                )
                job_session = overrides.get("_session_override")
                todo_repo = (
                    TodoRepository(job_session)
                    if job_session is not None
                    else loop._todo_repo
                )
                if todo_repo is not None:
                    await todo_repo.update(
                        todo_item.todo_id,
                        {"status": "reviewing_return"},
                        todo_item.version,
                    )

        loop.__dict__["_dispatch_execute_job"] = patched_dispatch
        await asyncio.wait_for(loop.tick(), timeout=30.0)

        async with factory() as session:
            result = await session.execute(
                select(TaskReturnModel).where(TaskReturnModel.todo_id == todo_id)
            )
            return_row = result.scalars().one()
            task_return = TaskReturn(
                return_id=return_row.return_id,
                todo_id=return_row.todo_id,
                job_id=return_row.job_id,
                playbook=return_row.playbook,
                queue=return_row.queue,
                exit_code=return_row.exit_code,
                result_summary=return_row.result_summary,
            )
            decision = reviewer.review_return(task_return, [], [])
            assert decision.decision == "complete"
            assert "artifact:app/main.py" in decision.evidence_refs
            session.add(
                TaskDecisionModel(
                    return_id=decision.return_id,
                    project_id=_PROJECT_ID,
                    matched_todo_id=decision.matched_todo_id or todo_id,
                    decision=decision.decision,
                    confidence=decision.confidence,
                    evidence_refs=json.dumps(decision.evidence_refs),
                )
            )
            await session.commit()

        await asyncio.wait_for(loop.tick(), timeout=30.0)
        async with factory() as session:
            final_todo = await TodoRepository(session).get_by_id(todo_id)
            assert final_todo is not None
            assert final_todo.status == "complete"

        if execution_engine._background_tasks:
            await asyncio.gather(*tuple(execution_engine._background_tasks))
        assert any(branch.startswith("gludd/") for branch in git.list_branches())
        assert git.get_current_commit() != initial_commit
        run_site_tests(workspace)
    finally:
        if execution_engine._background_tasks:
            await asyncio.gather(
                *tuple(execution_engine._background_tasks),
                return_exceptions=True,
            )
        await execution_engine.shutdown()
        await client.aclose()
        await db_engine.dispose()
        project_manager.remove_project(_PROJECT_ID)
        shutil.rmtree(workspace, ignore_errors=True)

    assert not workspace.exists()


async def test_todo_website_offline_scenario(tmp_path: Path) -> None:
    """CI runs the real lifecycle with deterministic generated artifacts."""
    gateway, mode = build_gateway(None, mock_response=_MOCK_GENERATION)
    assert mode == "mock"

    await _run_greenfield_scenario(
        tmp_path,
        gateway,
        model_profile="default",
        live_review=False,
    )


async def test_todo_website_live_scenario(tmp_path: Path) -> None:
    """A configured ZAI model must generate a site that passes the same gate."""
    credentials = load_llm_keys()
    if credentials is None:
        pytest.skip("ZAI_API_KEY is required for the live dogfood scenario")
    gateway, mode = build_gateway(credentials)
    assert mode == "live"

    await _run_greenfield_scenario(
        tmp_path,
        gateway,
        model_profile="zai-glm-e2e",
        live_review=True,
    )
