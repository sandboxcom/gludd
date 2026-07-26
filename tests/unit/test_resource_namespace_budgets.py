"""Regression tests for namespaced scheduling and bounded worker admission.

The scheduler treats each resource label as an exclusive lease.  Keeping
project, model, SearX, and Terraform labels in the work item makes their
contention boundaries explicit without sharing a process-wide counter.  The
pipeline's ``max_worktrees`` remains the hard admission ceiling when the
system is overloaded.
"""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

from general_ludd.pipeline.lanes import DispatchLane
from general_ludd.pipeline.state import LaneState, PipelineConfig
from general_ludd.scheduling.scheduler import Scheduler, WorkItem


def _namespaced_item(item_id: str, suffix: str) -> WorkItem:
    """Build one worker lease spanning every independently bounded namespace."""
    return WorkItem(
        id=item_id,
        resources=frozenset(
            {
                f"project:{suffix}",
                f"model:{suffix}",
                f"searx:{suffix}",
                f"terraform:{suffix}",
            }
        ),
    )


def test_disjoint_project_model_searx_terraform_namespaces_share_a_batch() -> None:
    """Independent leases do not serialize unrelated projects or workers."""
    items = [
        _namespaced_item("worker-alpha", "alpha"),
        _namespaced_item("worker-beta", "beta"),
    ]

    assert Scheduler().plan(items) == [["worker-alpha", "worker-beta"]]


@pytest.mark.parametrize(
    ("resource_kind", "resource_value"),
    [
        ("project", "shared-project"),
        ("model", "shared-model"),
        ("searx", "shared-searx"),
        ("terraform", "shared-terraform"),
    ],
)
def test_each_namespace_is_an_independent_contention_boundary(
    resource_kind: str, resource_value: str
) -> None:
    """Only a colliding namespace serializes; unrelated leases stay concurrent."""
    shared = f"{resource_kind}:{resource_value}"
    first = WorkItem(
        id="first",
        resources=frozenset({shared, "project:first", "model:first"}),
    )
    colliding = WorkItem(
        id="colliding",
        resources=frozenset({shared, "project:second", "model:second"}),
    )
    independent = WorkItem(
        id="independent",
        resources=frozenset({"project:independent", "model:independent"}),
    )

    batches = Scheduler().plan([first, colliding, independent])

    assert batches == [["first", "independent"], ["colliding"]]


@pytest.mark.asyncio
async def test_overloaded_namespace_admission_refuses_without_dispatching() -> None:
    """The hard worktree ceiling refuses overload deterministically."""
    state = LaneState(
        running={"project:alpha/worker", "project:beta/worker"},
        pending=deque(["project:gamma/worker"]),
    )
    config = PipelineConfig(floor=0, target=2, max_worktrees=2)
    attempted: list[str] = []

    async def dispatch(uid: str) -> None:
        attempted.append(uid)

    lane = DispatchLane(config, state, asyncio.Lock(), dispatch)

    first = await lane.step()
    second = await lane.step()

    assert lane.backpressured() is True
    assert first == second == []
    assert attempted == []
    assert list(state.pending) == ["project:gamma/worker"]
    assert state.running == {"project:alpha/worker", "project:beta/worker"}

