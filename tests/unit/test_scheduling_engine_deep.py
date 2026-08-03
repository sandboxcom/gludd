"""Deep scheduling engine tests — WorkItem, can_run_concurrently, Scheduler.

Coverage: frozen dataclass invariants, concurrent-safety decisions, topological
batch partitioning, cycle detection, unknown-dependency validation, greenfield
semantics, deterministic output, multi-tier dependency chains, ComputeSchedulingHint
GPU affinity, OrchestrationPlanner file-conflict serialization, and live-claim
integration boundaries.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_ludd.scheduling.scheduler import (
    ComputeSchedulingHint,
    CycleError,
    Scheduler,
    WorkItem,
    can_run_concurrently,
)

# ---------------------------------------------------------------------------
# WorkItem construction / immutability
# ---------------------------------------------------------------------------


class TestWorkItemConstruction:
    def test_defaults(self):
        wi = WorkItem(id="task-1")
        assert wi.id == "task-1"
        assert wi.resources == frozenset()
        assert wi.depends_on == frozenset()
        assert wi.is_greenfield is False

    def test_explicit_fields(self):
        wi = WorkItem(
            id="task-2",
            resources=frozenset(["db", "cache"]),
            depends_on=frozenset(["task-1"]),
            is_greenfield=True,
        )
        assert wi.resources == frozenset(["db", "cache"])
        assert wi.depends_on == frozenset(["task-1"])
        assert wi.is_greenfield is True

    def test_frozen_prevents_mutation(self):
        wi = WorkItem(id="task-3")
        with pytest.raises(FrozenInstanceError):
            wi.id = "changed"  # type: ignore[misc]

    def test_equality(self):
        a = WorkItem(id="x", resources=frozenset(["f1"]))
        b = WorkItem(id="x", resources=frozenset(["f1"]))
        c = WorkItem(id="x", resources=frozenset(["f2"]))
        assert a == b
        assert a != c

    def test_hashable(self):
        wi = WorkItem(id="h")
        _ = {wi: 1}
        assert True


# ---------------------------------------------------------------------------
# can_run_concurrently
# ---------------------------------------------------------------------------


class TestCanRunConcurrently:
    def test_no_shared_resources_no_dependency_returns_true(self):
        a = WorkItem(id="a", resources=frozenset(["f1"]))
        b = WorkItem(id="b", resources=frozenset(["f2"]))
        assert can_run_concurrently(a, b) is True

    def test_shared_resource_returns_false(self):
        a = WorkItem(id="a", resources=frozenset(["f1"]))
        b = WorkItem(id="b", resources=frozenset(["f1"]))
        assert can_run_concurrently(a, b) is False

    def test_depends_on_returns_false(self):
        a = WorkItem(id="a", resources=frozenset(["f1"]), depends_on=frozenset(["b"]))
        b = WorkItem(id="b", resources=frozenset(["f2"]))
        assert can_run_concurrently(a, b) is False

    def test_reverse_depends_on_returns_false(self):
        a = WorkItem(id="a", resources=frozenset(["f1"]))
        b = WorkItem(id="b", resources=frozenset(["f2"]), depends_on=frozenset(["a"]))
        assert can_run_concurrently(a, b) is False

    def test_greenfield_no_files_concurrent_with_anything(self):
        g = WorkItem(id="g", is_greenfield=True)
        n = WorkItem(id="n", resources=frozenset(["f1"]))
        assert can_run_concurrently(g, n) is True

    def test_greenfield_with_dependency_still_blocks(self):
        g = WorkItem(id="g", is_greenfield=True, depends_on=frozenset(["n"]))
        n = WorkItem(id="n", resources=frozenset(["f1"]))
        assert can_run_concurrently(g, n) is False

    def test_greenfield_with_resources_not_treated_as_greenfield(self):
        g = WorkItem(id="g", is_greenfield=True, resources=frozenset(["f1"]))
        n = WorkItem(id="n", resources=frozenset(["f1"]))
        assert can_run_concurrently(g, n) is False

    def test_both_empty_returns_true(self):
        a = WorkItem(id="a")
        b = WorkItem(id="b")
        assert can_run_concurrently(a, b) is True


# ---------------------------------------------------------------------------
# Scheduler.plan — basic cases
# ---------------------------------------------------------------------------


class TestSchedulerPlanBasic:
    def test_empty_items(self):
        s = Scheduler()
        batches = s.plan([])
        assert batches == []

    def test_single_item(self):
        s = Scheduler()
        batches = s.plan([WorkItem(id="a")])
        assert batches == [["a"]]

    def test_two_independent_items_same_batch(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a", resources=frozenset(["f1"])),
                WorkItem(id="b", resources=frozenset(["f2"])),
            ]
        )
        assert len(batches) == 1
        assert set(batches[0]) == {"a", "b"}

    def test_two_conflicting_items_different_batches(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a", resources=frozenset(["f1"])),
                WorkItem(id="b", resources=frozenset(["f1"])),
            ]
        )
        assert len(batches) >= 2
        flat = [iid for batch in batches for iid in batch]
        assert flat == ["a", "b"]

    def test_three_items_two_conflict(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a", resources=frozenset(["f1"])),
                WorkItem(id="b", resources=frozenset(["f1"])),
                WorkItem(id="c", resources=frozenset(["f2"])),
            ]
        )
        assert len(batches) >= 2
        batch0 = set(batches[0])
        assert "a" in batch0 or "b" in batch0
        assert "c" in batch0


# ---------------------------------------------------------------------------
# Scheduler.plan — dependency ordering
# ---------------------------------------------------------------------------


class TestSchedulerPlanDependencies:
    def test_linear_dependency_chain(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a"),
                WorkItem(id="b", depends_on=frozenset(["a"])),
                WorkItem(id="c", depends_on=frozenset(["b"])),
            ]
        )
        assert batches == [["a"], ["b"], ["c"]]

    def test_multiple_dependencies(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a"),
                WorkItem(id="b"),
                WorkItem(id="c", depends_on=frozenset(["a", "b"])),
            ]
        )
        assert len(batches) == 2
        assert set(batches[0]) == {"a", "b"}
        assert batches[1] == ["c"]

    def test_diamond_dependency(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a"),
                WorkItem(id="b", depends_on=frozenset(["a"])),
                WorkItem(id="c", depends_on=frozenset(["a"])),
                WorkItem(id="d", depends_on=frozenset(["b", "c"])),
            ]
        )
        assert batches[0] == ["a"]
        assert set(batches[1]) == {"b", "c"}
        assert batches[2] == ["d"]

    def test_before_after_not_reversed(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="b", depends_on=frozenset(["a"])),
                WorkItem(id="a"),
            ]
        )
        a_idx = next(i for i, b in enumerate(batches) if "a" in b)
        b_idx = next(i for i, b in enumerate(batches) if "b" in b)
        assert a_idx < b_idx


# ---------------------------------------------------------------------------
# Scheduler.plan — greenfield semantics
# ---------------------------------------------------------------------------


class TestSchedulerPlanGreenfield:
    def test_greenfield_all_parallel(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="g1", is_greenfield=True),
                WorkItem(id="g2", is_greenfield=True),
                WorkItem(id="g3", is_greenfield=True),
            ]
        )
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_greenfield_never_serializes_others(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a", resources=frozenset(["f1"])),
                WorkItem(id="b", resources=frozenset(["f1"])),
                WorkItem(id="g", is_greenfield=True),
            ]
        )
        batch0 = set(batches[0])
        assert "g" in batch0

    def test_greenfield_with_dependency_still_ordered(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a"),
                WorkItem(id="g", depends_on=frozenset(["a"]), is_greenfield=True),
            ]
        )
        assert batches[0] == ["a"]
        assert batches[1] == ["g"]


# ---------------------------------------------------------------------------
# Scheduler.plan — error paths
# ---------------------------------------------------------------------------


class TestSchedulerPlanErrors:
    def test_dependency_cycle_raises_cycle_error(self):
        s = Scheduler()
        with pytest.raises(CycleError) as exc:
            s.plan(
                [
                    WorkItem(id="a", depends_on=frozenset(["b"])),
                    WorkItem(id="b", depends_on=frozenset(["a"])),
                ]
            )
        assert "cycle" in str(exc.value).lower()

    def test_three_node_cycle_raises_cycle_error(self):
        s = Scheduler()
        with pytest.raises(CycleError):
            s.plan(
                [
                    WorkItem(id="a", depends_on=frozenset(["b"])),
                    WorkItem(id="b", depends_on=frozenset(["c"])),
                    WorkItem(id="c", depends_on=frozenset(["a"])),
                ]
            )

    def test_unknown_dependency_raises_value_error(self):
        s = Scheduler()
        with pytest.raises(ValueError) as exc:
            s.plan([WorkItem(id="a", depends_on=frozenset(["missing"]))])
        assert "missing" in str(exc.value)
        assert "a" in str(exc.value)

    def test_self_dependency_raises_cycle_error(self):
        s = Scheduler()
        with pytest.raises(CycleError):
            s.plan([WorkItem(id="a", depends_on=frozenset(["a"]))])


# ---------------------------------------------------------------------------
# Scheduler.plan — determinism
# ---------------------------------------------------------------------------


class TestSchedulerPlanDeterminism:
    def test_same_input_same_output(self):
        s1 = Scheduler()
        s2 = Scheduler()
        items = [
            WorkItem(id="a", resources=frozenset(["f1"])),
            WorkItem(id="b", resources=frozenset(["f1"])),
            WorkItem(id="c", resources=frozenset(["f2"])),
            WorkItem(id="d", depends_on=frozenset(["a"])),
        ]
        assert s1.plan(items) == s2.plan(items)

    def test_deterministic_across_scheduler_instances(self):
        items = [
            WorkItem(id="a", resources=frozenset(["f1"])),
            WorkItem(id="b", resources=frozenset(["f1"])),
            WorkItem(id="c", resources=frozenset(["f2"])),
        ]
        batches1 = Scheduler().plan(items)
        batches2 = Scheduler().plan(items)
        assert batches1 == batches2


# ---------------------------------------------------------------------------
# ComputeSchedulingHint — GPU affinity
# ---------------------------------------------------------------------------


class TestComputeSchedulingHint:
    def test_default_construction(self):
        hint = ComputeSchedulingHint()
        assert hint.preferred_gpu_type is None
        assert hint.min_vram_gb == 0.0
        assert hint.estimated_tokens == 0
        assert hint.work_type == ""

    def test_for_work_type_analysis(self):
        hint = ComputeSchedulingHint.for_work_type("analysis")
        assert hint.preferred_gpu_type == "a100_80"
        assert hint.min_vram_gb == 40.0
        assert hint.work_type == "analysis"

    def test_for_work_type_review(self):
        hint = ComputeSchedulingHint.for_work_type("review")
        assert hint.preferred_gpu_type == "t4"
        assert hint.min_vram_gb == 8.0
        assert hint.work_type == "review"

    def test_for_work_type_self_improve(self):
        hint = ComputeSchedulingHint.for_work_type("self_improve")
        assert hint.preferred_gpu_type == "h100"
        assert hint.min_vram_gb == 80.0
        assert hint.work_type == "self_improve"

    def test_override_gpu_type(self):
        hint = ComputeSchedulingHint.for_work_type("analysis", preferred_gpu_type="a100_40", min_vram_gb=20.0)
        assert hint.preferred_gpu_type == "a100_40"
        assert hint.min_vram_gb == 20.0

    def test_unknown_work_type_returns_defaults(self):
        hint = ComputeSchedulingHint.for_work_type("unknown_kind")
        assert hint.preferred_gpu_type is None
        assert hint.min_vram_gb == 0.0
        assert hint.work_type == "unknown_kind"


# ---------------------------------------------------------------------------
# OrchestrationPlanner — file-conflict serialization
# ---------------------------------------------------------------------------


class TestOrchestrationPlannerDeep:
    def test_explanation_when_no_conflicts(self):
        from general_ludd.scheduling.planner import OrchestrationPlanner

        planner = OrchestrationPlanner()
        result = planner.plan_work(
            [
                {"id": "a", "files": ["f1"], "depends_on": [], "is_greenfield": False},
                {"id": "b", "files": ["f2"], "depends_on": [], "is_greenfield": False},
            ]
        )
        assert "No file-conflict serializations" in result["explanation"]

    def test_explanation_describes_serialization(self):
        from general_ludd.scheduling.planner import OrchestrationPlanner

        planner = OrchestrationPlanner()
        result = planner.plan_work(
            [
                {"id": "a", "files": ["f1"], "depends_on": [], "is_greenfield": False},
                {"id": "b", "files": ["f1"], "depends_on": [], "is_greenfield": False},
            ]
        )
        assert len(result["serialized"]) >= 1
        assert "f1" in result["explanation"]

    def test_live_claim_conflicts_no_registry_returns_empty(self):
        from general_ludd.scheduling.planner import OrchestrationPlanner

        planner = OrchestrationPlanner()
        conflicts = planner.live_claim_conflicts(
            [
                {"id": "a", "files": ["f1"], "depends_on": [], "is_greenfield": False},
            ]
        )
        assert conflicts == {}

    def test_plan_with_live_claims_no_registry_delegates(self):
        from general_ludd.scheduling.planner import OrchestrationPlanner

        planner = OrchestrationPlanner()
        result = planner.plan_with_live_claims(
            [
                {"id": "a", "files": ["f1"], "depends_on": [], "is_greenfield": False},
            ]
        )
        assert result["batches"] == [["a"]]


# ---------------------------------------------------------------------------
# Scheduler.plan — complex multi-resource scenarios
# ---------------------------------------------------------------------------


class TestSchedulerPlanComplex:
    def test_multiple_independent_can_all_run(self):
        s = Scheduler()
        items = [
            WorkItem(id="a", resources=frozenset(["f1"])),
            WorkItem(id="b", resources=frozenset(["f2"])),
            WorkItem(id="c", resources=frozenset(["f3"])),
            WorkItem(id="d", resources=frozenset(["f4"])),
            WorkItem(id="e", resources=frozenset(["f5"])),
        ]
        batches = s.plan(items)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_resource_chaining(self):
        """a->f1, b->f1,f2, c->f2 → each pair conflicts → 3 batches."""
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a", resources=frozenset(["f1"])),
                WorkItem(id="b", resources=frozenset(["f1", "f2"])),
                WorkItem(id="c", resources=frozenset(["f2"])),
            ]
        )
        assert len(batches) >= 2

    def test_many_items_single_shared_resource(self):
        """N items all sharing f1 → N batches (fully serial)."""
        s = Scheduler()
        n = 5
        items = [WorkItem(id=str(i), resources=frozenset(["f1"])) for i in range(n)]
        batches = s.plan(items)
        assert len(batches) == n
        for batch in batches:
            assert len(batch) == 1

    def test_dependency_plus_resource_conflict(self):
        s = Scheduler()
        batches = s.plan(
            [
                WorkItem(id="a", resources=frozenset(["f1"])),
                WorkItem(id="b", resources=frozenset(["f1"]), depends_on=frozenset(["a"])),
            ]
        )
        assert batches == [["a"], ["b"]]


# ---------------------------------------------------------------------------
# CycleError as ValueError subclass
# ---------------------------------------------------------------------------


class TestCycleError:
    def test_is_value_error(self):
        err = CycleError("cycle")
        assert isinstance(err, ValueError)

    def test_message_preserved(self):
        err = CycleError("boom")
        assert "boom" in str(err)
