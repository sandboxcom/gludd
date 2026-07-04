"""Unit tests for RunRecorder wired into dispatch/completion hooks."""

import asyncio
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher
from general_ludd.agents.registry import default_registry
from general_ludd.agents.types import AgentTask
from general_ludd.event_loop.loop import EventLoop
from general_ludd.filestore.store import FileStore
from general_ludd.replay.recorder import RunRecorder


def _temp_recorder() -> RunRecorder:
    tmp = tempfile.mkdtemp(prefix="gludd_replay_test_")
    store = FileStore(root_path=tmp)
    return RunRecorder(store=store), tmp


class TestEventLoopRunRecorder:
    @pytest.mark.asyncio
    async def test_dispatch_started_event_recorded(self):
        recorder, tmpdir = _temp_recorder()
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.add = MagicMock()

        http_client = AsyncMock()
        http_client.post.return_value = AsyncMock()
        http_client.post.return_value.status_code = 200
        http_client.post.return_value.json = AsyncMock(return_value={
            "return_id": "RET-001",
            "exit_code": 0,
            "result_summary": "ok",
        })

        todo_repo = AsyncMock()
        task_return_repo = AsyncMock()

        prompt_registry = MagicMock()
        prompt_registry.render.return_value = "Test prompt for dispatch"

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0},
            session=session,
            http_client=http_client,
            todo_repo=todo_repo,
            task_return_repo=task_return_repo,
            prompt_registry=prompt_registry,
            run_recorder=recorder,
        )

        todo = MagicMock()
        todo.todo_id = "test-todo-001"
        todo.queue = "core"
        todo.work_type = "code"
        todo.prompt_profile = "default"
        todo.model_profile = None
        todo.title = "Test task"
        todo.description = "A test task for recording"
        todo.priority = "medium"
        todo.tags = []
        todo.project_id = None

        await loop._dispatch_execute_job(todo)

        runs = recorder.list_runs()
        assert len(runs) == 1
        run_id = runs[0]
        assert run_id == "EXEC-test-todo-001"

        events = recorder.replay(run_id)
        event_types = [e["type"] for e in events]

        assert "dispatch_started" in event_types
        assert "dispatch_completed" in event_types

        dispatch_started = next(e for e in events if e["type"] == "dispatch_started")
        assert dispatch_started["todo_id"] == "test-todo-001"
        assert dispatch_started["work_type"] == "code"

        dispatch_completed = next(e for e in events if e["type"] == "dispatch_completed")
        assert dispatch_completed["success"] is True

        shutil.rmtree(tmpdir)


class TestAgentDispatcherRunRecorder:
    @pytest.mark.asyncio
    async def test_task_started_and_completed_recorded(self):
        recorder, tmpdir = _temp_recorder()

        registry = default_registry()

        async def _executor(task: AgentTask) -> str:
            await asyncio.sleep(0.001)
            return "Task output for " + task.task_id

        dispatcher = AgentDispatcher(
            registry=registry,
            executor=_executor,
            run_recorder=recorder,
        )

        task = AgentTask(
            task_id="agent-task-001",
            agent_name="explore",
            description="Test exploration task",
            prompt="Explore the codebase",
            invoker_name="build",
        )

        result = await dispatcher.dispatch_one(task)
        assert result.status == "completed"
        assert "Task output" in result.output

        runs = recorder.list_runs()
        assert len(runs) == 1
        assert runs[0] == "agent-task-001"

        events = recorder.replay("agent-task-001")
        event_types = [e["type"] for e in events]

        assert "task_started" in event_types
        assert "task_completed" in event_types

        started = next(e for e in events if e["type"] == "task_started")
        assert started["agent_name"] == "explore"
        assert started["description"] == "Test exploration task"

        completed = next(e for e in events if e["type"] == "task_completed")
        assert completed["agent_name"] == "explore"
        assert completed["output"] == "Task output for agent-task-001"
        assert completed["duration_seconds"] > 0

        shutil.rmtree(tmpdir)

    @pytest.mark.asyncio
    async def test_task_failed_recorded_when_agent_not_found(self):
        recorder, tmpdir = _temp_recorder()

        registry = default_registry()
        dispatcher = AgentDispatcher(
            registry=registry,
            run_recorder=recorder,
        )

        task = AgentTask(
            task_id="agent-task-002",
            agent_name="nonexistent-agent",
            description="Should fail",
            prompt="Try to dispatch",
            invoker_name="build",
        )

        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "not found" in result.output

        events = recorder.replay("agent-task-002")
        event_types = [e["type"] for e in events]
        assert "task_failed" in event_types
        assert len(events) == 1

        failed = events[0]
        assert "not found" in failed["reason"]
        assert failed["agent_name"] == "nonexistent-agent"

        shutil.rmtree(tmpdir)

    @pytest.mark.asyncio
    async def test_task_failed_recorded_when_executor_raises(self):
        recorder, tmpdir = _temp_recorder()

        registry = default_registry()

        async def _failing_executor(task: AgentTask) -> str:
            await asyncio.sleep(0.001)
            raise ValueError("Simulated executor failure")

        dispatcher = AgentDispatcher(
            registry=registry,
            executor=_failing_executor,
            run_recorder=recorder,
        )

        task = AgentTask(
            task_id="agent-task-003",
            agent_name="explore",
            description="Will raise",
            prompt="Try to dispatch",
            invoker_name="build",
        )

        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "Simulated executor failure" in result.output

        events = recorder.replay("agent-task-003")
        event_types = [e["type"] for e in events]

        assert "task_started" in event_types
        assert "task_failed" in event_types

        failed = next(e for e in events if e["type"] == "task_failed")
        assert "Simulated executor failure" in failed["error"]

        shutil.rmtree(tmpdir)


class TestRunRecorderReplay:
    def test_replay_returns_events_in_sequence_order(self):
        recorder, tmpdir = _temp_recorder()

        recorder.record("run-seq-001", {"type": "start", "seq": 1})
        recorder.record("run-seq-001", {"type": "middle", "seq": 2})
        recorder.record("run-seq-001", {"type": "end", "seq": 3})

        events = recorder.replay("run-seq-001")
        assert len(events) == 3
        assert [e["seq"] for e in events] == [1, 2, 3]

        shutil.rmtree(tmpdir)

    def test_replay_empty_for_unknown_run(self):
        recorder, tmpdir = _temp_recorder()
        events = recorder.replay("no-such-run")
        assert events == []
        shutil.rmtree(tmpdir)

    def test_list_runs_returns_recorded_runs(self):
        recorder, tmpdir = _temp_recorder()

        recorder.record("run-a", {"type": "start"})
        recorder.record("run-b", {"type": "start"})

        runs = recorder.list_runs()
        assert sorted(runs) == ["run-a", "run-b"]

        shutil.rmtree(tmpdir)
