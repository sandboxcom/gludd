"""Tests for FileClaimRegistry → OrchestrationPlanner integration wiring."""

from __future__ import annotations

from general_ludd.coordination.file_claims import FileClaimRegistry
from general_ludd.scheduling.planner import OrchestrationPlanner


def _make_planner(registry: FileClaimRegistry | None = None) -> OrchestrationPlanner:
    return OrchestrationPlanner(registry)


def _make_registry() -> FileClaimRegistry:
    return FileClaimRegistry()


# ---------------------------------------------------------------------------
# Construction with registry
# ---------------------------------------------------------------------------


class TestConstructionWithRegistry:
    def test_construct_without_registry(self) -> None:
        planner = _make_planner()
        assert planner is not None
        assert hasattr(planner, "plan_work")
        assert hasattr(planner, "plan_with_live_claims")
        assert hasattr(planner, "live_claim_conflicts")

    def test_construct_with_registry(self) -> None:
        registry = _make_registry()
        planner = _make_planner(registry)
        assert planner is not None


# ---------------------------------------------------------------------------
# plan_work unchanged by registry presence
# ---------------------------------------------------------------------------


class TestPlanWorkUnchanged:
    def test_plan_work_without_registry(self) -> None:
        planner = _make_planner()
        result = planner.plan_work([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert len(result["batches"]) == 2

    def test_plan_work_with_registry_present_still_ignores_claims(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/x.py"])
        planner = _make_planner(registry)
        result = planner.plan_work([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert len(result["batches"]) == 2


# ---------------------------------------------------------------------------
# live_claim_conflicts
# ---------------------------------------------------------------------------


class TestLiveClaimConflicts:
    def test_no_registry_returns_empty(self) -> None:
        planner = _make_planner()
        conflicts = planner.live_claim_conflicts([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert conflicts == {}

    def test_empty_registry_returns_empty(self) -> None:
        registry = _make_registry()
        planner = _make_planner(registry)
        conflicts = planner.live_claim_conflicts([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert conflicts == {}

    def test_no_conflict_when_files_dont_overlap(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/a.py"])
        planner = _make_planner(registry)
        conflicts = planner.live_claim_conflicts([
            {"id": "b", "files": ["src/b.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert conflicts == {}

    def test_conflict_detected_on_overlap(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/x.py"])
        planner = _make_planner(registry)
        conflicts = planner.live_claim_conflicts([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert "a" in conflicts
        assert conflicts["a"] == ["src/x.py"]

    def test_multiple_items_multiple_conflicts(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/x.py", "src/y.py"])
        planner = _make_planner(registry)
        conflicts = planner.live_claim_conflicts([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/z.py"], "depends_on": [], "is_greenfield": False},
            {"id": "c", "files": ["src/y.py", "src/z.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert "a" in conflicts
        assert "b" not in conflicts
        assert "c" in conflicts
        assert conflicts["c"] == ["src/y.py"]

    def test_claimed_file_not_in_item_files(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/a.py", "src/b.py"])
        planner = _make_planner(registry)
        conflicts = planner.live_claim_conflicts([
            {"id": "x", "files": ["src/c.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert conflicts == {}


# ---------------------------------------------------------------------------
# plan_with_live_claims
# ---------------------------------------------------------------------------


class TestPlanWithLiveClaims:
    def test_no_registry_behaves_like_plan_work(self) -> None:
        planner = _make_planner()
        result = planner.plan_with_live_claims([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert len(result["batches"]) == 2

    def test_empty_registry_behaves_like_plan_work(self) -> None:
        registry = _make_registry()
        planner = _make_planner(registry)
        result = planner.plan_with_live_claims([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert result["batches"] == [["a"]]

    def test_claimed_file_defers_overlapping_item(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/claimed.py"])
        planner = _make_planner(registry)
        result = planner.plan_with_live_claims([
            {"id": "a", "files": ["src/claimed.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/free.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert result["parallel_now"] == ["b"]
        assert "a" not in result["parallel_now"]
        assert len(result["batches"]) == 2
        assert result["batches"][0] == ["b"]
        assert result["batches"][1] == ["a"]

    def test_unclaimed_file_not_deferred(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/x.py"])
        planner = _make_planner(registry)
        result = planner.plan_with_live_claims([
            {"id": "free", "files": ["src/unrelated.py"], "depends_on": [],
             "is_greenfield": False},
        ])
        assert result["parallel_now"] == ["free"]
        assert len(result["batches"]) == 1

    def test_virtual_item_not_in_result(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/x.py"])
        planner = _make_planner(registry)
        result = planner.plan_with_live_claims([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/y.py"], "depends_on": [], "is_greenfield": False},
        ])
        all_ids = {iid for batch in result["batches"] for iid in batch}
        assert "__live_file_claims__" not in all_ids
        assert "__live_file_claims__" not in result["parallel_now"]

    def test_multiple_claims_defer_multiple_items(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/a.py"])
        registry.claim("w2", ["src/b.py"])
        planner = _make_planner(registry)
        result = planner.plan_with_live_claims([
            {"id": "x", "files": ["src/a.py"], "depends_on": [], "is_greenfield": False},
            {"id": "y", "files": ["src/b.py"], "depends_on": [], "is_greenfield": False},
            {"id": "z", "files": ["src/c.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert result["parallel_now"] == ["z"]
        assert "x" not in result["parallel_now"]
        assert "y" not in result["parallel_now"]
        # x and y both deferred — they share no files so can be in same batch
        assert "x" in result["batches"][1]
        assert "y" in result["batches"][1]

    def test_item_with_existing_dependency_and_claim_conflict(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/shared.py"])
        planner = _make_planner(registry)
        result = planner.plan_with_live_claims([
            {"id": "a", "files": ["src/shared.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/other.py"], "depends_on": ["a"], "is_greenfield": False},
            {"id": "c", "files": ["src/free.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert result["parallel_now"] == ["c"]
        assert len(result["batches"]) == 3
        assert result["batches"][0] == ["c"]
        assert result["batches"][1] == ["a"]
        assert result["batches"][2] == ["b"]

    def test_all_files_claimed_nothing_in_batch_0(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/a.py", "src/b.py"])
        planner = _make_planner(registry)
        result = planner.plan_with_live_claims([
            {"id": "x", "files": ["src/a.py"], "depends_on": [], "is_greenfield": False},
            {"id": "y", "files": ["src/b.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert result["parallel_now"] == []
        assert len(result["batches"]) == 1
        assert set(result["batches"][0]) == {"x", "y"}

    def test_serialized_key_present_in_result(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/x.py"])
        planner = _make_planner(registry)
        result = planner.plan_with_live_claims([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert "serialized" in result
        assert "explanation" in result

    def test_explanation_key_present_in_result(self) -> None:
        registry = _make_registry()
        registry.claim("w1", ["src/x.py"])
        planner = _make_planner(registry)
        result = planner.plan_with_live_claims([
            {"id": "a", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
            {"id": "b", "files": ["src/x.py"], "depends_on": [], "is_greenfield": False},
        ])
        assert "explanation" in result
        assert "serialized" in result
