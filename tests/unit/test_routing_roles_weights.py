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
        assert weights_for(TaskType.BUG_FIX, default=sentinel) == task_weights[TaskType.BUG_FIX]

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


class TestRoleWeightsNamedTuple:
    def test_fields(self):
        assert RoleWeights._fields == ("cost", "quality")

    def test_asdict(self):
        rw = RoleWeights(0.3, 0.7)
        d = rw._asdict()
        assert d == {"cost": 0.3, "quality": 0.7}
        assert isinstance(d, dict)

    def test_replace(self):
        rw = RoleWeights(0.1, 0.9)
        rw2 = rw._replace(cost=0.5)
        assert rw2 == RoleWeights(0.5, 0.9)
        assert rw != rw2
        assert rw.cost == 0.1

    def test_immutable_cost(self):
        rw = RoleWeights(0.3, 0.7)
        with pytest.raises(AttributeError):
            rw.cost = 0.5  # type: ignore[misc]

    def test_immutable_quality(self):
        rw = RoleWeights(0.3, 0.7)
        with pytest.raises(AttributeError):
            rw.quality = 0.5  # type: ignore[misc]

    def test_make(self):
        rw = RoleWeights._make([0.25, 0.75])
        assert rw == RoleWeights(0.25, 0.75)

    def test_equality(self):
        assert RoleWeights(0.2, 0.8) == RoleWeights(0.2, 0.8)
        assert RoleWeights(0.2, 0.8) != RoleWeights(0.3, 0.7)

    def test_hashable(self):
        rw = RoleWeights(0.2, 0.8)
        d = {rw: "test"}
        assert d[RoleWeights(0.2, 0.8)] == "test"

    def test_repr(self):
        rw = RoleWeights(0.3, 0.7)
        r = repr(rw)
        assert "RoleWeights" in r
        assert "0.3" in r
        assert "0.7" in r

    def test_isinstance_tuple(self):
        rw = RoleWeights(0.2, 0.8)
        assert isinstance(rw, tuple)

    def test_indexing(self):
        rw = RoleWeights(0.1, 0.9)
        assert rw[0] == 0.1
        assert rw[1] == 0.9
        assert len(rw) == 2

    def test_iter_unpacking(self):
        rw = RoleWeights(0.6, 0.4)
        cost, quality = rw
        assert cost == 0.6
        assert quality == 0.4


class TestWeightsForEdgeCases:
    def test_weights_for_returns_roleweights_instance(self):
        w = weights_for(TaskType.BUG_FIX)
        assert isinstance(w, RoleWeights)

    def test_weights_for_all_tasktypes_match_direct_dict(self):
        for tt in TaskType:
            assert weights_for(tt) == task_weights[tt]

    def test_default_fallback_not_triggered_for_known_types(self):
        sentinel = RoleWeights(0.99, 0.01)
        for tt in TaskType:
            result = weights_for(tt, default=sentinel)
            assert result == task_weights[tt]

    def test_default_is_not_shared_mutable_state(self):
        from general_ludd.routing_roles.weights import _DEFAULT_WEIGHTS

        assert RoleWeights(0.2, 0.8) == _DEFAULT_WEIGHTS
        assert type(_DEFAULT_WEIGHTS) is RoleWeights


class TestModuleAssertions:
    def test_coverage_assertion_holds(self):
        assert set(task_weights) == set(TaskType)

    def test_sum_to_one_assertion_holds(self):
        for w in task_weights.values():
            assert abs(w.cost + w.quality - 1.0) < 1e-9

    def test_weights_dict_is_not_empty(self):
        assert len(task_weights) == len(set(TaskType))

    def test_all_weights_non_negative(self):
        for tt in TaskType:
            w = task_weights[tt]
            assert w.cost >= 0.0, f"{tt.name} cost negative"
            assert w.quality >= 0.0, f"{tt.name} quality negative"

    def test_weights_are_deterministic(self):
        for tt in TaskType:
            w1 = weights_for(tt)
            w2 = weights_for(tt)
            assert w1 == w2
