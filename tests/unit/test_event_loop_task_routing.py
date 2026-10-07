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


def test_invalid_task_type_mapping_falls_back_to_feature(monkeypatch) -> None:
    """A malformed extension mapping cannot escape the stable task taxonomy."""
    monkeypatch.setitem(
        task_routing.WORK_TYPE_TASK_TYPE_MAP,
        "custom-invalid",
        "not-a-task-type",
    )

    assert task_routing.work_type_to_task_type("custom-invalid") is TaskType.FEATURE


def test_project_workspace_playbook_overrides_default(tmp_path) -> None:
    """A project-owned playbook wins only when its exact file exists."""
    playbooks_dir = tmp_path / "playbooks"
    playbooks_dir.mkdir()
    project_playbook = playbooks_dir / "analysis.yml"
    project_playbook.write_text("---\n", encoding="utf-8")
    workspace = SimpleNamespace(playbooks_dir=playbooks_dir)

    assert task_routing.playbook_for_work_type(
        "analysis",
        project_id="project-1",
        workspaces={"project-1": workspace},
    ) == str(project_playbook)
    assert task_routing.playbook_for_work_type(
        "audit",
        project_id="project-1",
        workspaces={"project-1": workspace},
    ) == "log_audit.yml"
