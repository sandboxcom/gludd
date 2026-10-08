"""Tests for the EventLoop module — importability and key public API."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_completed_delivery_uses_project_workspace_and_task_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delivery resolves the owning project and leaves trunk before commit."""
    from general_ludd.event_loop.loop import EventLoop
    from general_ludd.git_automation import repo as repo_module

    project_id = "project-delivery"
    loop = EventLoop(
        config={},
        project_workspace={project_id: SimpleNamespace(repo_dir=tmp_path)},
    )
    todo = SimpleNamespace(
        todo_id="TODO-DELIVERY",
        title="deliver verified work",
        branch_name=None,
        worktree=None,
        project_id=project_id,
    )
    git_repo = MagicMock()
    git_repo.current_branch.return_value = "main"
    git_repo.commit.return_value = "abc123"
    git_repo.push.return_value = True
    resolved_roots: list[str] = []

    def git_automation(root: str) -> MagicMock:
        resolved_roots.append(root)
        return git_repo

    async def no_pr(*_args: object) -> None:
        return None

    monkeypatch.setattr(repo_module, "GitAutomation", git_automation)
    monkeypatch.setattr(loop, "_maybe_open_pr", no_pr)

    await loop._try_commit_completed_work(todo)

    assert resolved_roots == [str(tmp_path)]
    git_repo.create_branch.assert_called_once_with("gludd/todo-delivery")
    git_repo.commit.assert_called_once_with("[TODO-DELIVERY] deliver verified work")
    git_repo.push.assert_called_once_with(branch="gludd/todo-delivery")


class TestEventLoopImports:
    def test_module_importable(self) -> None:
        from general_ludd.event_loop import loop

        assert loop is not None

    def test_event_loop_class_exists(self) -> None:
        from general_ludd.event_loop.loop import EventLoop

        assert EventLoop is not None

    def test_file_claim_conflict_exists(self) -> None:
        from general_ludd.event_loop.loop import _FileClaimConflict

        assert issubclass(_FileClaimConflict, Exception)


def test_restore_tick_checkpoint_recovers_all_durable_ledgers() -> None:
    """The extracted checkpoint helper restores each persisted tick ledger."""
    from general_ludd.event_loop.loop import EventLoop

    class Checkpointer:
        def get(self, key: str) -> dict[str, object]:
            assert key == "last_tick"
            return {
                "_tick_state": {"phase": "dispatch"},
                "_applied_decision_keys": ["decision-1"],
                "_pushed_work_keys": ["work-1"],
                "_push_retry_count": {"work-1": 2},
            }

    state = cast(
        "EventLoop",
        SimpleNamespace(
            _checkpointer=Checkpointer(),
            _tick_state={},
            _applied_decisions={},
            _pushed_work={},
            _push_retry_count={},
        ),
    )

    EventLoop._restore_tick_checkpoint(state)

    assert state._tick_state == {"phase": "dispatch"}
    assert state._applied_decisions == {"decision-1": None}
    assert state._pushed_work == {"work-1": None}
    assert state._push_retry_count == {"work-1": 2}


def test_restore_tick_checkpoint_preserves_existing_ledger_values() -> None:
    """Restoring an overlapping checkpoint must not refresh LRU entries."""
    from general_ludd.event_loop.loop import EventLoop

    existing_decision = object()
    existing_push = object()
    state = cast(
        "EventLoop",
        SimpleNamespace(
            _checkpointer=SimpleNamespace(
                get=lambda _key: {
                    "_applied_decision_keys": ["decision-1"],
                    "_pushed_work_keys": ["work-1"],
                }
            ),
            _tick_state={},
            _applied_decisions={"decision-1": existing_decision},
            _pushed_work={"work-1": existing_push},
            _push_retry_count={},
        ),
    )

    EventLoop._restore_tick_checkpoint(state)

    assert state._applied_decisions["decision-1"] is existing_decision
    assert state._pushed_work["work-1"] is existing_push


def test_record_tick_completion_persists_metrics_and_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tick finalization updates observers and both durable checkpoint keys."""
    from general_ludd.event_loop.loop import EventLoop

    class Checkpointer:
        def __init__(self) -> None:
            self.puts: list[tuple[str, dict[str, object]]] = []

        def put(self, key: str, value: dict[str, object]) -> None:
            self.puts.append((key, value))

    checkpointer = Checkpointer()
    state = cast(
        "EventLoop",
        SimpleNamespace(
            _tick_metrics={"total_ticks": 3},
            _daemon_state={},
            _checkpointer=checkpointer,
            _tick_state={"phase": "complete"},
            _applied_decisions={"decision-1": None},
            _pushed_work={"work-1": None},
            _push_retry_count={"work-1": 2},
        ),
    )
    monkeypatch.setattr(time, "monotonic", lambda: 12.5)

    EventLoop._record_tick_completion(state, "tick_3", 10.0)

    assert state._tick_metrics["tick_duration_ms"] == 2500.0
    assert state._daemon_state is not None
    assert state._daemon_state["tick_metrics"] == state._tick_metrics
    assert [key for key, _value in checkpointer.puts] == ["tick_3", "last_tick"]
    assert checkpointer.puts[0][1] == checkpointer.puts[1][1]


class TestTaskTypeHelpers:
    def test_wrapper_delegates_to_universal_routing_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Normal production routing must execute the extracted helper."""
        from general_ludd.event_loop import task_routing
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        calls: list[str] = []

        def route(work_type: str) -> TaskType:
            calls.append(work_type)
            return TaskType.DOCUMENTATION

        monkeypatch.setattr(task_routing, "work_type_to_task_type", route)

        assert _work_type_to_task_type("docs") is TaskType.DOCUMENTATION
        assert calls == ["docs"]

    def test_work_type_to_task_type_known_mapping(self) -> None:
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("generation")
        assert isinstance(result, TaskType)

    def test_work_type_to_task_type_unknown_falls_back(self) -> None:
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("nonexistent_work_type_xyz")
        assert isinstance(result, TaskType)


class TestSafeStrHelper:
    def test_safe_str_valid_attr(self) -> None:
        from general_ludd.event_loop import loop

        safe_str = loop.__dict__["_safe_str"]

        class Obj:
            name = "hello"

        result = safe_str(Obj(), "name")
        assert result == "hello"

    def test_safe_str_missing_attr_with_default(self) -> None:
        from general_ludd.event_loop import loop

        safe_str = loop.__dict__["_safe_str"]

        class Obj:
            pass

        result = safe_str(Obj(), "missing", default="fallback")
        assert result == "fallback"

    def test_safe_str_missing_attr_no_default(self) -> None:
        from general_ludd.event_loop import loop

        safe_str = loop.__dict__["_safe_str"]

        class Obj:
            pass

        result = safe_str(Obj(), "missing")
        assert result is None

    def test_safe_str_non_string_attr_returns_default(self) -> None:
        from general_ludd.event_loop import loop

        safe_str = loop.__dict__["_safe_str"]

        class Obj:
            count = 42

        result = safe_str(Obj(), "count")
        assert result is None


@pytest.mark.asyncio
async def test_shutdown_drains_cleanup_spawned_while_cancelling() -> None:
    from general_ludd.event_loop.loop import EventLoop

    event_loop = EventLoop()
    cleanup_tasks: list[asyncio.Task[None]] = []

    async def cleanup() -> None:
        await asyncio.Event().wait()

    async def work() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_task = asyncio.create_task(cleanup())
            cleanup_tasks.append(cleanup_task)
            event_loop._track_background_task(cleanup_task)

    original = asyncio.create_task(work())
    event_loop._track_background_task(original)
    await asyncio.sleep(0)

    await asyncio.wait_for(event_loop.shutdown(), timeout=1)

    assert original.cancelled()
    assert len(cleanup_tasks) == 1
    assert cleanup_tasks[0].cancelled()
    assert not event_loop._background_tasks


@pytest.mark.asyncio
async def test_drain_background_tasks_does_not_cancel_completed_task() -> None:
    """The shutdown drain must accept an already-terminal tracked task."""
    from general_ludd.event_loop.loop import EventLoop

    event_loop = EventLoop()
    completed = asyncio.create_task(asyncio.sleep(0))
    await completed
    event_loop._background_tasks.add(completed)

    await event_loop._drain_background_tasks()

    assert completed.done()
    assert not completed.cancelled()
    assert not event_loop._background_tasks


@pytest.mark.asyncio
async def test_apply_todo_envelope_without_repository_is_safe() -> None:
    """A todo envelope remains observable when no repository is wired."""
    from general_ludd.event_loop.loop import EventLoop

    event_loop = EventLoop()
    envelope = SimpleNamespace(topic="todo.upsert", payload={"todo_id": "TODO-NO-REPO"})

    await event_loop._apply_envelope(envelope, session=None)


def test_rebuild_ansible_env_callback_without_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon callback is sufficient when no direct runner is present."""
    from general_ludd.ansible import paths
    from general_ludd.event_loop.loop import EventLoop

    entries = [SimpleNamespace(path=Path("/tmp/project-collections"))]
    env = {"ANSIBLE_COLLECTIONS_PATH": "/tmp/project-collections"}
    updater = MagicMock()
    event_loop = EventLoop(ansible_env_updater=updater, runner=None)
    monkeypatch.setattr(paths, "resolve_collections_paths", lambda _root: entries)
    monkeypatch.setattr(paths, "to_ansible_env", lambda _entries: env)

    event_loop._rebuild_ansible_env_for_project(None)

    updater.assert_called_once_with(entries, env)


@pytest.mark.asyncio
async def test_release_review_claim_without_session_is_safe() -> None:
    """Review-claim release remains a no-op without durable state."""
    from general_ludd.event_loop.loop import EventLoop

    event_loop = EventLoop()
    task_return = SimpleNamespace(return_id="RETURN-NO-SESSION", todo_id=None)

    await event_loop._release_review_claim(task_return)


@pytest.mark.asyncio
async def test_dispatch_review_runner_accepts_empty_result() -> None:
    """An empty runner result terminates the runner path without HTTP fallback."""
    from general_ludd.event_loop.loop import EventLoop

    runner = MagicMock()
    runner.prepare_job_dirs.return_value = {"root": "/tmp/review-empty-result"}
    runner.run_playbook.return_value = None
    event_loop = EventLoop(runner=runner)
    task_return = SimpleNamespace(
        return_id="RETURN-EMPTY",
        todo_id="TODO-EMPTY",
        queue="model",
        project_id=None,
        plan_artifact=None,
    )

    await event_loop._dispatch_review_job(task_return)

    runner.run_playbook.assert_called_once()


@pytest.mark.asyncio
async def test_persist_review_response_without_decision_is_safe() -> None:
    """A valid response without a decision must not write a decision row."""
    from general_ludd.event_loop.loop import EventLoop

    session = MagicMock()
    session.flush = AsyncMock()
    event_loop = EventLoop(task_return_repo=MagicMock())
    event_loop._active_session = session

    await event_loop._persist_review_response(
        SimpleNamespace(return_id="RETURN-NO-DECISION"),
        {},
    )

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_sdlc_existing_artifact_and_optional_gates_pass(tmp_path: Path) -> None:
    """Existing artifacts and optional gates leave the SDLC stage unblocked."""
    from general_ludd.event_loop.loop import EventLoop

    event_loop = EventLoop(
        config={
            "ai_sdlc": {
                "enforce": True,
                "pipeline_stages": {
                    "verify": {
                        "artifact_dir": str(tmp_path),
                        "entry_gates": {"lint": {"required": False}},
                        "exit_gates": {"tests": {"required": False}},
                    }
                },
            }
        }
    )

    await event_loop._phase_sdlc_gate()

    result = event_loop._tick_state["sdlc_gate_results"]
    assert result["stages_checked"] == 1
    assert result["stages_blocked"] == 0
    assert result["stage_results"]["verify"]["entry_passed"] is True
    assert result["stage_results"]["verify"]["exit_passed"] is True


@pytest.mark.asyncio
async def test_defer_reaped_claim_without_session_skips_lease_release() -> None:
    """A reaped claim requeues safely when there is no session-held lease."""
    from general_ludd.event_loop.loop import EventLoop

    todo_repo = MagicMock()
    todo_repo.transition = AsyncMock()
    event_loop = EventLoop(todo_repo=todo_repo)
    event_loop._tick_state["reaped_todo_ids"] = {"TODO-REAPED"}
    todo = SimpleNamespace(todo_id="TODO-REAPED", version=3, queue="core")

    retained = await event_loop._defer_reaped_claims([todo], "project-1")

    assert retained == []
    todo_repo.transition.assert_awaited_once()


@pytest.mark.asyncio
async def test_allowed_budget_reaches_execute_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowed budget decision must flow through to scheduler dispatch."""
    from general_ludd.event_loop.loop import EventLoop

    budget_guard = MagicMock()
    budget_guard.check_all_limits.return_value = {"allowed": True, "reason": ""}
    event_loop = EventLoop(budget_guard=budget_guard)
    event_loop._tick_state["claimed_todos"] = []
    dispatch = AsyncMock(return_value=0)
    monkeypatch.setattr(event_loop, "_dispatch_jobs_via_scheduler", dispatch)

    await event_loop._phase_dispatch_execute_jobs()

    dispatch.assert_awaited_once_with([])
    assert event_loop._tick_metrics["todos_dispatched"] == 0


@pytest.mark.asyncio
async def test_completed_delivery_with_no_changed_files_skips_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty real diff needs no claim while delivery still completes."""
    from general_ludd.event_loop.loop import EventLoop
    from general_ludd.git_automation import repo as repo_module

    registry = MagicMock()
    git_repo = MagicMock()
    git_repo.changed_files.return_value = []
    git_repo.current_branch.return_value = "gludd/existing-task"
    git_repo.push.return_value = True
    event_loop = EventLoop(file_claim_registry=registry)
    monkeypatch.setattr(repo_module, "GitAutomation", lambda _root: git_repo)
    monkeypatch.setattr(event_loop, "_maybe_open_pr", AsyncMock())
    todo = SimpleNamespace(
        todo_id="TODO-NO-DIFF",
        title="deliver metadata-only work",
        branch_name=None,
        worktree="/tmp/no-diff-worktree",
        project_id="project-1",
    )

    await event_loop._try_commit_completed_work(todo)

    registry.claim_or_conflict.assert_not_called()
    registry.release.assert_not_called()
    git_repo.push.assert_called_once_with(branch="gludd/existing-task")
