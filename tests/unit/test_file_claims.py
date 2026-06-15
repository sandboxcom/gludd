"""Unit tests for FileClaimRegistry (TDD — written before the implementation).

Covers:
- claim / release basics
- overlaps detection (two workers sharing a file → reported to both)
- should_wait
- no-overlap case
- merge_plan classification
- release clears overlaps
"""

from __future__ import annotations

from general_ludd.coordination.file_claims import FileClaimRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_registry() -> FileClaimRegistry:
    return FileClaimRegistry()


# ---------------------------------------------------------------------------
# claim + all_claims
# ---------------------------------------------------------------------------

class TestClaim:
    def test_claim_single_worker(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["a.py", "b.py"])
        claims = reg.all_claims()
        assert set(claims["a.py"]) == {"w1"}
        assert set(claims["b.py"]) == {"w1"}

    def test_claim_is_idempotent(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["a.py"])
        reg.claim("w1", ["a.py"])
        assert reg.all_claims()["a.py"] == ["w1"]

    def test_claim_multiple_workers_same_file(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        workers = reg.all_claims()["shared.py"]
        assert set(workers) == {"w1", "w2"}

    def test_claim_extending_worker_files(self) -> None:
        """Re-claiming for a worker replaces its file set."""
        reg = make_registry()
        reg.claim("w1", ["a.py"])
        reg.claim("w1", ["b.py", "c.py"])
        claims = reg.all_claims()
        # a.py should be gone because w1 updated its claims
        assert "w1" not in claims.get("a.py", [])
        assert "w1" in claims["b.py"]
        assert "w1" in claims["c.py"]


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------

class TestRelease:
    def test_release_removes_worker(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["a.py"])
        reg.release("w1")
        assert reg.all_claims() == {}

    def test_release_clears_overlaps(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        # Confirm overlap exists
        assert "w2" in reg.overlaps("w1").get("shared.py", [])
        reg.release("w2")
        # After release, no more overlap for w1
        assert reg.overlaps("w1") == {}

    def test_release_nonexistent_worker_is_noop(self) -> None:
        reg = make_registry()
        reg.release("ghost")  # must not raise
        assert reg.all_claims() == {}

    def test_release_only_removes_its_own_files(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["a.py", "b.py"])
        reg.claim("w2", ["b.py", "c.py"])
        reg.release("w1")
        claims = reg.all_claims()
        assert "w1" not in claims.get("a.py", [])
        assert "w1" not in claims.get("b.py", [])
        assert "w2" in claims["b.py"]
        assert "w2" in claims["c.py"]


# ---------------------------------------------------------------------------
# overlaps
# ---------------------------------------------------------------------------

class TestOverlaps:
    def test_no_overlap_returns_empty(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["unique.py"])
        reg.claim("w2", ["other.py"])
        assert reg.overlaps("w1") == {}

    def test_overlap_reported_to_requesting_worker(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        overlaps = reg.overlaps("w1")
        assert "shared.py" in overlaps
        assert "w2" in overlaps["shared.py"]

    def test_overlap_reported_symmetrically(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        assert "w1" in reg.overlaps("w2").get("shared.py", [])
        assert "w2" in reg.overlaps("w1").get("shared.py", [])

    def test_own_worker_not_included_in_overlap(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        overlap_w1 = reg.overlaps("w1")
        assert "w1" not in overlap_w1.get("shared.py", [])

    def test_multiple_workers_overlap_on_one_file(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["f.py"])
        reg.claim("w2", ["f.py"])
        reg.claim("w3", ["f.py"])
        overlaps = reg.overlaps("w1")
        assert set(overlaps["f.py"]) == {"w2", "w3"}

    def test_partial_overlap(self) -> None:
        """w1 and w2 share b.py but w2 has c.py exclusively."""
        reg = make_registry()
        reg.claim("w1", ["a.py", "b.py"])
        reg.claim("w2", ["b.py", "c.py"])
        overlaps = reg.overlaps("w1")
        assert "b.py" in overlaps
        assert "a.py" not in overlaps  # exclusive to w1

    def test_unknown_worker_returns_empty(self) -> None:
        reg = make_registry()
        assert reg.overlaps("nobody") == {}


# ---------------------------------------------------------------------------
# should_wait
# ---------------------------------------------------------------------------

class TestShouldWait:
    def test_no_overlap_means_no_wait(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["b.py"])
        assert reg.should_wait("w1") == []

    def test_should_wait_returns_other_workers(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        wait = reg.should_wait("w1")
        assert "w2" in wait
        assert "w1" not in wait

    def test_should_wait_union_across_files(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["f1.py", "f2.py"])
        reg.claim("w2", ["f1.py"])
        reg.claim("w3", ["f2.py"])
        wait = set(reg.should_wait("w1"))
        assert wait == {"w2", "w3"}

    def test_should_wait_deduplicates(self) -> None:
        """w2 overlaps on two files → w2 appears once in should_wait."""
        reg = make_registry()
        reg.claim("w1", ["f1.py", "f2.py"])
        reg.claim("w2", ["f1.py", "f2.py"])
        wait = reg.should_wait("w1")
        assert wait.count("w2") == 1


# ---------------------------------------------------------------------------
# merge_plan
# ---------------------------------------------------------------------------

class TestMergePlan:
    def test_no_conflicts_returns_empty(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["b.py"])
        assert reg.merge_plan() == {}

    def test_conflict_classified_as_union_by_default(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        plan = reg.merge_plan()
        assert "shared.py" in plan
        assert plan["shared.py"] == "union"

    def test_three_way_conflict(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["f.py"])
        reg.claim("w2", ["f.py"])
        reg.claim("w3", ["f.py"])
        plan = reg.merge_plan()
        assert plan["f.py"] == "union"

    def test_exclusive_files_not_in_plan(self) -> None:
        reg = make_registry()
        reg.claim("w1", ["a.py", "shared.py"])
        reg.claim("w2", ["shared.py"])
        plan = reg.merge_plan()
        assert "a.py" not in plan
        assert "shared.py" in plan
