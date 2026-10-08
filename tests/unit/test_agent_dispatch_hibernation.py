"""Runtime acceptance for parent hibernation around nested dispatch batches."""

from __future__ import annotations

import asyncio
import gc
import threading
import weakref
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar, cast

import pytest

from general_ludd.agents.context import ContextMessage
from general_ludd.agents.dispatcher import AgentDispatcher, AgentTaskResult
from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    HibernationController,
    HibernationHandle,
    HibernationStore,
    TokenCreds,
    TokenReviver,
)
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask, AgentType


def _registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentConfig(
            name="root",
            description="root",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_dispatch_subagents=True,
                allowed_subagents=["parent"],
            ),
        )
    )
    registry.register(
        AgentConfig(
            name="parent",
            description="nested dispatcher",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(
                can_dispatch_subagents=True,
                allowed_subagents=["child"],
            ),
        )
    )
    registry.register(
        AgentConfig(
            name="child",
            description="leaf",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(),
            max_concurrent=4,
        )
    )
    registry.seal()
    return registry


def _messages(count: int = 8, *, content: str | None = None) -> list[ContextMessage]:
    return [
        ContextMessage(
            role="user" if index % 2 else "assistant",
            content=content if content is not None else f"context-{index}-" + "x" * 256,
            token_estimate=64,
            timestamp=float(index),
        )
        for index in range(count)
    ]


def _parent(*, depth: int = 3, messages: list[ContextMessage] | None = None) -> AgentTask:
    return AgentTask(
        task_id="parent-task",
        agent_name="parent",
        description="coordinate children",
        prompt="fan out",
        invoker_name="root",
        project_id="project-one",
        depth=depth,
        messages=list(messages if messages is not None else _messages()),
        env={"S23": "present"},
    )


def _children(parent_ids: tuple[str, ...] = ("parent-task", "parent-task")) -> list[AgentTask]:
    return [
        AgentTask(
            task_id=f"child-{index}",
            agent_name="child",
            description="leaf work",
            prompt="wait",
            parent_task_id=parent_id,
            invoker_name="parent",
            project_id="project-one",
            depth=4,
        )
        for index, parent_id in enumerate(parent_ids)
    ]


def _snapshot_bytes(task: AgentTask) -> bytes:
    return AgentEnvironmentSnapshot(
        task_id=task.task_id,
        agent_name=task.agent_name,
        parent_task_id=task.parent_task_id,
        invoker_name=task.invoker_name,
        depth=task.depth,
        messages=cast(list[ContextMessage], list(task.messages)),
        scratch={
            "description": task.description,
            "prompt": task.prompt,
            "project_id": task.project_id or "",
        },
    ).model_dump_json().encode("utf-8")


class CountingStore(HibernationStore):
    """Count durable snapshots without replacing the real JSON/HMAC store."""

    def __init__(self, base_dir: Path) -> None:
        super().__init__(base_dir)
        self.dehydrate_count = 0

    def dehydrate_bounded(
        self,
        snap: AgentEnvironmentSnapshot,
        max_payload_bytes: int,
    ) -> HibernationHandle | None:
        handle = super().dehydrate_bounded(snap, max_payload_bytes)
        if handle is not None:
            self.dehydrate_count += 1
        return handle


class ThreadTrackingStore(CountingStore):
    """Record the threads used by snapshot disk operations."""

    def __init__(self, base_dir: Path) -> None:
        super().__init__(base_dir)
        self.io_thread_ids: list[int] = []

    def dehydrate_bounded(
        self,
        snap: AgentEnvironmentSnapshot,
        max_payload_bytes: int,
    ) -> HibernationHandle | None:
        self.io_thread_ids.append(threading.get_ident())
        return super().dehydrate_bounded(snap, max_payload_bytes)

    def hydrate(self, handle: HibernationHandle) -> AgentEnvironmentSnapshot:
        self.io_thread_ids.append(threading.get_ident())
        return super().hydrate(handle)

    def discard(self, handle: HibernationHandle) -> None:
        self.io_thread_ids.append(threading.get_ident())
        super().discard(handle)


class ReferenceTrackingStore(CountingStore):
    """Expose only a weak reference to the most recently serialized snapshot."""

    snapshot_ref: ClassVar[weakref.ReferenceType[AgentEnvironmentSnapshot] | None] = None

    def dehydrate_bounded(
        self,
        snap: AgentEnvironmentSnapshot,
        max_payload_bytes: int,
    ) -> HibernationHandle | None:
        type(self).snapshot_ref = weakref.ref(snap)
        return super().dehydrate_bounded(snap, max_payload_bytes)


class RecordingTokenReviver:
    """Provide deterministic credentials and retain invocation evidence."""

    def __init__(self) -> None:
        self.task_ids: list[str] = []

    async def revive(self, task_id: str) -> TokenCreds:
        self.task_ids.append(task_id)
        return TokenCreds(role_id="role", secret_id="secret")


async def _run_nested(
    tmp_path: Path,
    *,
    parent: AgentTask | None = None,
    parent_ids: tuple[str, ...] = ("parent-task", "parent-task"),
    controller: bool = True,
    child_action: Callable[[AgentTask], Awaitable[str]] | None = None,
    timeout: float = 30.0,
    store_type: type[CountingStore] = CountingStore,
    token_reviver: TokenReviver | None = None,
) -> tuple[AgentTask, CountingStore, list[AgentTaskResult], bool]:
    task = parent or _parent()
    store = store_type(tmp_path)
    hibernation = (
        HibernationController(store, token_reviver=token_reviver) if controller else None
    )
    children_started = asyncio.Event()
    release_children = asyncio.Event()
    results: list[AgentTaskResult] = []
    started_count = 0

    async def default_child(_child: AgentTask) -> str:
        await release_children.wait()
        return "leaf-complete"

    action = child_action or default_child

    async def execute(current: AgentTask) -> str:
        nonlocal started_count
        if current.agent_name == "parent":
            results.extend(
                await dispatcher.dispatch_many(_children(parent_ids), timeout=timeout)
            )
            return "parent-complete"
        started_count += 1
        if started_count == len(parent_ids):
            children_started.set()
        return await action(current)

    dispatcher = AgentDispatcher(
        _registry(),
        executor=execute,
        hibernation=hibernation,
    )
    future = asyncio.create_task(dispatcher.dispatch_one(task))
    await asyncio.wait_for(children_started.wait(), timeout=1.0)
    parked_during_wait = bool(list(tmp_path.glob("*.snapshot.json")))
    state_released_during_wait = task.messages == []
    release_children.set()
    results.append(await future)
    return task, store, results, parked_during_wait and state_released_during_wait


class TestNestedDispatchParking:
    async def test_parked_parent_releases_snapshot_object_graph(
        self,
        tmp_path: Path,
    ) -> None:
        ReferenceTrackingStore.snapshot_ref = None

        async def verify_snapshot_released(_child: AgentTask) -> str:
            gc.collect()
            snapshot_ref = ReferenceTrackingStore.snapshot_ref
            assert snapshot_ref is not None
            assert snapshot_ref() is None
            return "leaf-complete"

        _parent_task, store, results, parked = await _run_nested(
            tmp_path,
            parent_ids=("parent-task",),
            child_action=verify_snapshot_released,
            store_type=ReferenceTrackingStore,
        )

        assert parked is True
        assert store.dehydrate_count == 1
        assert [result.status for result in results] == ["completed", "completed"]

    async def test_depth_three_common_parent_parks_once_and_restores(self, tmp_path: Path) -> None:
        parent = _parent()
        before = _snapshot_bytes(parent)

        restored, store, results, parked = await _run_nested(tmp_path, parent=parent)

        assert parked is True
        assert store.dehydrate_count == 1
        assert _snapshot_bytes(restored) == before
        assert [result.status for result in results[:-1]] == ["completed", "completed"]
        assert results[-1].status == "completed"
        assert list(tmp_path.glob("*.snapshot.json")) == []

    async def test_child_error_restores_parent_and_cleans_snapshot(self, tmp_path: Path) -> None:
        parent = _parent()
        before = _snapshot_bytes(parent)
        started = asyncio.Event()

        async def fail(_child: AgentTask) -> str:
            started.set()
            raise RuntimeError("child failed")

        restored, store, results, _parked = await _run_nested(
            tmp_path,
            parent=parent,
            parent_ids=("parent-task",),
            child_action=fail,
        )

        assert started.is_set()
        assert store.dehydrate_count == 1
        assert results[0].status == "failed"
        assert _snapshot_bytes(restored) == before
        assert list(tmp_path.glob("*.snapshot.json")) == []

    async def test_timeout_restores_parent_and_cleans_snapshot(self, tmp_path: Path) -> None:
        parent = _parent()
        before = _snapshot_bytes(parent)
        started = asyncio.Event()

        async def never_finishes(_child: AgentTask) -> str:
            started.set()
            await asyncio.Event().wait()
            return "unreachable"

        restored, store, results, _parked = await _run_nested(
            tmp_path,
            parent=parent,
            parent_ids=("parent-task",),
            child_action=never_finishes,
            timeout=0.01,
        )

        assert started.is_set()
        assert store.dehydrate_count == 1
        assert results[0].status == "failed"
        assert results[0].output == "dispatch timed out"
        assert _snapshot_bytes(restored) == before
        assert list(tmp_path.glob("*.snapshot.json")) == []

    async def test_cancellation_restores_parent_and_cleans_snapshot(self, tmp_path: Path) -> None:
        parent = _parent()
        before = _snapshot_bytes(parent)
        store = CountingStore(tmp_path)
        started = asyncio.Event()

        async def execute(current: AgentTask) -> str:
            if current.agent_name == "parent":
                await dispatcher.dispatch_many(_children(("parent-task",)))
                return "unreachable"
            started.set()
            await asyncio.Event().wait()
            return "unreachable"

        dispatcher = AgentDispatcher(
            _registry(),
            executor=execute,
            hibernation=HibernationController(store),
        )
        future = asyncio.create_task(dispatcher.dispatch_one(parent))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert parent.messages == []
        assert len(list(tmp_path.glob("*.snapshot.json"))) == 1

        future.cancel()
        with pytest.raises(asyncio.CancelledError):
            await future

        assert store.dehydrate_count == 1
        assert _snapshot_bytes(parent) == before
        assert list(tmp_path.glob("*.snapshot.json")) == []

    async def test_snapshot_io_runs_off_the_event_loop_thread(self, tmp_path: Path) -> None:
        event_loop_thread = threading.get_ident()

        _parent_task, raw_store, _results, parked = await _run_nested(
            tmp_path,
            store_type=ThreadTrackingStore,
        )

        store = cast(ThreadTrackingStore, raw_store)
        assert parked is True
        assert len(store.io_thread_ids) == 3
        assert all(thread_id != event_loop_thread for thread_id in store.io_thread_ids)

    async def test_existing_token_reviver_runs_after_parent_restore(
        self,
        tmp_path: Path,
    ) -> None:
        reviver = RecordingTokenReviver()

        parent, store, _results, parked = await _run_nested(
            tmp_path,
            token_reviver=reviver,
        )

        assert parked is True
        assert store.dehydrate_count == 1
        assert reviver.task_ids == [parent.task_id]
        assert list(tmp_path.glob("*.snapshot.json")) == []


class TestNestedDispatchBypasses:
    def test_non_positive_snapshot_limits_are_rejected(self, tmp_path: Path) -> None:
        store = HibernationStore(tmp_path)
        snapshot = AgentEnvironmentSnapshot(
            task_id="parent-task",
            agent_name="parent",
            depth=3,
            messages=_messages(),
        )

        with pytest.raises(ValueError, match="max_payload_bytes must be positive"):
            store.dehydrate_bounded(snapshot, 0)
        with pytest.raises(ValueError, match="max_snapshot_bytes must be positive"):
            HibernationController(store, max_snapshot_bytes=0)

    @pytest.mark.parametrize(
        ("parent", "controller"),
        [
            pytest.param(_parent(), False, id="absent-controller"),
            pytest.param(_parent(depth=2), True, id="below-depth"),
            pytest.param(_parent(messages=_messages(7)), True, id="below-message-count"),
        ],
    )
    async def test_ineligible_parent_stays_resident(
        self,
        tmp_path: Path,
        parent: AgentTask,
        controller: bool,
    ) -> None:
        before = _snapshot_bytes(parent)

        restored, store, _results, parked = await _run_nested(
            tmp_path,
            parent=parent,
            controller=controller,
        )

        assert parked is False
        assert store.dehydrate_count == 0
        assert _snapshot_bytes(restored) == before
        assert list(tmp_path.glob("*.snapshot.json")) == []

    async def test_mixed_parent_batch_bypasses_hibernation(self, tmp_path: Path) -> None:
        parent = _parent()
        before = _snapshot_bytes(parent)

        restored, store, _results, parked = await _run_nested(
            tmp_path,
            parent=parent,
            parent_ids=("parent-task", "different-parent"),
        )

        assert parked is False
        assert store.dehydrate_count == 0
        assert _snapshot_bytes(restored) == before
        assert list(tmp_path.glob("*.snapshot.json")) == []

    async def test_empty_parent_task_id_bypasses_hibernation(self, tmp_path: Path) -> None:
        parent = _parent()
        parent.task_id = ""
        before = _snapshot_bytes(parent)

        restored, store, _results, parked = await _run_nested(
            tmp_path,
            parent=parent,
            parent_ids=("", ""),
        )

        assert parked is False
        assert store.dehydrate_count == 0
        assert _snapshot_bytes(restored) == before
        assert list(tmp_path.glob("*.snapshot.json")) == []

    async def test_invalid_parent_message_stays_resident(self, tmp_path: Path) -> None:
        parent = _parent()
        invalid_messages = [object() for _index in range(8)]
        parent.messages = invalid_messages

        restored, store, _results, parked = await _run_nested(tmp_path, parent=parent)

        assert parked is False
        assert store.dehydrate_count == 0
        assert restored.messages == invalid_messages
        assert list(tmp_path.glob("*.snapshot.json")) == []

    async def test_snapshot_over_16_mib_stays_resident(self, tmp_path: Path) -> None:
        parent = _parent(messages=_messages(8, content="x" * (2 * 1024 * 1024 + 1)))
        before = _snapshot_bytes(parent)

        restored, store, _results, parked = await _run_nested(tmp_path, parent=parent)

        assert parked is False
        assert store.dehydrate_count == 0
        assert _snapshot_bytes(restored) == before
        assert list(tmp_path.glob("*.snapshot.json")) == []
