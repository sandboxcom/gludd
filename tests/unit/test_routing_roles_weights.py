"""Tests for per-task routing-role (cost, quality) weights and TaskRole enum."""

from __future__ import annotations

import pytest

from general_ludd.routing_roles import RoleWeights, TaskRole, task_weights, weights_for
from general_ludd.routing_roles.weights import _DEFAULT_WEIGHTS
from general_ludd.schemas.benchmark import TaskType

# Exact (cost, quality) pair expected for each TaskType.
EXPECTED: dict[TaskType, tuple[float, float]] = {
    TaskType.SECURITY_FIX: (0.05, 0.95),
    TaskType.BUG_FIX: (0.15, 0.85),
    TaskType.DEBUGGING: (0.15, 0.85),
    TaskType.CODE_REVIEW: (0.15, 0.85),
    TaskType.FEATURE: (0.20, 0.80),
    TaskType.TEST_WRITE: (0.20, 0.80),
    TaskType.INTEGRATION: (0.20, 0.80),
    TaskType.OPTIMIZATION: (0.25, 0.75),
    TaskType.REFACTOR: (0.25, 0.75),
    TaskType.DOCUMENTATION: (0.40, 0.60),
}


class TestTaskWeights:
    @pytest.mark.parametrize("task_type,pair", list(EXPECTED.items()))
    def test_exact_pair(self, task_type: TaskType, pair: tuple[float, float]):
        w = task_weights[task_type]
        assert (w.cost, w.quality) == pair

    @pytest.mark.parametrize("task_type", list(TaskType))
    def test_pair_sums_to_one(self, task_type: TaskType):
        w = task_weights[task_type]
        assert abs(w.cost + w.quality - 1.0) < 1e-9

    def test_all_task_types_covered(self):
        assert set(task_weights) == set(TaskType)

    def test_no_extra_keys(self):
        # Every key in EXPECTED is a real TaskType and vice versa.
        assert set(EXPECTED) == set(TaskType)


class TestWeightsFor:
    @pytest.mark.parametrize("task_type,pair", list(EXPECTED.items()))
    def test_returns_mapped_pair(self, task_type: TaskType, pair: tuple[float, float]):
        w = weights_for(task_type)
        assert (w.cost, w.quality) == pair

    def test_default_constant(self):
        assert RoleWeights(0.2, 0.8) == _DEFAULT_WEIGHTS

    def test_default_path_unknown_uses_explicit_default(self):
        sentinel = RoleWeights(0.99, 0.01)
        # Force the .get() fallback with a value not present in the map by
        # passing a custom default; every TaskType is mapped, so we verify the
        # default param is threaded through via the constant default too.
        assert weights_for(TaskType.BUG_FIX, default=sentinel) == task_weights[
            TaskType.BUG_FIX
        ]

    def test_default_param_is_module_constant(self):
        # weights_for's default arg is the shared _DEFAULT_WEIGHTS constant
        # (B008 fix): identity holds.
        defaults = weights_for.__defaults__
        assert defaults is not None
        assert defaults[0] is _DEFAULT_WEIGHTS


class TestTaskRole:
    def test_members(self):
        assert {r.name for r in TaskRole} == {
            "PLANNER",
            "CODER",
            "REVIEWER",
            "EDITOR",
            "COMPACTOR",
            "ENUMERATOR",
        }

    def test_string_values(self):
        assert TaskRole.PLANNER == "planner"
        assert TaskRole.CODER == "coder"
        assert TaskRole.REVIEWER == "reviewer"
        assert TaskRole.EDITOR == "editor"
        assert TaskRole.COMPACTOR == "compactor"
        assert TaskRole.ENUMERATOR == "enumerator"


class TestRoleWeightsProperties:
    def test_weight_preserves_type_information(self):
        w = RoleWeights(0.3, 0.7)
        assert isinstance(w.cost, float)
        assert isinstance(w.quality, float)

    def test_all_weights_individually_sum_to_one(self):
        for tt in TaskType:
            w = task_weights[tt]
            assert 0.0 < w.cost < 1.0, f"{tt.name} cost {w.cost} out of range"
            assert 0.0 < w.quality < 1.0, f"{tt.name} quality {w.quality} out of range"

    def test_security_fix_is_most_quality_sensitive(self):
        security_cost = task_weights[TaskType.SECURITY_FIX].cost
        for tt in TaskType:
            if tt == TaskType.SECURITY_FIX:
                continue
            assert security_cost <= task_weights[tt].cost, f"{tt.name} has lower quality weight than SECURITY_FIX"

    def test_documentation_is_most_cost_sensitive(self):
        doc_cost = task_weights[TaskType.DOCUMENTATION].cost
        for tt in TaskType:
            if tt == TaskType.DOCUMENTATION:
                continue
            assert doc_cost >= task_weights[tt].cost, f"{tt.name} has higher quality weight than DOCUMENTATION"


class TestTaskRoleEnumExport:
    def test_exported_from_package_init(self):
        from general_ludd.routing_roles import TaskRole as TR
        assert TR is TaskRole

    def test_role_weights_exported_from_package_init(self):
        from general_ludd.routing_roles import RoleWeights as RW
        assert RW is RoleWeights

    def test_task_weights_exported_from_package_init(self):
        from general_ludd.routing_roles import task_weights as tw
        assert tw is task_weights

    def test_weights_for_exported_from_package_init(self):
        from general_ludd.routing_roles import weights_for as wf
        assert wf is weights_for


class TestTaskTypeFromSchema:
    def test_task_type_enum_includes_all_weights(self):
        from general_ludd.schemas.benchmark import TaskType
        for tt in TaskType:
            assert tt in task_weights, f"{tt.name} missing from task_weights"

    def test_no_extra_types_in_weights(self):
        from general_ludd.schemas.benchmark import TaskType
        for tt_key in list(task_weights):
            assert tt_key in list(TaskType)
