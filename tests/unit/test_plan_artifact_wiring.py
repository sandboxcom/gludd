"""Unit tests for plan_artifact wiring into schemas and event loop."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.planning.artifact import PlanArtifact
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.todo import Todo, TodoStatus


class TestPlanArtifactFieldOnTodo:
    def test_todo_plan_artifact_defaults_to_none(self):
        todo = Todo(title="test task")
        assert todo.plan_artifact is None

    def test_todo_plan_artifact_can_be_set(self):
        artifact = PlanArtifact(
            todo_id="TODO-001",
            title="Plan",
            content="Step 1: Do thing",
        )
        todo = Todo(title="test task", plan_artifact=artifact.to_markdown())
        assert todo.plan_artifact is not None
        assert "Step 1: Do thing" in todo.plan_artifact

    def test_todo_plan_artifact_serialization(self):
        todo = Todo(title="test task", plan_artifact="## Plan\nDo the work")
        data = todo.model_dump(mode="json")
        assert data["plan_artifact"] == "## Plan\nDo the work"


class TestPlanArtifactFieldOnJobSpec:
    def test_job_spec_plan_artifact_defaults_to_none(self):
        job = JobSpec(job_id="JOB-001", playbook="noop.yml", queue="core")
        assert job.plan_artifact is None

    def test_job_spec_plan_artifact_can_be_set(self):
        job = JobSpec(
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            plan_artifact="## Plan\nStep 1: Write code",
        )
        assert job.plan_artifact == "## Plan\nStep 1: Write code"

    def test_job_spec_plan_artifact_serialization(self):
        job = JobSpec(
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            plan_artifact="plan content",
        )
        data = job.model_dump(mode="json")
        assert data["plan_artifact"] == "plan content"


class TestEventLoopPlanArtifactDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_execute_job_includes_plan_artifact(self):
        loop, mocks = _make_loop()
        plan_md = "## Plan\nStep 1: Implement feature"
        todo = Todo(
            title="test task",
            todo_id="TODO-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            resource_profile="low_resource",
            plan_artifact=plan_md,
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        call_args = mocks["http_client"].post.call_args
        payload = call_args[1]["json"]
        assert payload["plan_artifact"] == plan_md

    @pytest.mark.asyncio
    async def test_dispatch_execute_job_without_plan_artifact(self):
        loop, mocks = _make_loop()
        todo = Todo(
            title="test task",
            todo_id="TODO-002",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        call_args = mocks["http_client"].post.call_args
        payload = call_args[1]["json"]
        assert payload["plan_artifact"] is None

    @pytest.mark.asyncio
    async def test_dispatch_review_job_includes_plan_artifact(self):
        loop, mocks = _make_loop()
        plan_md = "## Plan\nReview context"
        tr = MagicMock()
        tr.return_id = "RET-001"
        tr.todo_id = "TODO-001"
        tr.queue = "model"
        tr.plan_artifact = plan_md
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        await loop._dispatch_review_job(tr)
        call_args = mocks["http_client"].post.call_args
        payload = call_args[1]["json"]
        assert payload["plan_artifact"] == plan_md


def _make_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.delete = AsyncMock()
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
