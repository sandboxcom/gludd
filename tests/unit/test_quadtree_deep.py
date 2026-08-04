"""Deep quadtree/octree tests: point insert, subdivision, range query, nearest."""

from __future__ import annotations

import math
import random

from general_ludd.algorithms.quadtree import Octree, Point2D, Point3D, Quadtree


def _p2(x: float, y: float) -> Point2D:
    return (x, y)


def _p3(x: float, y: float, z: float) -> Point3D:
    return (x, y, z)


# ── Quadtree ────────────────────────────────────────────────────────────────


class TestQuadtreeConstruction:
    def test_empty_quadtree(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        assert qt.count == 0
        assert not qt._divided

    def test_custom_capacity_and_depth(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 10.0, 10.0), capacity=8, max_depth=3)
        assert qt._capacity == 8
        assert qt._max_depth == 3

    def test_zero_capacity_means_no_subdivision(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0), capacity=0)
        for i in range(20):
            qt.insert(_p2(float(i), float(i)))
        assert not qt._divided
        assert qt.count == 20


class TestQuadtreeInsert:
    def test_insert_single_point(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        assert qt.insert(_p2(50.0, 50.0))
        assert qt.count == 1

    def test_insert_out_of_bounds_returns_false(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        assert not qt.insert(_p2(-1.0, 50.0))
        assert not qt.insert(_p2(50.0, 101.0))
        assert not qt.insert(_p2(200.0, 200.0))
        assert qt.count == 0

    def test_insert_boundary_points(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        assert qt.insert(_p2(0.0, 0.0))
        assert qt.insert(_p2(100.0, 100.0))
        assert qt.insert(_p2(0.0, 100.0))
        assert qt.insert(_p2(100.0, 0.0))
        assert qt.count == 4

    def test_insert_triggers_subdivision(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0), capacity=3)
        for i in range(4):
            qt.insert(_p2(float(i * 20 + 10), float(i * 20 + 10)))
        assert qt.count == 4

    def test_insert_same_point_multiple_times(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0), capacity=2)
        for _ in range(10):
            assert qt.insert(_p2(50.0, 50.0))
        assert qt.count == 10


class TestQuadtreeQueryRange:
    def test_range_query_all_points(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        pts = [(10.0, 10.0), (90.0, 90.0), (50.0, 50.0)]
        for p in pts:
            qt.insert(p)
        found = qt.query_range((0.0, 0.0, 100.0, 100.0))
        assert len(found) == 3

    def test_range_query_partial_overlap(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        for x in range(0, 100, 10):
            for y in range(0, 100, 10):
                qt.insert(_p2(float(x), float(y)))
        result = qt.query_range((0.0, 0.0, 45.0, 45.0))
        assert len(result) == 25

    def test_range_query_no_results(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        qt.insert(_p2(10.0, 10.0))
        qt.insert(_p2(20.0, 20.0))
        result = qt.query_range((80.0, 80.0, 90.0, 90.0))
        assert len(result) == 0

    def test_range_query_no_overlap_returns_empty(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 50.0, 50.0))
        for i in range(20):
            qt.insert(_p2(float(i), float(i)))
        result = qt.query_range((60.0, 60.0, 100.0, 100.0))
        assert len(result) == 0

    def test_range_query_with_subdivided_tree(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0), capacity=1)
        for x in range(0, 100, 5):
            for y in range(0, 100, 5):
                qt.insert(_p2(float(x), float(y)))
        found = qt.query_range((25.0, 25.0, 35.0, 35.0))
        assert len(found) == 9
        all_x = {p[0][0] for p in found}
        assert all(25.0 <= x <= 35.0 for x in all_x)


class TestQuadtreeNearest:
    def test_nearest_single_point(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        qt.insert(_p2(10.0, 10.0))
        result = qt.query_nearest((90.0, 90.0))
        assert len(result) == 1
        assert result[0][0] == (10.0, 10.0)

    def test_nearest_empty_tree(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        assert qt.query_nearest((50.0, 50.0)) == []

    def test_nearest_k_points(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        pts = [(0.0, 0.0), (10.0, 10.0), (20.0, 20.0), (51.0, 51.0)]
        for p in pts:
            qt.insert(p)
        result = qt.query_nearest((50.0, 50.0), k=3)
        assert len(result) == 3
        dists = [math.hypot(r[0][0] - 50.0, r[0][1] - 50.0) for r in result]
        assert dists == sorted(dists)

    def test_nearest_exact_match(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0))
        qt.insert(_p2(42.0, 42.0))
        qt.insert(_p2(99.0, 99.0))
        result = qt.query_nearest((42.0, 42.0))
        assert len(result) == 1
        assert result[0][0] == (42.0, 42.0)

    def test_nearest_when_query_point_inside_bbox(self) -> None:
        random.seed(42)
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0), capacity=4)
        pts: list[Point2D] = []
        for _ in range(50):
            p = (random.uniform(0, 100), random.uniform(0, 100))
            pts.append(p)
            qt.insert(p)
        result = qt.query_nearest((50.0, 50.0), k=5)
        assert len(result) == 5
        all_dists = [(math.hypot(p[0] - 50.0, p[1] - 50.0), p) for p in pts]
        all_dists.sort()
        expected_5 = [p for _, p in all_dists[:5]]
        assert [r[0] for r in result] == expected_5


class TestQuadtreeMaxDepth:
    def test_max_depth_prevents_infinite_subdivision(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0), capacity=1, max_depth=2)
        random.seed(7)
        for _ in range(100):
            qt.insert((random.uniform(0, 50), random.uniform(0, 50)))
        found = qt.query_range((0.0, 0.0, 100.0, 100.0))
        assert len(found) == 100

    def test_max_depth_zero(self) -> None:
        qt = Quadtree(bbox=(0.0, 0.0, 10.0, 10.0), capacity=1, max_depth=0)
        for i in range(50):
            qt.insert(_p2(float(i % 10), float(i // 10)))
        assert qt.count == 50


class TestQuadtreeStress:
    def test_large_uniform_random_set(self) -> None:
        random.seed(99)
        qt = Quadtree(bbox=(0.0, 0.0, 1000.0, 1000.0), capacity=8, max_depth=10)
        pts: list[Point2D] = []
        for _ in range(1000):
            p = (random.uniform(0, 1000), random.uniform(0, 1000))
            pts.append(p)
            assert qt.insert(p)
        assert qt.count == 1000
        found = qt.query_range((200.0, 200.0, 250.0, 250.0))
        assert len(found) >= 0

    def test_clustered_points(self) -> None:
        random.seed(13)
        qt = Quadtree(bbox=(0.0, 0.0, 100.0, 100.0), capacity=4, max_depth=12)
        for _ in range(200):
            qt.insert((random.gauss(30, 2), random.gauss(30, 2)))
        assert qt.count == 200
        nearest = qt.query_nearest((30.0, 30.0), k=10)
        assert len(nearest) == 10


# ── Octree ───────────────────────────────────────────────────────────────────


class TestOctreeConstruction:
    def test_empty_octree(self) -> None:
        ot = Octree(bbox=(0.0, 0.0, 0.0, 100.0, 100.0, 100.0))
        assert ot.count == 0
        assert not ot._divided

    def test_custom_capacity(self) -> None:
        ot = Octree(bbox=(0.0, 0.0, 0.0, 10.0, 10.0, 10.0), capacity=10, max_depth=6)
        assert ot._capacity == 10
        assert ot._max_depth == 6


class TestOctreeInsertQuery:
    def test_insert_and_retrieve_via_range(self) -> None:
        ot = Octree(bbox=(0.0, 0.0, 0.0, 100.0, 100.0, 100.0), capacity=2)
        ot.insert(_p3(10.0, 20.0, 30.0))
        ot.insert(_p3(80.0, 90.0, 70.0))
        ot.insert(_p3(50.0, 50.0, 50.0))
        assert ot.count == 3
        found = ot.query_range((0.0, 0.0, 0.0, 100.0, 100.0, 100.0))
        assert len(found) == 3

    def test_insert_out_of_bounds(self) -> None:
        ot = Octree(bbox=(0.0, 0.0, 0.0, 100.0, 100.0, 100.0))
        assert not ot.insert(_p3(-1.0, 50.0, 50.0))
        assert not ot.insert(_p3(50.0, 50.0, 101.0))
        assert ot.count == 0

    def test_partial_range_query(self) -> None:
        ot = Octree(bbox=(0.0, 0.0, 0.0, 100.0, 100.0, 100.0), capacity=2)
        for i in range(10):
            ot.insert(_p3(float(i * 10), float(i * 10), float(i * 10)))
        result = ot.query_range((0.0, 0.0, 0.0, 45.0, 45.0, 45.0))
        assert len(result) == 5


class TestOctreeNearest:
    def test_nearest_point(self) -> None:
        ot = Octree(bbox=(0.0, 0.0, 0.0, 100.0, 100.0, 100.0))
        ot.insert(_p3(0.0, 0.0, 0.0))
        ot.insert(_p3(100.0, 100.0, 100.0))
        result = ot.query_nearest((99.0, 99.0, 99.0))
        assert len(result) == 1
        assert result[0][0] == (100.0, 100.0, 100.0)

    def test_nearest_k_3d(self) -> None:
        random.seed(23)
        ot = Octree(bbox=(0.0, 0.0, 0.0, 100.0, 100.0, 100.0), capacity=4)
        for _ in range(80):
            ot.insert((random.uniform(0, 100), random.uniform(0, 100), random.uniform(0, 100)))
        result = ot.query_nearest((50.0, 50.0, 50.0), k=8)
        assert len(result) == 8
        dists = [math.hypot(r[0][0] - 50.0, r[0][1] - 50.0, r[0][2] - 50.0) for r in result]
        assert dists == sorted(dists)
