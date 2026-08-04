"""Deep R-tree spatial index tests: insert, search, quadratic split, bounding box, nearest."""

from __future__ import annotations

import math
import random

import pytest

from general_ludd.algorithms.r_tree import BBox, RTree, _Node


class TestBBox:
    def test_area_unit_square(self) -> None:
        b = BBox(0, 0, 1, 1)
        assert b.area == 1.0

    def test_area_zero_height(self) -> None:
        b = BBox(0, 0, 5, 0)
        assert b.area == 0.0

    def test_area_negative_clamped(self) -> None:
        b = BBox(5, 5, 0, 0)
        assert b.area == 0.0

    def test_margin(self) -> None:
        b = BBox(0, 0, 3, 4)
        assert b.margin == 14.0

    def test_contains_true(self) -> None:
        outer = BBox(0, 0, 10, 10)
        inner = BBox(2, 2, 5, 5)
        assert outer.contains(inner)

    def test_contains_false_partial(self) -> None:
        outer = BBox(0, 0, 4, 4)
        inner = BBox(2, 2, 6, 6)
        assert not outer.contains(inner)

    def test_intersects_true(self) -> None:
        a = BBox(0, 0, 5, 5)
        b = BBox(3, 3, 8, 8)
        assert a.intersects(b)

    def test_intersects_false_separated(self) -> None:
        a = BBox(0, 0, 2, 2)
        b = BBox(3, 3, 5, 5)
        assert not a.intersects(b)

    def test_intersects_touching_edge(self) -> None:
        a = BBox(0, 0, 2, 2)
        b = BBox(2, 0, 4, 2)
        assert a.intersects(b)

    def test_distance_sq_adjacent(self) -> None:
        a = BBox(0, 0, 2, 2)
        b = BBox(3, 0, 5, 2)
        assert a.distance_sq(b) == 1.0

    def test_distance_sq_overlapping(self) -> None:
        a = BBox(0, 0, 5, 5)
        b = BBox(3, 3, 8, 8)
        assert a.distance_sq(b) == 0.0

    def test_expanded(self) -> None:
        a = BBox(0, 0, 2, 2)
        b = BBox(3, 1, 5, 4)
        e = a.expanded(b)
        assert e.x1 == 0
        assert e.y1 == 0
        assert e.x2 == 5
        assert e.y2 == 4

    def test_union_all_empty(self) -> None:
        u = BBox.union_all([])
        assert u.x1 == math.inf
        assert u.y1 == math.inf
        assert u.x2 == -math.inf
        assert u.y2 == -math.inf

    def test_center(self) -> None:
        b = BBox(0, 0, 4, 6)
        assert b.center == (2.0, 3.0)


class TestRTreeEmpty:
    def test_empty_search(self) -> None:
        tree: RTree[str] = RTree()
        assert tree.search(BBox(0, 0, 10, 10)) == []

    def test_empty_len(self) -> None:
        tree: RTree[str] = RTree()
        assert len(tree) == 0

    def test_empty_bool(self) -> None:
        tree: RTree[str] = RTree()
        assert not tree

    def test_empty_contains_point(self) -> None:
        tree: RTree[str] = RTree()
        assert tree.contains_point(5, 5) == []

    def test_empty_range_search(self) -> None:
        tree: RTree[str] = RTree()
        assert tree.range_search(0, 0, 100, 100) == []

    def test_empty_depth(self) -> None:
        tree: RTree[str] = RTree()
        assert tree.depth == 1

    def test_empty_total_nodes(self) -> None:
        tree: RTree[str] = RTree()
        assert tree.total_nodes == 1


class TestRTreeInsertSearch:
    def test_insert_one(self) -> None:
        tree: RTree[str] = RTree()
        tree.insert(BBox(0, 0, 2, 2), "a")
        assert tree.size == 1
        assert tree.search(BBox(0, 0, 2, 2)) == ["a"]

    def test_insert_many_no_split(self) -> None:
        tree: RTree[str] = RTree(max_entries=5, min_entries=2)
        for i in range(5):
            tree.insert(BBox(float(i), float(i), float(i + 1), float(i + 1)), f"item_{i}")
        assert tree.size == 5
        results = tree.search(BBox(0, 0, 6, 6))
        assert len(results) == 5

    def test_search_none_outside(self) -> None:
        tree: RTree[str] = RTree()
        tree.insert(BBox(10, 10, 20, 20), "far")
        assert tree.search(BBox(0, 0, 2, 2)) == []

    def test_search_partial_overlap(self) -> None:
        tree: RTree[str] = RTree()
        tree.insert(BBox(0, 0, 5, 5), "a")
        tree.insert(BBox(10, 10, 15, 15), "b")
        results = tree.search(BBox(4, 4, 11, 11))
        assert set(results) == {"a", "b"}

    def test_search_bbox_returns_bboxes(self) -> None:
        tree: RTree[str] = RTree()
        box = BBox(1, 2, 5, 8)
        tree.insert(box, "data")
        results = tree.search_bbox(BBox(0, 0, 10, 10))
        assert len(results) == 1
        assert results[0][0] == box
        assert results[0][1] == "data"

    def test_search_exact_match(self) -> None:
        tree: RTree[str] = RTree()
        box = BBox(3, 7, 11, 13)
        tree.insert(box, "exact")
        results = tree.search(box)
        assert results == ["exact"]

    def test_contains_point(self) -> None:
        tree: RTree[str] = RTree()
        tree.insert(BBox(0, 0, 10, 10), "inside")
        tree.insert(BBox(20, 20, 30, 30), "outside")
        assert tree.contains_point(5, 5) == ["inside"]
        assert tree.contains_point(15, 15) == []

    def test_range_search(self) -> None:
        tree: RTree[str] = RTree()
        tree.insert(BBox(0, 0, 5, 5), "a")
        tree.insert(BBox(4, 4, 10, 10), "b")
        tree.insert(BBox(50, 50, 60, 60), "c")
        results = tree.range_search(3, 3, 6, 6)
        assert set(results) == {"a", "b"}


class TestRTreeSplit:
    def test_split_triggers_on_overflow(self) -> None:
        tree: RTree[int] = RTree(max_entries=3, min_entries=1)
        for i in range(10):
            tree.insert(BBox(float(i), float(i), float(i + 0.5), float(i + 0.5)), i)
        assert tree.size == 10
        assert tree.depth >= 2
        for i in range(10):
            results = tree.search(BBox(float(i) - 0.1, float(i) - 0.1, float(i) + 0.6, float(i) + 0.6))
            assert i in results

    def test_all_items_reachable_after_split(self) -> None:
        tree: RTree[int] = RTree(max_entries=3, min_entries=1)
        n = 50
        for i in range(n):
            x = float(i % 10) * 10
            y = float(i // 10) * 10
            tree.insert(BBox(x, y, x + 5, y + 5), i)
        assert tree.size == n
        results = tree.search(BBox(-0.1, -0.1, 1000, 1000))
        assert len(results) == n
        assert set(results) == set(range(n))

    def test_quadratic_split_picks_max_waste_seeds(self) -> None:
        node: _Node[str] = _Node(is_leaf=True)
        node.children = [
            BBox(0, 0, 1, 1),
            BBox(0, 9, 1, 10),
            BBox(9, 0, 10, 1),
            BBox(9, 9, 10, 10),
        ]
        node.data = ["a", "b", "c", "d"]
        node.recalc_bbox()
        tree: RTree[str] = RTree(max_entries=3, min_entries=1)
        tree._root = node
        tree._split(node)
        assert len(node.children) >= 1
        assert all(isinstance(c, BBox) for c in node.children)


class TestRTreeNearest:
    def test_nearest_single(self) -> None:
        tree: RTree[str] = RTree()
        tree.insert(BBox(0, 0, 1, 1), "origin")
        results = tree.nearest((5, 5), k=1)
        assert len(results) == 1
        assert results[0][1] == "origin"
        assert math.isclose(results[0][0], math.sqrt(32))

    def test_nearest_k3(self) -> None:
        tree: RTree[str] = RTree()
        tree.insert(BBox(0, 0, 1, 1), "near")
        tree.insert(BBox(50, 50, 51, 51), "far")
        tree.insert(BBox(0, 0.5, 1, 1.5), "near2")
        tree.insert(BBox(30, 30, 31, 31), "mid")
        results = tree.nearest((0, 0), k=3)
        assert len(results) == 3


class TestRTreeLarge:
    def test_100_random_inserts_searchable(self) -> None:
        tree: RTree[int] = RTree(max_entries=6, min_entries=2)
        rng = random.Random(42)
        expected: dict[tuple[float, float, float, float], int] = {}
        for i in range(100):
            x = rng.uniform(0, 1000)
            y = rng.uniform(0, 1000)
            w = rng.uniform(1, 10)
            h = rng.uniform(1, 10)
            bbox = BBox(x, y, x + w, y + h)
            tree.insert(bbox, i)
            expected[(bbox.x1, bbox.y1, bbox.x2, bbox.y2)] = i
        assert tree.size == 100
        assert tree.depth >= 2
        full = tree.search(BBox(0, 0, 1010, 1010))
        assert len(full) == 100

    def test_depth_grows_with_data(self) -> None:
        tree: RTree[int] = RTree(max_entries=3, min_entries=1)
        assert tree.depth == 1
        for i in range(30):
            tree.insert(BBox(float(i), float(i), float(i + 0.5), float(i + 0.5)), i)
        assert tree.depth >= 3

    def test_total_nodes_after_splits(self) -> None:
        tree: RTree[int] = RTree(max_entries=3, min_entries=1)
        for i in range(50):
            tree.insert(BBox(float(i), float(i), float(i + 0.5), float(i + 0.5)), i)
        assert tree.total_nodes >= tree.depth


class TestRTreeEdgeCases:
    def test_zero_area_bbox_insert(self) -> None:
        tree: RTree[str] = RTree()
        tree.insert(BBox(5, 5, 5, 5), "point")
        assert tree.size == 1
        assert tree.search(BBox(5, 5, 5, 5)) == ["point"]

    def test_duplicate_bboxes(self) -> None:
        tree: RTree[str] = RTree()
        bbox = BBox(0, 0, 2, 2)
        tree.insert(bbox, "first")
        tree.insert(bbox, "second")
        results = tree.search(bbox)
        assert set(results) == {"first", "second"}

    def test_max_entries_equals_2x_min(self) -> None:
        tree: RTree[str] = RTree(max_entries=4, min_entries=2)
        assert tree.size == 0
        tree.insert(BBox(0, 0, 1, 1), "a")
        assert tree.size == 1

    def test_rejects_invalid_min_entries(self) -> None:
        with pytest.raises(ValueError):
            RTree(max_entries=3, min_entries=0)

    def test_rejects_invalid_ratio(self) -> None:
        with pytest.raises(ValueError):
            RTree(max_entries=3, min_entries=2)

    def test_search_bbox_on_empty(self) -> None:
        tree: RTree[str] = RTree()
        assert tree.search_bbox(BBox(0, 0, 10, 10)) == []

    def test_bool_after_insert_delete(self) -> None:
        tree: RTree[str] = RTree()
        assert not tree
        tree.insert(BBox(0, 0, 1, 1), "x")
        assert tree
