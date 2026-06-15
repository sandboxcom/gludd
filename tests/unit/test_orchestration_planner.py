"""Unit tests for OrchestrationPlanner (task #32).

Covers:
  - Two items touching the same file serialize into different batches.
  - Two items with disjoint files batch together (both in parallel_now).
  - Dependency ordering is respected (dependent in a later batch).
  - A greenfield, resource-less item is always in batch 0.
  - The 'serialized' output names the shared file.
  - A dependency cycle surfaces a clear CycleError.
  - Exact batch membership assertions throughout.
"""

import pytest

from general_ludd.scheduling.planner import OrchestrationPlanner
from general_ludd.scheduling.scheduler import CycleError


@pytest.fixture()
def planner() -> OrchestrationPlanner:
    return OrchestrationPlanner()


# ---------------------------------------------------------------------------
# 1. Two items touching the SAME file must serialize into different batches
# ---------------------------------------------------------------------------


class TestSameFileSerializes:
    def test_shared_file_puts_items_in_different_batches(
        self, planner: OrchestrationPlanner
    ) -> None:
        items = [
            {"id": "task-a", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
            {"id": "task-b", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        batches = result["batches"]
        # Must be 2 separate batches (cannot share a batch due to file conflict).
        assert len(batches) == 2, f"Expected 2 batches, got {batches}"
        # Exactly one of the tasks lands in batch 0 and the other in batch 1.
        assert len(batches[0]) == 1
        assert len(batches[1]) == 1
        # Batch 0 has one task; batch 1 has the other.
        assert set(batches[0]) | set(batches[1]) == {"task-a", "task-b"}

    def test_shared_file_not_in_parallel_now(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "task-a", "files": ["src/shared.py"], "depends_on": [], "is_greenfield": False},
            {"id": "task-b", "files": ["src/shared.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        # Only one task can start immediately (batch 0).
        assert len(result["parallel_now"]) == 1

    def test_serialized_output_names_the_shared_file(
        self, planner: OrchestrationPlanner
    ) -> None:
        items = [
            {"id": "task-a", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
            {"id": "task-b", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        serialized = result["serialized"]
        assert len(serialized) == 1, f"Expected 1 serialization triple, got {serialized}"
        a_id, b_id, shared_file = serialized[0]
        # The two item ids must be the pair.
        assert {a_id, b_id} == {"task-a", "task-b"}
        # The shared file must be the conflicting file.
        assert shared_file == "src/foo.py"

    def test_explanation_mentions_shared_file(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "task-a", "files": ["src/conflict.py"], "depends_on": [], "is_greenfield": False},
            {"id": "task-b", "files": ["src/conflict.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        assert "src/conflict.py" in result["explanation"]


# ---------------------------------------------------------------------------
# 2. Two items with DISJOINT files batch together (both in parallel_now)
# ---------------------------------------------------------------------------


class TestDisjointFilesParallel:
    def test_disjoint_files_in_same_batch(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "task-a", "files": ["src/alpha.py"], "depends_on": [], "is_greenfield": False},
            {"id": "task-b", "files": ["src/beta.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        batches = result["batches"]
        # Both items share no file — they should end up in the same (first) batch.
        assert len(batches) == 1, f"Expected 1 batch, got {batches}"
        assert set(batches[0]) == {"task-a", "task-b"}

    def test_disjoint_files_both_in_parallel_now(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "task-a", "files": ["src/alpha.py"], "depends_on": [], "is_greenfield": False},
            {"id": "task-b", "files": ["src/beta.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        assert set(result["parallel_now"]) == {"task-a", "task-b"}

    def test_no_serialized_for_disjoint_items(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "task-a", "files": ["src/alpha.py"], "depends_on": [], "is_greenfield": False},
            {"id": "task-b", "files": ["src/beta.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        assert result["serialized"] == []

    def test_three_disjoint_items_all_in_batch_0(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "a", "files": ["f1.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["f2.py"], "depends_on": [], "is_greenfield": False},
            {"id": "c", "files": ["f3.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        assert len(result["batches"]) == 1
        assert set(result["parallel_now"]) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# 3. Dependency ordering is respected
# ---------------------------------------------------------------------------


class TestDependencyOrdering:
    def test_dependent_task_in_later_batch(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "base", "files": ["src/base.py"], "depends_on": [], "is_greenfield": False},
            {"id": "derived", "files": ["src/derived.py"], "depends_on": ["base"], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        batches = result["batches"]
        assert len(batches) == 2, f"Expected 2 batches (dep ordering), got {batches}"
        assert "base" in batches[0]
        assert "derived" in batches[1]

    def test_chain_of_three_dependencies(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "step1", "files": [], "depends_on": [], "is_greenfield": True},
            {"id": "step2", "files": [], "depends_on": ["step1"], "is_greenfield": True},
            {"id": "step3", "files": [], "depends_on": ["step2"], "is_greenfield": True},
        ]
        result = planner.plan_work(items)
        batches = result["batches"]
        assert len(batches) == 3, f"Expected 3 batches for a chain, got {batches}"
        assert batches[0] == ["step1"]
        assert batches[1] == ["step2"]
        assert batches[2] == ["step3"]

    def test_parallel_items_then_dependent(self, planner: OrchestrationPlanner) -> None:
        """a and b can run in parallel; c depends on both."""
        items = [
            {"id": "a", "files": ["f_a.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["f_b.py"], "depends_on": [], "is_greenfield": False},
            {"id": "c", "files": ["f_c.py"], "depends_on": ["a", "b"], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        batches = result["batches"]
        assert len(batches) == 2
        assert set(batches[0]) == {"a", "b"}
        assert batches[1] == ["c"]


# ---------------------------------------------------------------------------
# 4. Greenfield (resource-less) items are always in batch 0
# ---------------------------------------------------------------------------


class TestGreenfieldItems:
    def test_greenfield_item_in_batch_0(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "gf", "files": [], "depends_on": [], "is_greenfield": True},
            {"id": "task-a", "files": ["src/foo.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        # Greenfield item has no resources so it never conflicts with anything.
        assert "gf" in result["parallel_now"]

    def test_greenfield_does_not_block_others(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "gf", "files": [], "depends_on": [], "is_greenfield": True},
            {"id": "a", "files": ["a.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["b.py"], "depends_on": [], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        # All three are free of conflicts — they should all be in batch 0.
        assert len(result["batches"]) == 1
        assert set(result["parallel_now"]) == {"gf", "a", "b"}

    def test_greenfield_with_dependency_respects_ordering(
        self, planner: OrchestrationPlanner
    ) -> None:
        """A greenfield item that explicitly depends on another must wait."""
        items = [
            {"id": "base", "files": ["src/base.py"], "depends_on": [], "is_greenfield": False},
            {"id": "gf", "files": [], "depends_on": ["base"], "is_greenfield": True},
        ]
        result = planner.plan_work(items)
        batches = result["batches"]
        assert len(batches) == 2
        assert "base" in batches[0]
        assert "gf" in batches[1]


# ---------------------------------------------------------------------------
# 5. Cycle detection surfaces a clear CycleError
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_simple_cycle_raises_cycle_error(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "a", "files": [], "depends_on": ["b"], "is_greenfield": True},
            {"id": "b", "files": [], "depends_on": ["a"], "is_greenfield": True},
        ]
        with pytest.raises(CycleError):
            planner.plan_work(items)

    def test_three_node_cycle_raises_cycle_error(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "x", "files": [], "depends_on": ["z"], "is_greenfield": True},
            {"id": "y", "files": [], "depends_on": ["x"], "is_greenfield": True},
            {"id": "z", "files": [], "depends_on": ["y"], "is_greenfield": True},
        ]
        with pytest.raises(CycleError):
            planner.plan_work(items)

    def test_cycle_error_message_is_informative(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "alpha", "files": [], "depends_on": ["beta"], "is_greenfield": True},
            {"id": "beta", "files": [], "depends_on": ["alpha"], "is_greenfield": True},
        ]
        with pytest.raises(CycleError, match=r"cycle"):
            planner.plan_work(items)


# ---------------------------------------------------------------------------
# 6. parallelizable() convenience wrapper
# ---------------------------------------------------------------------------


class TestParallelizable:
    def test_parallelizable_returns_batch_0_ids(self, planner: OrchestrationPlanner) -> None:
        items = [
            {"id": "a", "files": ["f_a.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["f_b.py"], "depends_on": [], "is_greenfield": False},
            {"id": "c", "files": ["f_a.py"], "depends_on": [], "is_greenfield": False},
        ]
        # a and b are disjoint; c conflicts with a — so one of {a, c} serializes.
        result = planner.parallelizable(items)
        # Exactly two items should be parallelizable (a+b or b+c).
        assert len(result) == 2

    def test_parallelizable_empty_list(self, planner: OrchestrationPlanner) -> None:
        assert planner.parallelizable([]) == []


# ---------------------------------------------------------------------------
# 7. Mixed scenario — file conflict AND dependency in same plan
# ---------------------------------------------------------------------------


class TestMixedConflictAndDependency:
    def test_file_conflict_and_dependency_combined(
        self, planner: OrchestrationPlanner
    ) -> None:
        """
        a and b share a file -> different batches.
        c depends on a -> must be after a.
        d is independent and greenfield -> batch 0.
        """
        items = [
            {"id": "a", "files": ["shared.py", "a_only.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["shared.py", "b_only.py"], "depends_on": [], "is_greenfield": False},
            {"id": "c", "files": ["c_only.py"], "depends_on": ["a"], "is_greenfield": False},
            {"id": "d", "files": [], "depends_on": [], "is_greenfield": True},
        ]
        result = planner.plan_work(items)
        batches = result["batches"]
        # d and one of {a,b} should be in batch 0 (a is input-order first, so a wins batch 0).
        assert len(batches) >= 2
        # a and d are in batch 0 (a comes first in input order, d is greenfield/no conflict).
        assert "a" in batches[0]
        assert "d" in batches[0]
        # b conflicts with a — it must be in a later batch.
        assert "b" not in batches[0]
        # c depends on a — it must be in a later batch than a.
        a_batch = next(i for i, b in enumerate(batches) if "a" in b)
        c_batch = next(i for i, b in enumerate(batches) if "c" in b)
        assert c_batch > a_batch

    def test_serialized_names_the_shared_file_in_mixed_scenario(
        self, planner: OrchestrationPlanner
    ) -> None:
        items = [
            {"id": "a", "files": ["shared.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["shared.py", "b_only.py"], "depends_on": [], "is_greenfield": False},
            {"id": "c", "files": ["c_only.py"], "depends_on": ["a"], "is_greenfield": False},
        ]
        result = planner.plan_work(items)
        serialized = result["serialized"]
        # a and b conflict on shared.py.
        conflict_files = {triple[2] for triple in serialized}
        assert "shared.py" in conflict_files


# ---------------------------------------------------------------------------
# 8. Empty input
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_item_list(self, planner: OrchestrationPlanner) -> None:
        result = planner.plan_work([])
        assert result["batches"] == []
        assert result["parallel_now"] == []
        assert result["serialized"] == []

    def test_single_item(self, planner: OrchestrationPlanner) -> None:
        items = [{"id": "solo", "files": ["only.py"], "depends_on": [], "is_greenfield": False}]
        result = planner.plan_work(items)
        assert result["batches"] == [["solo"]]
        assert result["parallel_now"] == ["solo"]
        assert result["serialized"] == []
