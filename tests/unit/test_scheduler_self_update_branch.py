"""Phase-2 Step 5: ``self_update`` queue branch in the event-loop scheduler.

``EventLoop._dispatch_jobs_via_scheduler`` — when ``todo.queue == "self_update"``
it reconstructs the ``ApplyTier`` from the todo's ``tier:`` tag and feeds it
through ``priority.work_item_for_tier`` so code-tier self-updates serialise on
``self_update:code`` and config-tier on ``self_update:config`` (per
``priority.SELF_UPDATE_*_RESOURCE``). Non-self_update todos keep the default
``todo:<id>`` resource so existing behaviour is unchanged.

The ``work_item_for_tier`` helper itself is tested in
``test_self_update_priority.py`` (per-module naming).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers for the EventLoop scheduler-branch tests
# ---------------------------------------------------------------------------


def _make_self_update_todo(todo_id: str, tier_value: str) -> MagicMock:
    """A self_update-queue todo carrying the tags ``to_todo_spec`` would write."""
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.queue = "self_update"
    todo.work_type = "infra"
    todo.priority = "high"
    todo.title = f"su-{todo_id}"
    todo.description = ""
    todo.prompt_profile = None
    todo.model_profile = None
    todo.plan_artifact = None
    todo.tags = ["self-update", f"tier:{tier_value}"]
    type(todo).project_id = property(lambda self: None)
    return todo


def _make_core_todo(todo_id: str) -> MagicMock:
    """A regular core-queue todo (no tier tag, default ``todo:<id>`` resource)."""
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.queue = "core"
    todo.work_type = "code"
    todo.priority = "medium"
    todo.title = f"core-{todo_id}"
    todo.description = ""
    todo.prompt_profile = None
    todo.model_profile = None
    todo.plan_artifact = None
    todo.tags = []
    type(todo).project_id = property(lambda self: None)
    return todo


def _make_loop() -> Any:
    """EventLoop with the minimal attribute surface the scheduler branch needs."""
    from general_ludd.event_loop.loop import EventLoop

    loop = EventLoop(session=None, config={})
    loop._session_factory = None
    loop._config_snapshot = {}
    loop._runner = None
    loop._http_client = None
    loop._budget_guard = None
    loop._mcp_tool_registry = None
    loop._adaptive_router = None
    loop._prompt_registry = None
    loop._skill_registry = None
    loop._variable_repo = None
    loop._task_return_repo = None
    loop._active_session = None

    async def _no_op_dispatch(todo: Any, **kwargs: Any) -> None:
        return None

    loop._dispatch_execute_job = _no_op_dispatch  # type: ignore[method-assign]
    return loop


def _capture_scheduler_items() -> tuple[list[Any], Any]:
    """Return (captured-items-list, context_manager) — patch ``Scheduler.plan``
    to record the WorkItems it was called with so a test can assert against
    their resources without disturbing the dispatch path."""
    from contextlib import contextmanager
    from unittest.mock import patch

    captured: list[Any] = []

    @contextmanager
    def _patch() -> Any:
        original_plan = None

        def _recording_plan(self: Any, items: list[Any]) -> list[list[str]]:
            captured.extend(items)
            return original_plan(self, items)

        from general_ludd.scheduling.scheduler import Scheduler

        original_plan = Scheduler.plan
        with patch.object(Scheduler, "plan", _recording_plan):
            yield

    return captured, _patch()


# ---------------------------------------------------------------------------
# EventLoop._dispatch_jobs_via_scheduler — the self_update branch
# ---------------------------------------------------------------------------


class TestSchedulerSelfUpdateBranch:
    """``_dispatch_jobs_via_scheduler`` routes ``self_update``-queue todos
    through ``work_item_for_tier`` based on their ``tier:`` tag."""

    @pytest.mark.asyncio
    async def test_code_tier_self_update_uses_code_resource(self) -> None:
        loop = _make_loop()
        todos = [_make_self_update_todo("SU1", "code")]
        captured, ctx = _capture_scheduler_items()

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            return None

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]

        with ctx:
            await loop._dispatch_jobs_via_scheduler(todos)

        from general_ludd.self_update.priority import SELF_UPDATE_CODE_RESOURCE

        assert len(captured) == 1
        assert SELF_UPDATE_CODE_RESOURCE in captured[0].resources

    @pytest.mark.asyncio
    async def test_config_tier_self_update_uses_config_resource(self) -> None:
        loop = _make_loop()
        todos = [_make_self_update_todo("SU2", "config")]
        captured, ctx = _capture_scheduler_items()

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            return None

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]

        with ctx:
            await loop._dispatch_jobs_via_scheduler(todos)

        from general_ludd.self_update.priority import (
            SELF_UPDATE_CODE_RESOURCE,
            SELF_UPDATE_CONFIG_RESOURCE,
        )

        assert len(captured) == 1
        assert SELF_UPDATE_CONFIG_RESOURCE in captured[0].resources
        # Must NOT also hold the code resource — that would over-serialise.
        assert SELF_UPDATE_CODE_RESOURCE not in captured[0].resources

    @pytest.mark.asyncio
    async def test_two_code_tier_todos_serialize_into_separate_batches(
        self,
    ) -> None:
        """Two code-tier self-update todos share ``self_update:code`` → the
        scheduler must place them in separate (sequential) batches."""
        loop = _make_loop()
        todos = [
            _make_self_update_todo("C1", "code"),
            _make_self_update_todo("C2", "code"),
        ]

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            return None

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]

        captured, ctx = _capture_scheduler_items()
        with ctx:
            await loop._dispatch_jobs_via_scheduler(todos)

        assert len(captured) == 2
        # Both items share the code resource → they cannot run concurrently.
        from general_ludd.scheduling.scheduler import Scheduler

        batches = Scheduler().plan(captured)
        # Two items sharing a resource end up in two singleton batches.
        assert len(batches) == 2
        assert all(len(b) == 1 for b in batches)

    @pytest.mark.asyncio
    async def test_code_and_config_tier_can_run_concurrently(self) -> None:
        """Code + config share NO resource — they CAN run concurrently.
        (Config holds ``self_update:config``, code holds ``self_update:code``.)"""
        loop = _make_loop()
        todos = [
            _make_self_update_todo("MIX-C", "code"),
            _make_self_update_todo("MIX-K", "config"),
        ]

        async def fake_dispatch(todo: Any, **kwargs: Any) -> None:
            return None

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]

        captured, ctx = _capture_scheduler_items()
        with ctx:
            await loop._dispatch_jobs_via_scheduler(todos)

        from general_ludd.scheduling.scheduler import Scheduler

        batches = Scheduler().plan(captured)
        # They share no resource → one concurrent batch of two.
        assert len(batches) == 1
        assert len(batches[0]) == 2

    @pytest.mark.asyncio
    async def test_missing_tier_tag_fails_closed_greenfield(self) -> None:
        """A self_update todo with no ``tier:`` tag must not over-serialise:
        fail-closed to greenfield (empty resources) so it never blocks real
        work, mirroring REFUSED-tier behaviour."""
        loop = _make_loop()
        todo = _make_self_update_todo("NOTIER", "code")
        todo.tags = ["self-update"]  # no tier: tag at all
        todos = [todo]

        async def fake_dispatch(t: Any, **kwargs: Any) -> None:
            return None

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]

        captured, ctx = _capture_scheduler_items()
        with ctx:
            await loop._dispatch_jobs_via_scheduler(todos)

        assert len(captured) == 1
        assert captured[0].resources == frozenset()
        assert captured[0].is_greenfield is True

    @pytest.mark.asyncio
    async def test_unknown_tier_value_fails_closed_greenfield(self) -> None:
        """``tier:banana`` (not a valid ApplyTier) → fail-closed greenfield."""
        loop = _make_loop()
        todos = [_make_self_update_todo("BAD", "banana")]

        async def fake_dispatch(t: Any, **kwargs: Any) -> None:
            return None

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]

        captured, ctx = _capture_scheduler_items()
        with ctx:
            await loop._dispatch_jobs_via_scheduler(todos)

        assert len(captured) == 1
        assert captured[0].resources == frozenset()
        assert captured[0].is_greenfield is True

    @pytest.mark.asyncio
    async def test_non_self_update_queue_uses_default_todo_resource(self) -> None:
        """A core-queue todo must still get ``todo:<id>`` — the new branch
        only fires for ``queue == "self_update"``."""
        loop = _make_loop()
        todos = [_make_core_todo("REG1")]

        async def fake_dispatch(t: Any, **kwargs: Any) -> None:
            return None

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]

        captured, ctx = _capture_scheduler_items()
        with ctx:
            await loop._dispatch_jobs_via_scheduler(todos)

        from general_ludd.self_update.priority import (
            SELF_UPDATE_CODE_RESOURCE,
            SELF_UPDATE_CONFIG_RESOURCE,
        )

        assert len(captured) == 1
        assert "todo:REG1" in captured[0].resources
        # Must NOT pick up any self_update resource.
        assert SELF_UPDATE_CODE_RESOURCE not in captured[0].resources
        assert SELF_UPDATE_CONFIG_RESOURCE not in captured[0].resources

    @pytest.mark.asyncio
    async def test_self_update_code_concurrent_with_regular_todo(self) -> None:
        """A code-tier self_update todo and an unrelated core todo share no
        resource → they can run concurrently. Verifies the branch doesn't
        over-serialise non-overlapping work."""
        loop = _make_loop()
        todos = [
            _make_self_update_todo("SU-CODE", "code"),
            _make_core_todo("CORE-1"),
        ]

        async def fake_dispatch(t: Any, **kwargs: Any) -> None:
            return None

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]

        captured, ctx = _capture_scheduler_items()
        with ctx:
            await loop._dispatch_jobs_via_scheduler(todos)

        from general_ludd.scheduling.scheduler import Scheduler

        batches = Scheduler().plan(captured)
        assert len(batches) == 1
        assert len(batches[0]) == 2

    @pytest.mark.asyncio
    async def test_self_update_queue_with_no_tags_attribute_fails_closed(
        self,
    ) -> None:
        """A self_update todo missing the ``tags`` attribute entirely (legacy
        row) must fail closed to greenfield — never raise into the dispatch
        path."""
        loop = _make_loop()
        todo = MagicMock()
        todo.todo_id = "NOTAGS"
        todo.queue = "self_update"
        todo.work_type = "infra"
        todo.priority = "high"
        todo.title = "su-notags"
        todo.description = ""
        todo.prompt_profile = None
        todo.model_profile = None
        todo.plan_artifact = None
        type(todo).project_id = property(lambda self: None)
        # Note: no `tags` attribute set.

        async def fake_dispatch(t: Any, **kwargs: Any) -> None:
            return None

        loop._dispatch_execute_job = fake_dispatch  # type: ignore[method-assign]

        captured, ctx = _capture_scheduler_items()
        with ctx:
            await loop._dispatch_jobs_via_scheduler([todo])

        assert len(captured) == 1
        assert captured[0].resources == frozenset()
        assert captured[0].is_greenfield is True
