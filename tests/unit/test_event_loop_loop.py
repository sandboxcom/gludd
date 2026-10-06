"""Tests for the EventLoop module — importability and key public API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

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


def test_record_tick_completion_persists_metrics_and_checkpoint(monkeypatch) -> None:
    """Tick finalization updates observers and both durable checkpoint keys."""
    from general_ludd.event_loop import loop
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
    monkeypatch.setattr(loop.time, "monotonic", lambda: 12.5)

    EventLoop._record_tick_completion(state, "tick_3", 10.0)

    assert state._tick_metrics["tick_duration_ms"] == 2500.0
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
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            name = "hello"

        result = _safe_str(Obj(), "name")
        assert result == "hello"

    def test_safe_str_missing_attr_with_default(self) -> None:
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            pass

        result = _safe_str(Obj(), "missing", default="fallback")
        assert result == "fallback"

    def test_safe_str_missing_attr_no_default(self) -> None:
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            pass

        result = _safe_str(Obj(), "missing")
        assert result is None

    def test_safe_str_non_string_attr_returns_default(self) -> None:
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            count = 42

        result = _safe_str(Obj(), "count")
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
