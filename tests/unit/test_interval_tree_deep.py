"""Deep tests for augmented interval tree — 22 tests."""

from __future__ import annotations

import random

from general_ludd.algorithms.interval_tree import (
    IntervalNode,
    interval_delete,
    interval_insert,
    interval_overlap_query,
    interval_size,
    interval_stabbing_query,
    interval_to_list,
)


class TestIntervalInsert:
    def test_insert_single(self) -> None:
        root = interval_insert(None, 2, 5, "a")
        assert interval_to_list(root) == [(2, 5, "a")]

    def test_insert_non_overlapping(self) -> None:
        root: IntervalNode[str] | None = None
        root = interval_insert(root, 1, 3, "a")
        root = interval_insert(root, 4, 7, "b")
        root = interval_insert(root, 8, 10, "c")
        assert interval_to_list(root) == [(1, 3, "a"), (4, 7, "b"), (8, 10, "c")]

    def test_insert_duplicate_overwrites(self) -> None:
        root = interval_insert(None, 5, 9, "old")
        root = interval_insert(root, 5, 9, "new")
        assert interval_to_list(root) == [(5, 9, "new")]

    def test_insert_many_and_size(self) -> None:
        root: IntervalNode[int] | None = None
        for i in range(20):
            root = interval_insert(root, i * 2, i * 2 + 1, i)
        assert interval_size(root) == 20

    def test_insert_max_hi_updated(self) -> None:
        root: IntervalNode[str] | None = None
        root = interval_insert(root, 0, 3, "a")
        root = interval_insert(root, 2, 5, "b")
        root = interval_insert(root, 1, 10, "c")
        assert root.max_hi == 10


class TestIntervalOverlapQuery:
    def test_overlap_empty_tree(self) -> None:
        assert interval_overlap_query(None, 1, 3) == []

    def test_overlap_exact_match(self) -> None:
        root = interval_insert(None, 3, 7, "x")
        result = interval_overlap_query(root, 3, 7)
        assert result == [(3, 7, "x")]

    def test_overlap_partial_left(self) -> None:
        root = interval_insert(None, 2, 8, "wide")
        result = interval_overlap_query(root, 1, 5)
        assert result == [(2, 8, "wide")]

    def test_overlap_partial_right(self) -> None:
        root = interval_insert(None, 2, 8, "wide")
        result = interval_overlap_query(root, 5, 10)
        assert result == [(2, 8, "wide")]

    def test_overlap_contained(self) -> None:
        root = interval_insert(None, 3, 7, "inner")
        result = interval_overlap_query(root, 0, 10)
        assert result == [(3, 7, "inner")]

    def test_overlap_disjoint(self) -> None:
        root = interval_insert(None, 1, 3, "left")
        result = interval_overlap_query(root, 5, 8)
        assert result == []

    def test_overlap_multiple(self) -> None:
        root: IntervalNode[str] | None = None
        root = interval_insert(root, 1, 4, "a")
        root = interval_insert(root, 3, 6, "b")
        root = interval_insert(root, 5, 8, "c")
        root = interval_insert(root, 7, 10, "d")
        result = interval_overlap_query(root, 3, 7)
        assert len(result) == 3
        assert set(v for _, _, v in result) == {"a", "b", "c"}

    def test_overlap_touching_no_overlap(self) -> None:
        root = interval_insert(None, 1, 3, "a")
        result = interval_overlap_query(root, 3, 5)
        assert result == []

    def test_overlap_deep_tree(self) -> None:
        root: IntervalNode[int] | None = None
        intervals: list[tuple[int, int, int]] = []
        for i in range(30):
            lo = i * 3
            hi = lo + 5
            root = interval_insert(root, lo, hi, i)
            intervals.append((lo, hi, i))
        q_lo, q_hi = 45, 50
        result = interval_overlap_query(root, q_lo, q_hi)
        brute = [(lo, hi, v) for lo, hi, v in intervals if lo < q_hi and q_lo < hi]
        assert set(result) == set(brute)


class TestIntervalStabbingQuery:
    def test_stabbing_empty(self) -> None:
        assert interval_stabbing_query(None, 5) == []

    def test_stabbing_point_inside(self) -> None:
        root = interval_insert(None, 2, 8, "hit")
        result = interval_stabbing_query(root, 5)
        assert result == [(2, 8, "hit")]

    def test_stabbing_point_at_left_edge(self) -> None:
        root = interval_insert(None, 2, 8, "hit")
        result = interval_stabbing_query(root, 2)
        assert result == [(2, 8, "hit")]

    def test_stabbing_point_at_right_edge(self) -> None:
        root = interval_insert(None, 2, 8, "hit")
        result = interval_stabbing_query(root, 7)
        assert result == [(2, 8, "hit")]

    def test_stabbing_point_outside(self) -> None:
        root = interval_insert(None, 2, 8, "miss")
        assert interval_stabbing_query(root, 8) == []
        assert interval_stabbing_query(root, 1) == []

    def test_stabbing_multiple_hits(self) -> None:
        root: IntervalNode[str] | None = None
        root = interval_insert(root, 1, 10, "wide")
        root = interval_insert(root, 3, 5, "narrow")
        root = interval_insert(root, 7, 9, "narrow2")
        result = interval_stabbing_query(root, 4)
        assert set(v for _, _, v in result) == {"wide", "narrow"}


class TestIntervalDelete:
    def test_delete_empty(self) -> None:
        assert interval_delete(None, 1, 3) is None

    def test_delete_single(self) -> None:
        root = interval_insert(None, 2, 5, "x")
        root = interval_delete(root, 2, 5)
        assert root is None

    def test_delete_one_of_many(self) -> None:
        root: IntervalNode[str] | None = None
        root = interval_insert(root, 1, 3, "a")
        root = interval_insert(root, 4, 6, "b")
        root = interval_insert(root, 7, 9, "c")
        root = interval_delete(root, 4, 6)
        assert interval_to_list(root) == [(1, 3, "a"), (7, 9, "c")]

    def test_delete_non_existent(self) -> None:
        root = interval_insert(None, 5, 10, "x")
        result = interval_delete(root, 1, 2)
        assert interval_to_list(result) == [(5, 10, "x")]

    def test_delete_root_max_hi_updates(self) -> None:
        root: IntervalNode[str] | None = None
        root = interval_insert(root, 0, 10, "wide")
        root = interval_insert(root, 1, 3, "narrow")
        root = interval_delete(root, 0, 10)
        assert root is not None
        assert root.max_hi == 3


class TestIntervalFuzz:
    def test_fuzz_insert_overlap_consistency(self) -> None:
        rng = random.Random(42)
        root: IntervalNode[int] | None = None
        seen: set[tuple[int, int]] = set()
        intervals: list[tuple[int, int, int]] = []
        for i in range(100):
            lo = rng.randint(0, 80)
            size = rng.randint(1, 20)
            hi = lo + size
            if (lo, hi) in seen:
                continue
            seen.add((lo, hi))
            root = interval_insert(root, lo, hi, i)
            intervals.append((lo, hi, i))
        query_lo, query_hi = rng.randint(0, 60), rng.randint(1, 40)
        tree_result = interval_overlap_query(root, query_lo, query_lo + query_hi)
        brute_result = [(lo, hi, v) for lo, hi, v in intervals if lo < (query_lo + query_hi) and query_lo < hi]
        assert set(tree_result) == set(brute_result)

    def test_fuzz_stabbing_consistency(self) -> None:
        rng = random.Random(99)
        root: IntervalNode[str] | None = None
        seen: set[tuple[int, int]] = set()
        intervals: list[tuple[int, int, str]] = []
        for i in range(100):
            lo = rng.randint(0, 200)
            size = rng.randint(1, 15)
            hi = lo + size
            if (lo, hi) in seen:
                continue
            seen.add((lo, hi))
            val = f"v{i}"
            root = interval_insert(root, lo, hi, val)
            intervals.append((lo, hi, val))
        point = rng.randint(0, 200)
        tree_result = interval_stabbing_query(root, point)
        brute_result = [(lo, hi, v) for lo, hi, v in intervals if lo <= point < hi]
        assert set(tree_result) == set(brute_result)

    def test_fuzz_delete_then_query(self) -> None:
        rng = random.Random(7)
        root: IntervalNode[int] | None = None
        tree_contents: dict[tuple[int, int], int] = {}
        for i in range(80):
            lo = rng.randint(0, 100)
            hi = lo + rng.randint(1, 15)
            root = interval_insert(root, lo, hi, i)
            tree_contents[(lo, hi)] = i
        delete_keys = list(tree_contents.keys())[:20]
        remaining = [(lo, hi, tree_contents[(lo, hi)]) for lo, hi in tree_contents if (lo, hi) not in set(delete_keys)]
        for lo, hi in delete_keys:
            root = interval_delete(root, lo, hi)
        assert interval_size(root) == len(remaining)
        assert set(interval_to_list(root)) == set(remaining)
