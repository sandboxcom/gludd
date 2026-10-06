"""Contracts for the event-loop task-routing boundary."""

from __future__ import annotations

from types import SimpleNamespace

import general_ludd.event_loop.task_routing as task_routing
from general_ludd.event_loop import loop
from general_ludd.schemas.benchmark import TaskType


def test_loop_preserves_task_routing_compatibility_exports() -> None:
    """Existing callers keep the loop imports while implementation is split."""
    assert loop._format_acceptance_criteria is task_routing.format_acceptance_criteria
    assert loop._playbook_for_work_type is task_routing.playbook_for_work_type
    assert loop._compute_todo_estimate is task_routing.compute_todo_estimate
    assert loop._work_type_to_task_type("bug_fix") == task_routing.work_type_to_task_type(
        "bug_fix"
    )


def test_task_routing_preserves_representative_behavior() -> None:
    """The extracted boundary retains formatting, mapping, and cost behavior."""
    assert task_routing.format_acceptance_criteria('["first", "second"]') == (
        "- first\n- second"
    )
    assert task_routing.work_type_to_task_type("bug_fix") is TaskType.BUG_FIX
    assert task_routing.playbook_for_work_type("test") == "molecule_test.yml"
    todo = SimpleNamespace(resource_profile="high_resource", confidence=0.5)
    assert task_routing.compute_todo_estimate(todo) == 1.0
