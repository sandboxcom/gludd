"""Per-task (cost, quality) weight pairs consumed by the adaptive router."""

from __future__ import annotations

from typing import NamedTuple

from general_ludd.schemas.benchmark import TaskType


class RoleWeights(NamedTuple):
    cost: float
    quality: float


task_weights: dict[TaskType, RoleWeights] = {
    TaskType.SECURITY_FIX: RoleWeights(0.05, 0.95),
    TaskType.BUG_FIX: RoleWeights(0.15, 0.85),
    TaskType.DEBUGGING: RoleWeights(0.15, 0.85),
    TaskType.CODE_REVIEW: RoleWeights(0.15, 0.85),
    TaskType.FEATURE: RoleWeights(0.20, 0.80),
    TaskType.TEST_WRITE: RoleWeights(0.20, 0.80),
    TaskType.INTEGRATION: RoleWeights(0.20, 0.80),
    TaskType.OPTIMIZATION: RoleWeights(0.25, 0.75),
    TaskType.REFACTOR: RoleWeights(0.25, 0.75),
    TaskType.DOCUMENTATION: RoleWeights(0.40, 0.60),
}


_DEFAULT_WEIGHTS = RoleWeights(0.2, 0.8)


def weights_for(
    task_type: TaskType, default: RoleWeights = _DEFAULT_WEIGHTS
) -> RoleWeights:
    return task_weights.get(task_type, default)


assert set(task_weights) == set(TaskType), (
    "task_weights must cover every TaskType member"
)
assert all(
    abs(w.cost + w.quality - 1.0) < 1e-9 for w in task_weights.values()
), "each RoleWeights pair must sum to 1.0"
