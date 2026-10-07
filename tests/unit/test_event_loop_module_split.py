"""Compatibility contract for the event-loop facade split."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

EXPECTED_MIXINS = {
    "tick_lifecycle": "TickLifecycleMixin",
    "review_dispatch": "ReviewDispatchMixin",
    "compute_lifecycle": "ComputeLifecycleMixin",
    "execution_dispatch": "ExecutionDispatchMixin",
    "self_improve_lifecycle": "SelfImproveLifecycleMixin",
    "decision_completion": "DecisionCompletionMixin",
}

REPRESENTATIVE_METHODS = {
    "tick_lifecycle": "tick",
    "review_dispatch": "_dispatch_review_job",
    "compute_lifecycle": "_phase_claim_runnable_todos",
    "execution_dispatch": "_dispatch_execute_job",
    "self_improve_lifecycle": "_dispatch_managed_self_improve",
    "decision_completion": "_attempt_completed_push",
}


@pytest.mark.parametrize(("module_name", "class_name"), EXPECTED_MIXINS.items())
def test_split_modules_are_cohesive_mixins(module_name: str, class_name: str) -> None:
    """Each planned W6 module owns one explicit mixin boundary."""
    module = importlib.import_module(f"general_ludd.event_loop.{module_name}")
    mixin = getattr(module, class_name)

    assert inspect.isclass(mixin)
    assert Path(inspect.getsourcefile(mixin) or "").name == f"{module_name}.py"


def test_event_loop_facade_is_thin_and_keeps_planned_mro() -> None:
    """The stable facade composes all mixins and remains below the project cap."""
    from general_ludd.event_loop.loop import EventLoop

    loop_path = Path(inspect.getsourcefile(EventLoop) or "")
    assert len(loop_path.read_text(encoding="utf-8").splitlines()) < 2500
    assert set(EXPECTED_MIXINS.values()) <= {base.__name__ for base in EventLoop.__mro__}


def test_extracted_methods_keep_facade_monkeypatch_globals() -> None:
    """Historical ``event_loop.loop`` patch points still control moved methods."""
    import general_ludd.event_loop.loop as loop_module

    for module_name, method_name in REPRESENTATIVE_METHODS.items():
        method = inspect.getattr_static(loop_module.EventLoop, method_name)
        assert inspect.isfunction(method), (module_name, method_name)
        assert method.__globals__ is vars(loop_module), (module_name, method_name)


def test_facade_retains_phase_and_routing_exports() -> None:
    """Callers importing the legacy constants and helper aliases remain valid."""
    import general_ludd.event_loop.loop as loop_module
    from general_ludd.event_loop import task_routing

    assert loop_module.PHASE_ORDER[0] == "load_config_snapshot"
    assert loop_module.PHASE_ORDER.index(
        "dispatch_execute_jobs"
    ) == loop_module.DISPATCH_PHASE_INDEX
    assert loop_module.PHASE_ORDER.index(
        "release_compute_demand"
    ) == loop_module.RELEASE_PHASE_INDEX
    assert loop_module._compute_todo_estimate is task_routing.compute_todo_estimate
    assert loop_module._format_acceptance_criteria is task_routing.format_acceptance_criteria
    assert loop_module._playbook_for_work_type is task_routing.playbook_for_work_type


@pytest.mark.parametrize("module_name", EXPECTED_MIXINS)
def test_extracted_modules_do_not_import_the_facade(module_name: str) -> None:
    """Mixin modules stay acyclic and never import their compatibility facade."""
    module = importlib.import_module(f"general_ludd.event_loop.{module_name}")
    source = Path(inspect.getsourcefile(module) or "").read_text(encoding="utf-8")

    assert "event_loop.loop import" not in source
    assert "from .loop import" not in source


def _scheduler_todo(
    todo_id: str,
    *,
    project_id: str | None,
    dependencies: list[str] | None = None,
) -> SimpleNamespace:
    """Build the smallest stable scheduler input used by compatibility tests."""
    return SimpleNamespace(
        todo_id=todo_id,
        project_id=project_id,
        queue="core",
        work_type="code",
        dependencies=dependencies or [],
    )


@pytest.mark.asyncio
async def test_scheduler_rejects_duplicate_legacy_runtime_identity() -> None:
    """Moved scheduling code keeps the legacy duplicate-identity fail-closed path."""
    from general_ludd.event_loop.loop import EventLoop

    event_loop = EventLoop(config={})
    event_loop._config_snapshot = {}
    dispatch_job = AsyncMock()
    cast(Any, event_loop)._dispatch_execute_job = dispatch_job
    duplicate = _scheduler_todo("SAME", project_id=None)

    assert await event_loop._dispatch_jobs_via_scheduler([duplicate, duplicate]) == 0
    dispatch_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_empty_batch_remains_a_noop() -> None:
    """Moved batch dispatch keeps the empty-batch fast path side-effect free."""
    from general_ludd.event_loop.loop import EventLoop

    event_loop = EventLoop(config={})
    dispatch_job = AsyncMock()
    cast(Any, event_loop)._dispatch_execute_job = dispatch_job

    assert await event_loop._dispatch_scheduler_batch([], can_concurrent=True) == 0
    dispatch_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("predecessor_status", "expected_count"), [("complete", 1), ("active", 0)])
async def test_scheduler_requires_complete_external_predecessor(
    predecessor_status: str,
    expected_count: int,
) -> None:
    """Moved scheduling code keeps scoped dependency proof behavior."""
    from general_ludd.event_loop.loop import EventLoop

    repository = SimpleNamespace(
        get_by_ids=AsyncMock(
            return_value={"PARENT": SimpleNamespace(status=predecessor_status)}
        )
    )
    event_loop = EventLoop(config={}, todo_repo=cast(Any, repository))
    event_loop._config_snapshot = {}
    dispatch_job = AsyncMock()
    cast(Any, event_loop)._dispatch_execute_job = dispatch_job
    child = _scheduler_todo(
        "CHILD",
        project_id="project-a",
        dependencies=["PARENT"],
    )

    assert await event_loop._dispatch_jobs_via_scheduler([child]) == expected_count
    if expected_count:
        dispatch_job.assert_awaited_once_with(child)
    else:
        dispatch_job.assert_not_awaited()
