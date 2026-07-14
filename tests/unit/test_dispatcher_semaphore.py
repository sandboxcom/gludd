"""S.7: Verify AgentDispatcher._get_semaphore is atomic and BoundedSemaphore
respects concurrency limits under concurrent dispatch_one calls."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType


def _subagent_config(
    name: str, max_concurrent: int = 2, enabled: bool = True
) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Test subagent {name}",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=[],
        ),
        max_concurrent=max_concurrent,
        enabled=enabled,
    )


def _invoker_config(name: str) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Test invoker {name}",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=True,
            allowed_subagents=["*"],
        ),
        enabled=True,
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Semaphore creation is atomic (lock-protected)
# ---------------------------------------------------------------------------


class TestSemaphoreCreationAtomic:
    def test_multiple_agents_get_distinct_semaphores(self) -> None:
        """Each distinct agent_name gets its own BoundedSemaphore."""
        registry = AgentRegistry()
        registry.register(_subagent_config("alpha", max_concurrent=2))
        registry.register(_subagent_config("beta", max_concurrent=3))

        dispatcher = AgentDispatcher(registry)

        async def _go() -> tuple[int, int]:
            sa = await dispatcher._get_semaphore("alpha")
            sb = await dispatcher._get_semaphore("beta")
            return sa._bound_value, sb._bound_value

        a_lim, b_lim = _run(_go())
        assert a_lim == 2
        assert b_lim == 3

    def test_same_agent_returns_cached_semaphore(self) -> None:
        """Repeated calls for the same agent return the exact same object."""
        registry = AgentRegistry()
        registry.register(_subagent_config("gamma", max_concurrent=4))
        dispatcher = AgentDispatcher(registry)

        async def _go() -> bool:
            s1 = await dispatcher._get_semaphore("gamma")
            s2 = await dispatcher._get_semaphore("gamma")
            return s1 is s2

        assert _run(_go()) is True

    def test_concurrent_creation_is_atomic(self) -> None:
        """Many concurrent coroutines creating the same semaphore must all
        receive the identical object — no duplicate BoundedSemaphore instances."""
        registry = AgentRegistry()
        registry.register(_subagent_config("shared", max_concurrent=5))
        dispatcher = AgentDispatcher(registry)

        async def _concurrent_creators() -> set[int]:
            async def _get_id() -> int:
                s = await dispatcher._get_semaphore("shared")
                return id(s)

            ids = await asyncio.gather(*[_get_id() for _ in range(50)])
            return set(ids)

        ids = _run(_concurrent_creators())
        assert len(ids) == 1, (
            f"Expected exactly 1 semaphore object; got {len(ids)} distinct ids"
        )


# ---------------------------------------------------------------------------
# Concurrent acquires respect the limit
# ---------------------------------------------------------------------------


class TestConcurrencyLimitRespected:
    def test_max_concurrent_respected(self) -> None:
        """When max_concurrent=2 and 10 tasks are dispatched, at most 2 run
        concurrently.  Measured by peak in-flight count from a tracking executor."""
        registry = AgentRegistry()
        invoker = "trusted"
        registry.register(_invoker_config(invoker))
        registry.register(_subagent_config("bottleneck", max_concurrent=2))

        peak: list[int] = [0]
        inflight: list[int] = [0]
        lock = asyncio.Lock()

        async def _tracking_executor(task: AgentTask) -> str:
            nonlocal inflight, peak
            async with lock:
                inflight[0] += 1
                peak[0] = max(peak[0], inflight[0])
            await asyncio.sleep(0.01)
            async with lock:
                inflight[0] -= 1
            return f"done:{task.task_id}"

        dispatcher = AgentDispatcher(registry, executor=_tracking_executor)

        async def _go() -> None:
            tasks = [
                asyncio.ensure_future(
                    dispatcher.dispatch_one(
                        AgentTask(
                            task_id=f"t{i}",
                            agent_name="bottleneck",
                            description=f"task {i}",
                            prompt="run",
                            invoker_name=invoker,
                        )
                    )
                )
                for i in range(10)
            ]
            await asyncio.gather(*tasks)

        _run(_go())
        assert peak[0] <= 2, (
            f"Expected peak concurrency ≤ 2, got {peak[0]}"
        )
        assert peak[0] >= 1, (
            f"Expected at least 1 concurrent task, got peak {peak[0]}"
        )

    def test_limit_one_serializes(self) -> None:
        """max_concurrent=1 forces full serialization."""
        registry = AgentRegistry()
        invoker = "trusted"
        registry.register(_invoker_config(invoker))
        registry.register(_subagent_config("serial", max_concurrent=1))

        peak: list[int] = [0]
        inflight: list[int] = [0]
        lock = asyncio.Lock()

        async def _serial_executor(task: AgentTask) -> str:
            async with lock:
                inflight[0] += 1
                peak[0] = max(peak[0], inflight[0])
            await asyncio.sleep(0.005)
            async with lock:
                inflight[0] -= 1
            return f"done:{task.task_id}"

        dispatcher = AgentDispatcher(registry, executor=_serial_executor)

        async def _go() -> None:
            tasks = [
                asyncio.ensure_future(
                    dispatcher.dispatch_one(
                        AgentTask(
                            task_id=f"s{i}",
                            agent_name="serial",
                            description=f"task {i}",
                            prompt="run",
                            invoker_name=invoker,
                        )
                    )
                )
                for i in range(5)
            ]
            await asyncio.gather(*tasks)

        _run(_go())
        assert peak[0] == 1, (
            f"Expected peak concurrency exactly 1, got {peak[0]}"
        )

    def test_different_agents_dont_share_semaphore(self) -> None:
        """Two agents each with max_concurrent=1 can still run concurrently
        because they have independent semaphores."""
        registry = AgentRegistry()
        invoker = "trusted"
        registry.register(_invoker_config(invoker))
        registry.register(_subagent_config("indie_a", max_concurrent=1))
        registry.register(_subagent_config("indie_b", max_concurrent=1))

        max_total: list[int] = [0]
        inflight: list[int] = [0]
        lock = asyncio.Lock()

        async def _multi_executor(task: AgentTask) -> str:
            async with lock:
                inflight[0] += 1
                max_total[0] = max(max_total[0], inflight[0])
            await asyncio.sleep(0.01)
            async with lock:
                inflight[0] -= 1
            return f"done:{task.agent_name}:{task.task_id}"

        dispatcher = AgentDispatcher(registry, executor=_multi_executor)

        async def _go() -> None:
            tasks: list[asyncio.Task[object]] = []
            for i in range(4):
                tasks.append(
                    asyncio.ensure_future(
                        dispatcher.dispatch_one(
                            AgentTask(
                                task_id=f"a{i}",
                                agent_name="indie_a",
                                description=f"indie_a task {i}",
                                prompt="run",
                                invoker_name=invoker,
                            )
                        )
                    )
                )
                tasks.append(
                    asyncio.ensure_future(
                        dispatcher.dispatch_one(
                            AgentTask(
                                task_id=f"b{i}",
                                agent_name="indie_b",
                                description=f"indie_b task {i}",
                                prompt="run",
                                invoker_name=invoker,
                            )
                        )
                    )
                )
            await asyncio.gather(*tasks)

        _run(_go())
        assert max_total[0] >= 2, (
            f"Expected ≥2 concurrent tasks (one from each agent), got {max_total[0]}"
        )


# ---------------------------------------------------------------------------
# Release works — capacity is freed after completion
# ---------------------------------------------------------------------------


class TestReleaseFreesCapacity:
    def test_release_frees_slot(self) -> None:
        """After a task completes, its semaphore slot is released and a new
        task can acquire it.  Verify that all dispatched tasks eventually
        complete (none deadlock)."""
        registry = AgentRegistry()
        invoker = "trusted"
        registry.register(_invoker_config(invoker))
        registry.register(_subagent_config("release_test", max_concurrent=2))

        completions: list[str] = []

        async def _quick_executor(task: AgentTask) -> str:
            completions.append(task.task_id)
            return f"ok:{task.task_id}"

        dispatcher = AgentDispatcher(registry, executor=_quick_executor)

        async def _go() -> list[str]:
            results = await asyncio.gather(
                *[
                    dispatcher.dispatch_one(
                        AgentTask(
                            task_id=f"r{i}",
                            agent_name="release_test",
                            description=f"release task {i}",
                            prompt="run",
                            invoker_name=invoker,
                        )
                    )
                    for i in range(8)
                ]
            )
            return [r.status for r in results]

        statuses = _run(_go())
        assert len(completions) == 8, (
            f"Expected 8 completions, got {len(completions)}"
        )
        assert all(s == "completed" for s in statuses), (
            f"All should be 'completed', got {statuses}"
        )


# ---------------------------------------------------------------------------
# BoundedSemaphore release-without-acquire raises
# ---------------------------------------------------------------------------


class TestBoundedSemaphoreGuard:
    def test_release_without_acquire_raises(self) -> None:
        """BoundedSemaphore.release() on an unacquired semaphore raises
        ValueError.  This catches programming errors where release() is called
        more times than acquire()."""
        registry = AgentRegistry()
        registry.register(_subagent_config("bounded", max_concurrent=1))
        dispatcher = AgentDispatcher(registry)

        async def _go() -> None:
            s = await dispatcher._get_semaphore("bounded")
            with pytest.raises(ValueError, match=r"BoundedSemaphore.*release"):
                s.release()

        _run(_go())

    def test_acquire_then_release_is_allowed(self) -> None:
        """Normal acquire → release cycle within the bound is fine."""
        registry = AgentRegistry()
        registry.register(_subagent_config("bounded2", max_concurrent=2))
        dispatcher = AgentDispatcher(registry)

        async def _go() -> None:
            s = await dispatcher._get_semaphore("bounded2")
            await s.acquire()
            s.release()
            async with s:
                pass

        _run(_go())
